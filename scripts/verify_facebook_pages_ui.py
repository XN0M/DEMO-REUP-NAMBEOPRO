"""Exercise the real interface with fake Pages; never contact Facebook."""
import json
import socket
import threading
import time
from urllib.parse import urlsplit

import uvicorn
from playwright.sync_api import sync_playwright
from server import app


def main():
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(128)
    base = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    service = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='error'))
    thread = threading.Thread(target=lambda: service.run(sockets=[sock]), daemon=True); thread.start()
    state = {'pages': [], 'posts': [], 'check_error': False}
    def route_api(route):
        req = route.request; path = urlsplit(req.url).path
        body = req.post_data_json if req.post_data else {}
        result = {}
        if path == '/api/fb/pages':
            if req.method == 'POST':
                page = {'page_id': body['page_id'], 'page_name': body['page_name'], 'token_set': True}
                state['pages'] = [p for p in state['pages'] if p['page_id'] != page['page_id']] + [page]
                result = {'page': page}
            else: result = {'pages': state['pages']}
        elif path.startswith('/api/fb/pages/'):
            identity = path.split('/')[4]
            if req.method == 'DELETE':
                state['pages'] = [p for p in state['pages'] if p['page_id'] != identity]
                result = {'deleted': True}
            elif path.endswith('/post'):
                state['posts'].append((identity, body))
                result = {'success': True, 'page_id': identity, 'video_id': 'test-video'}
            else:
                time.sleep(.3)
                if state['check_error']:
                    route.fulfill(status=403, content_type='application/json', body=json.dumps({'detail': 'Token test invalid'}))
                    return
                result = {'ok': True, 'page_id': identity, 'page_name': 'Checked'}
        elif path == '/api/channels': result = {'channels': []}
        elif path == '/api/channels/videos': result = {'videos': []}
        elif path == '/api/history': result = {'videos': []}
        elif path == '/api/folders': result = {'folders': []}
        elif path == '/api/config': result = {'download_dir': 'Test videos'}
        elif 'voices' in path: result = {'voices': []}
        route.fulfill(status=200, content_type='application/json', body=json.dumps(result))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []; page.on('pageerror', lambda error: errors.append(error.stack))
            page.route('**/api/**', route_api)
            page.goto(base)
            page.locator('.nb-nav[data-page="settings"]').click()
            for identity, name in [('101', 'Page A'), ('202', 'Page B <safe>')]:
                page.locator('#fbMultiPageId').fill(identity)
                page.locator('#fbMultiPageName').fill(name)
                page.locator('#fbMultiPageToken').fill('synthetic-token')
                page.get_by_role('button', name='Lưu Page', exact=True).click()
                page.wait_for_function("id => document.querySelector('#facebookPagesPanel').textContent.includes(id)", arg=identity)
            assert page.locator('#fbMultiPageToken').input_value() == ''
            check = page.get_by_role('button', name='Kiểm tra kết nối', exact=True).first
            check.click()
            page.wait_for_function("document.querySelector('[data-page-check=\"101\"]').textContent.includes('Đã kết nối')")
            assert check.is_enabled()
            assert 'Kết nối thành công' in page.locator('#toast').inner_text()
            state['check_error'] = True
            check.click()
            page.wait_for_function("document.querySelector('[data-page-check=\"101\"]').textContent.includes('Token test invalid')")
            assert check.is_enabled()
            assert 'Kiểm tra thất bại' in page.locator('#toast').inner_text()
            page.evaluate("openFbPostModal('sample.mp4', 'Caption')")
            page.locator('#fbPostModal').wait_for(state='visible')
            page.locator('#btnSubmitFbPost').click()
            assert state['posts'] == []
            page.locator('#fbPostPage').select_option('202')
            page.locator('#btnSubmitFbPost').click()
            page.wait_for_function("document.querySelector('#fbPostStatus').textContent.includes('test-video')")
            assert state['posts'] == [('202', {'mp4_path': 'sample.mp4', 'caption': 'Caption'})]
            page.evaluate('closeFbPostModal()')
            page.reload(); page.locator('.nb-nav[data-page="settings"]').click()
            page.get_by_text('Page B <safe> · 202', exact=True).wait_for()
            page.on('dialog', lambda dialog: dialog.accept())
            page.get_by_role('button', name='Xóa khỏi tool').last.click()
            page.wait_for_function("!document.querySelector('#facebookPagesPanel').textContent.includes('202')")
            page.screenshot(path='facebook-pages-ui-test.png', full_page=True)
            assert not errors, errors
            browser.close()
        print('PASS: add Pages, visible connection success/failure, correct destination, reload/delete, no script errors')
    finally:
        service.should_exit = True; thread.join(5)


if __name__ == '__main__':
    main()
