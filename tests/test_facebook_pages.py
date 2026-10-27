import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import server
from core import facebook_pages as pages, monitor, fb_poster


class FacebookPages(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / 'video.mp4'
        self.file.write_bytes(b'fake video')
        for owner, attr, value in [(monitor, 'FB_CONFIG_FILE', self.root / 'fb_config.json'),
                                   (server, 'current_download_dir', str(self.root))]:
            p = patch.object(owner, attr, value)
            p.start(); self.addCleanup(p.stop)
        self.client = TestClient(server.app, base_url='http://127.0.0.1:8000',
                                 client=('127.0.0.1', 1234), headers={'Origin': 'http://127.0.0.1:8000'})

    def add(self, identity, token=None):
        return self.client.post('/api/fb/pages', json={'page_id': identity, 'page_name': 'Page '+identity,
                                                     'page_access_token': token})

    def test_legacy_bom_preserved_and_imported_once_without_token_exposure(self):
        raw = json.dumps({'page_id': '1', 'page_name': 'Old', 'page_access_token': 'synthetic-old'}).encode('utf-8-sig')
        monitor.FB_CONFIG_FILE.write_bytes(raw)
        self.assertEqual(self.client.get('/api/fb/pages').json()['pages'][0]['page_id'], '1')
        self.assertEqual(self.add('2', 'synthetic-new').status_code, 200)
        response = self.client.get('/api/fb/pages')
        self.assertEqual(len(response.json()['pages']), 2)
        self.assertNotIn('synthetic', response.text)
        self.assertEqual(monitor.FB_CONFIG_FILE.read_bytes(), raw)
        self.client.delete('/api/fb/pages/1')
        self.assertEqual([p['page_id'] for p in pages.list_pages()], ['2'])

    def test_blank_token_preserves_only_same_page(self):
        self.add('1', 'synthetic-one')
        self.assertEqual(self.add('1', '').status_code, 200)
        self.assertEqual(pages.get_page('1')['page_access_token'], 'synthetic-one')
        self.assertEqual(self.add('2', '').status_code, 422)
        self.assertEqual(len(pages.list_pages()), 1)

    def test_selected_page_routes_correct_token_and_deleted_page_never_posts(self):
        self.add('1', 'synthetic-one'); self.add('2', 'synthetic-two')
        with patch.object(fb_poster, '_upload_small', return_value='123') as upload:
            result = self.client.post('/api/fb/pages/2/post', json={'mp4_path': 'video.mp4', 'caption': 'hello'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(upload.call_args.args[2:], ('2', 'synthetic-two'))
            self.assertEqual(result.json()['page_id'], '2')
            self.client.delete('/api/fb/pages/2')
            self.assertEqual(self.client.post('/api/fb/pages/2/post', json={'mp4_path': 'video.mp4'}).status_code, 404)
            self.assertEqual(upload.call_count, 1)

    def test_cross_origin_is_rejected(self):
        self.assertEqual(self.client.get('/api/fb/pages', headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/fb/pages', headers={'Origin': 'https://evil.example'},
                                         json={'page_id': '1', 'page_access_token': 'synthetic'}).status_code, 403)

    def test_no_default_destination_and_invalid_ids(self):
        self.assertEqual(self.add('../wrong', 'synthetic').status_code, 422)
        with patch.object(fb_poster, '_upload_small') as upload:
            self.assertEqual(self.client.post('/api/fb/pages/missing/post', json={'mp4_path': 'video.mp4'}).status_code, 404)
            upload.assert_not_called()

    def test_snapshot_is_independent_of_later_edits(self):
        self.add('1', 'synthetic-old')
        snapshot = pages.get_page('1')
        self.add('1', 'synthetic-new')
        self.assertEqual(snapshot['page_access_token'], 'synthetic-old')


if __name__ == '__main__':
    unittest.main()
