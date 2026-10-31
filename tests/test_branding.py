"""Offline branding contracts; synthetic images and short local media only."""
import copy
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.testclient import TestClient
from core import branding, branding_api, studio_store as store, studio_media as media
from core.studio_service import StudioService


def png(color=(255,0,0,128), size=(80,40), format='PNG'):
    data=io.BytesIO();Image.new('RGBA',size,color).save(data,format=format);return data.getvalue()

class Branding(unittest.TestCase):
    def setUp(self):
        root=Path('.runtime/branding-tests').resolve();root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.directory=self.root/'assets'
        self.config=copy.deepcopy(branding.DEFAULT)
    def logo(self):
        key=branding.upload(self.directory,png(),'logo.png')
        self.config['logo'].update(enabled=True,image=key)
        return key
    def test_png_webp_and_transparency(self):
        key=self.logo()
        with Image.open(branding.asset_path(self.directory,key)) as im:self.assertEqual(im.getpixel((0,0))[3],128)
        self.assertEqual(key,branding.upload(self.directory,png(),'again.png'))
        branding.upload(self.directory,png(format='WEBP'),'logo.webp')
    def test_invalid_images_and_ids(self):
        for content,name in [(b'<svg/>','a.svg'),(png(),'a.webp'),(b'bad','a.png'),(b'x'*(branding.MAX_UPLOAD_BYTES+1),'a.png'),(png(size=(4097,1)),'a.png')]:
            with self.assertRaises(ValueError):branding.upload(self.directory,content,name)
        with self.assertRaises(ValueError):branding.asset_path(self.directory,'../../file')
        data=io.BytesIO();im=Image.new('RGBA',(4,4));im.save(data,format='PNG',save_all=True,append_images=[Image.new('RGBA',(4,4),'red')],duration=10,loop=0)
        with self.assertRaises(ValueError):branding.upload(self.directory,data.getvalue(),'animated.png')
    def test_defaults_nested_validation_and_cache_keys(self):
        old=store.normalize({'segments':[]});self.assertFalse(old['studio_config']['branding']['logo']['enabled'])
        cfg=branding.normalize({'logo':{'opacity':.4}});self.assertEqual(cfg['logo']['width'],.12)
        for change in [{'logo':{'x':float('nan')}},{'text':{'content':'x'*201}},{'text':{'color':'red;movie=x'}},{'logo':{'enabled':True}},{'text':{'start':2,'end':1}}]:
            with self.assertRaises(ValueError):branding.validate(change,6)
        seg={'id':'x','start':0,'end':1,'vietnamese':'Chào'};old['segments']=[seg]
        before=store.audio_key(old,seg);timing=store.timing_key(old)
        old['studio_config']['branding']['text'].update(content='Thương hiệu',enabled=True)
        self.assertEqual(before,store.audio_key(old,seg));self.assertEqual(timing,store.timing_key(old))
    def test_layout_and_vietnamese_text(self):
        self.logo();self.config['text'].update(enabled=True,content='Thương hiệu Việt\nDòng thứ hai')
        for aspect in ('original','9:16','16:9','1:1'):
            w,h=branding.dimensions({'width':640,'height':360},aspect)
            layers=branding.layers(self.config,self.directory,w,h)
            self.assertEqual([l['kind'] for l in layers],['logo','text'])
            for layer in layers:
                self.assertLessEqual(layer['x']+layer['width'],1.00001);self.assertLessEqual(layer['y']+layer['height'],1.00001)
                self.assertTrue(branding.asset_path(self.directory,layer['image']).exists())
    def test_presets_detached_and_times_reset(self):
        self.logo();self.config['logo'].update(start=1,end=3)
        library=branding.BrandLibrary(self.root/'library')
        preset=library.save('Kênh A',self.config,self.directory)
        self.assertIsNone(preset['branding']['logo']['start'])
        other=self.root/'other';applied=library.load(preset['id'],other)
        library.preset_path(preset['id']).unlink()
        self.assertTrue(branding.asset_path(other,applied['branding']['logo']['image']).exists())
    def test_api_apply_revision_and_upload(self):
        video=self.root/'source.mp4';video.write_bytes(b'source')
        def safe(value):
            path=(self.root/value).resolve()
            if self.root not in path.parents:raise ValueError('outside')
            return str(path)
        host=SimpleNamespace(DATA_DIR=str(self.root),current_download_dir=str(self.root),_resolve_safe_path=safe)
        svc=StudioService(host);app=FastAPI();router=APIRouter(prefix='/api/studio')
        def call(fn,*args):
            try:return fn(*args)
            except store.Conflict as e:raise HTTPException(409,str(e))
            except (ValueError,FileNotFoundError) as e:raise HTTPException(400,str(e))
        branding_api.attach(router,svc,call);app.include_router(router)
        with TestClient(app) as client,patch.object(media,'probe',return_value=dict(width=640,height=360,duration=6,fps=25)):
            response=client.post('/api/studio/branding/logo?filepath=source.mp4&filename=logo.png',content=png());self.assertEqual(response.status_code,200,response.text)
            self.config['logo'].update(enabled=True,image=response.json()['image'])
            preset=client.post('/api/studio/branding/presets',json=dict(filepath='source.mp4',name='Kênh',branding=self.config)).json()
            path='/api/studio/branding/presets/'+preset['id']
            response=client.post(path+'/apply',json=dict(filepath='source.mp4',expected_revision=0));self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['project']['revision'],1)
            self.assertEqual(client.post(path+'/apply',json=dict(filepath='source.mp4',expected_revision=0)).status_code,409)
            self.assertEqual(client.patch(path,json={'name':'Tên mới'}).status_code,200)
            self.assertEqual(client.delete(path).status_code,200)
            image=self.config['logo']['image']
            self.assertEqual(client.get('/api/studio/branding/image/'+image+'?filepath=source.mp4').status_code,200)
            with patch.object(branding,'preview_frame',return_value=b'jpeg') as extract:
                response=client.get('/api/studio/branding/frame?filepath=source.mp4&at=1.5&aspect=9:16')
                self.assertEqual(response.status_code,200);self.assertEqual(response.content,b'jpeg')
                self.assertEqual(response.headers['content-type'],'image/jpeg')
                self.assertEqual(response.headers['cache-control'],'no-store')
                extract.assert_called_once_with(str(video),'9:16',1.5)
            payload=png();large=payload+b'\0'*(6*1024*1024-len(payload))
            self.assertEqual(client.post('/api/studio/branding/logo?filepath=source.mp4&filename=large.png',content=large).status_code,200)
            over=client.post('/api/studio/branding/logo?filepath=source.mp4&filename=over.png',content=b'x'*(branding.MAX_UPLOAD_BYTES+1))
            self.assertEqual(over.status_code,400);self.assertIn('20 MB',over.json()['detail'])
            self.assertIn(client.get('/api/studio/branding/frame?filepath=source.mp4&at=-1').status_code,(400,422))
            self.assertEqual(client.get('/api/studio/branding/frame?filepath=../outside.mp4').status_code,400)

    def test_jpeg_upload_is_normalized_and_oriented(self):
        payload=io.BytesIO();image=Image.new('RGB',(80,40),'red');exif=Image.Exif();exif[274]=6
        image.save(payload,format='JPEG',exif=exif)
        for filename in ('logo.jpg','logo.jpeg','LOGO.JPG'):
            key=branding.upload(self.directory,payload.getvalue(),filename)
            with Image.open(branding.asset_path(self.directory,key)) as normalized:
                self.assertEqual(normalized.size,(40,80));self.assertEqual(normalized.mode,'RGBA')
                self.assertEqual(normalized.getpixel((0,0))[3],255);self.assertFalse(normalized.getexif())
        with self.assertRaises(ValueError):branding.upload(self.directory,payload.getvalue(),'fake.png')

    def test_upload_size_limits_include_20mb_boundary(self):
        # Padding a small valid PNG exercises byte limits without decoding a huge image.
        payload=png()
        for size in (6*1024*1024,branding.MAX_UPLOAD_BYTES):
            key=branding.upload(self.directory,payload+b'\0'*(size-len(payload)),'large.png')
            self.assertTrue(branding.asset_path(self.directory,key).exists())
        with self.assertRaisesRegex(ValueError,'20 MB'):
            branding.upload(self.directory,payload+b'\0'*(branding.MAX_UPLOAD_BYTES+1-len(payload)),'over.png')
