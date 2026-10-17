"""Opt-in real GPU integration: preset, uploaded clone, Studio export, quick and batch."""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def main():
    from fastapi.testclient import TestClient
    import server
    from core import studio_media as media, studio_store as store
    from core.tts_runtime import library, runtime
    output=ROOT/'.runtime'/'vieneu'
    output.mkdir(parents=True,exist_ok=True)
    # Freeze the downloaded snapshot identity in the same format as the installer.
    verified=output/'verified.json'
    metrics=json.loads(verified.read_text())
    refs={str(p.relative_to(output/'models')):p.read_text().strip() for p in (output/'models').glob('hub/models--*/refs/*') if p.is_file()}
    metrics['model_fingerprint']=hashlib.sha256(json.dumps(refs,sort_keys=True).encode()).hexdigest()
    verified.write_text(json.dumps(metrics),encoding='utf-8')
    client=TestClient(server.app)
    custom=None
    old_root=server.current_download_dir
    try:
        catalog=server.dubber.get_available_voices()
        assert any(v['id']=='vieneu:preset:Mai Anh' for v in catalog),catalog
        response=client.post('/api/tts/voices/custom?name=QA%20VieNeu&filename=sample.wav&consent=true',content=(output/'setup-sample.wav').read_bytes())
        assert response.status_code==200,response.text
        custom=response.json()['id'];voice='vieneu:custom:'+custom
        started=time.monotonic()
        preview=client.post('/api/dub/preview',json={'voice':'vieneu:preset:Mai Anh','text':'Xin chào các bạn.'})
        assert preview.status_code==200,preview.text
        (output/'demo-preset.wav').write_bytes(preview.content)
        preset_seconds=round(time.monotonic()-started,2)
        started=time.monotonic()
        preview=client.post('/api/dub/preview',json={'voice':voice,'text':'Cảm ơn các bạn.'})
        assert preview.status_code==200,preview.text
        (output/'demo-cloned.wav').write_bytes(preview.content)
        clone_seconds=round(time.monotonic()-started,2)
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp',prefix='vieneu-real-') as temporary:
            root=Path(temporary);server.current_download_dir=str(root)
            server.studio_service.database=str(root/'jobs.sqlite3');server.studio_service.ready=False
            video=root/'first.mp4'
            media.run(['-f','lavfi','-i','color=c=blue:s=640x360:d=12','-c:v','libx264','-pix_fmt','yuv420p',video])
            data=store.normalize({'detected_language':'vi','segments':[
                {'id':'a','start':.2,'end':5.5,'text':'Xin chào các bạn.','speaker':'A'},
                {'id':'b','start':6,'end':11.5,'text':'Cảm ơn các bạn.','speaker':'B'}]})
            data['studio_config']['voice']={'provider':'vieneu','model':'v3turbo','voice':'vieneu:preset:Mai Anh'}
            data['studio_config']['speakers']={'B':{'provider':'vieneu','model':'v3turbo','voice':voice}}
            data['studio_config']['render'].update(dubbed=True,subtitles=False)
            data=store.save(video,data,0,12)
            def wait(job):
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    value=server.studio_service.get(job['id'])
                    if value['status'] in ('completed','partial','error','cancelled'):
                        assert value['status']=='completed',value
                        return value
                    time.sleep(.15)
                raise AssertionError('Job timed out')
            wait(server.studio_service.launch('tts','first.mp4',data['revision']))
            entries=server.studio_service.audio_status(video,data)
            assert all(v['status']=='ready' for v in entries.values()),entries
            exported=wait(server.studio_service.launch('export','first.mp4',data['revision']))
            artifact=store.paths(video)[2]/exported['artifact']
            assert media.has_audio(artifact)
            shutil.copy2(artifact,output/'demo-studio.mp4')
            quick=asyncio.run(server.dubber.execute_dubbing(str(video),data,mode='multi',single_voice='vieneu:preset:Mai Anh',character_voices={'B':voice},task_id='real-vieneu'))
            assert media.has_audio(quick['dubbed_video'])
            for name in ('second','third'):
                target=root/(name+'.mp4');shutil.copy2(video,target)
                store.save(target,data,0,12)
                assert media.has_audio(server.studio_service.batch(str(target)))
        result=dict(catalog_voices=len(catalog),preset_seconds_including_load=preset_seconds,clone_seconds=clone_seconds,
                    studio_export=True,quick_export=True,batch_videos=2,worker=runtime.status())
        (output/'integration.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=True))
    finally:
        server.current_download_dir=old_root
        runtime.stop()
        if custom:
            library.delete(custom)


if __name__=='__main__':
    main()
