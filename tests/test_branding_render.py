"""Real FFmpeg coverage for branding geometry, time mapping and audio preservation."""
import copy
import io
import tempfile
import unittest
import wave
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from core import branding, studio_media as media, studio_store as store

class BrandingRender(unittest.TestCase):
    def setUp(self):
        parent=Path('.runtime/branding-render-tests').resolve();parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.video=self.root/'nguồn video.mp4'
        media.run(['-f','lavfi','-i','color=c=blue:s=320x240:d=3:r=20','-c:v','libx264','-pix_fmt','yuv420p',self.video])
        self.assets=store.paths(self.video)[2]/'branding';self.work=self.root/'work';self.work.mkdir()
        image=io.BytesIO();Image.new('RGBA',(80,40),(255,0,0,255)).save(image,format='PNG')
        self.key=branding.upload(self.assets,image.getvalue(),'logo.png')
        self.data=store.normalize({'segments':[dict(id='cue',start=0,end=3,vietnamese='Thương hiệu Việt')],'detected_language':'vi'})
        self.data['studio_config']['render']['subtitles']=False
        self.data['studio_config']['branding']['logo'].update(enabled=True,image=self.key,x=.1,y=.1,width=.3,opacity=.5)
    def frame(self,path,at):
        cap=cv2.VideoCapture(str(path));cap.set(cv2.CAP_PROP_POS_MSEC,at*1000);ok,frame=cap.read();cap.release();self.assertTrue(ok);return frame
    def render(self,name='output.mp4',**kwargs):
        output=self.root/name;media.render(str(self.video),self.data,output,self.work,lambda:False,**kwargs);return output
    def test_aspect_matrix_matches_preview_geometry(self):
        for aspect in ('original','9:16','16:9','1:1'):
            self.data['studio_config']['render']['aspect']=aspect
            w,h=branding.dimensions(media.probe(self.video),aspect)
            layer=branding.layers(self.data['studio_config']['branding'],self.assets,w,h)[0]
            output=self.render(aspect.replace(':','-')+'.mp4');frame=self.frame(output,.3)
            self.assertEqual(frame.shape[:2],(h,w))
            x=int((layer['x']+layer['width']/2)*w);y=int((layer['y']+layer['height']/2)*h)
            b,g,r=map(int,frame[y,x]);self.assertLess(g,30);self.assertTrue(90<r<170,(b,g,r));self.assertTrue(90<b<170,(b,g,r))
    def test_timing_trim_speed_and_text(self):
        cfg=self.data['studio_config'];cfg['render'].update(start=.5,end=2.5,speed=2)
        cfg['branding']['logo'].update(start=1,end=2)
        cfg['branding']['text'].update(enabled=True,content='Thương hiệu Việt\nDòng thứ hai',size=80,y=.7)
        output=self.render();self.assertAlmostEqual(media.probe(output)['duration'],1,delta=.1)
        for at,expected in [(.05,False),(.45,True),(.85,False)]:
            frame=self.frame(output,at);r=int(frame[40,50,2]);self.assertEqual(r>70,expected)
        frame=self.frame(output,.45);self.assertGreater(np.count_nonzero(np.min(frame[165:230],axis=2)>170),5)
        self.render('preview.mp4',preview_at=1.5)
    def test_missing_logo_fails_and_snapshot_remains_detached(self):
        snapshot=copy.deepcopy(self.data);self.data['studio_config']['branding']['logo']['enabled']=False
        output=self.render();self.assertLess(int(self.frame(output,.2)[40,50,2]),30)
        self.data=snapshot;branding.asset_path(self.assets,self.key).unlink()
        with self.assertRaises(FileNotFoundError):self.render('missing.mp4')
    def test_audio_tail_and_subtitle_layer(self):
        cfg=self.data['studio_config'];cfg['render'].update(dubbed=True,subtitles=True)
        cfg['branding']['logo'].update(x=0,y=0,width=1,opacity=1)
        voice=self.root/'voice.wav'
        with wave.open(str(voice),'wb') as wav:
            wav.setparams((1,2,24000,0,'NONE','not compressed'));wav.writeframes(np.zeros(4*24000,dtype='<i2').tobytes())
        output=self.render(voice_track=voice,entries={'cue':dict(status='ready',play_start=0,play_end=4,aligned_duration=4)})
        self.assertTrue(media.has_audio(output));self.assertGreater(media.probe(output)['duration'],3.8)
        frame=self.frame(output,3.5);self.assertGreater(int(frame[40,50,2]),200)
        frame=self.frame(output,.5);self.assertGreater(np.count_nonzero(np.min(frame,axis=2)>180),10)

    def test_logo_is_above_source_mask(self):
        self.data['studio_config']['style'].update(mask=True,mask_blur=False,mask_x=0,mask_y=0,mask_width=.8,mask_height=.6)
        frame=self.frame(self.render(),.2)
        b,g,r=map(int,frame[40,50]);self.assertLess(b,30);self.assertGreater(r,90)
    def test_branding_preserves_original_audio(self):
        audible=self.root/'audible.mp4'
        media.run(['-i',self.video,'-f','lavfi','-i','sine=frequency=440:duration=3','-map','0:v','-map','1:a','-c:v','copy','-c:a','aac',audible])
        old=self.video;self.video=audible
        destination=store.paths(audible)[2]/'branding'
        destination.mkdir(parents=True)
        import shutil
        shutil.copyfile(branding.asset_path(self.assets,self.key),branding.asset_path(destination,self.key))
        output=self.render();self.assertTrue(media.has_audio(output))
        self.assertGreater(int(self.frame(output,.3)[40,50,2]),90)
