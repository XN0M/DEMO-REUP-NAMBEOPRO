"""Headless UI regression using an isolated media directory and mock synthesis."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    from core import studio_media as media, studio_store as store
    from playwright.sync_api import sync_playwright, expect
    output = ROOT / '.runtime' / 'vieneu'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='vieneu-ui-') as temporary:
        root = Path(temporary)
        video = root / 'sample.mp4'
        sample = root / 'reference.wav'
        media.run(['-f','lavfi','-i','color=c=blue:s=640x360:d=6','-c:v','libx264','-pix_fmt','yuv420p',video])
        media.run(['-f','lavfi','-i','sine=frequency=440:duration=3.2','-ar','48000','-ac','1',sample])
        store.atomic_json(store.paths(video)[0], {'detected_language':'vi','segments':[
            {'id':'a','start':.2,'end':2,'text':'Xin chào các bạn','speaker':'A'},
            {'id':'b','start':3,'end':5,'text':'Cảm ơn các bạn','speaker':'A'}]})
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0));port=sock.getsockname()[1]
        child = '''
import sys, io, wave
from pathlib import Path
import server, uvicorn
from core.tts_runtime import library, runtime
root=Path(sys.argv[1]);server.current_download_dir=str(root)
library.root=root/'voices';server.studio_service.database=str(root/'studio.sqlite3')
runtime.catalog=lambda **kwargs:[dict(id='vieneu:preset:Mai Anh',name='Mai Anh',provider='vieneu',model='v3turbo',kind='preset')]
async def synthesize(*args, **kwargs):
    stream=io.BytesIO()
    with wave.open(stream,'wb') as a:
        a.setparams((1,2,48000,0,'NONE','not compressed'));a.writeframes(b'\\x00\\x01'*19200)
    return stream.getvalue()
server.dubber.synthesize_vieneu=synthesize
uvicorn.run(server.app,host='127.0.0.1',port=int(sys.argv[2]),log_level='warning')
'''
        with (output / 'ui-server.log').open('w', encoding='utf-8') as log:
            process=subprocess.Popen([sys.executable,'-c',child,str(root),str(port)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            base=f'http://127.0.0.1:{port}'
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError('UI server exited; see ui-server.log')
                try:
                    urllib.request.urlopen(base+'/api/tts/voices/custom',timeout=1);break
                except OSError:time.sleep(.2)
            else:raise RuntimeError('UI server not ready')
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000})
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(base,wait_until='domcontentloaded')
                expect(page.locator('#batchVoiceSelect option[value="vieneu:preset:Mai Anh"]')).to_have_count(1)
                page.evaluate("window.openSubtitleStudio('sample.mp4','voice')")
                page.locator('.ns').get_by_role('button',name='Giọng cá nhân / Cài VieNeu').click()
                modal=page.get_by_role('dialog',name='Giọng cá nhân và VieNeu')
                expect(modal).to_be_visible()
                modal.get_by_label('Tên giọng mới').fill('Giọng kiểm thử')
                modal.get_by_label('Audio mẫu dài 3–8 giây').set_input_files(str(sample))
                modal.get_by_text('Tôi có quyền sử dụng giọng mẫu này',exact=False).click()
                modal.get_by_role('button',name='Lưu giọng mẫu',exact=True).click()
                expect(modal.locator('[data-list] input')).to_have_value('Giọng kiểm thử')
                default_select=modal.locator('[data-default-voice]')
                custom_id=default_select.locator('option').evaluate_all("opts=>opts.find(o=>o.value.startsWith('vieneu:custom:')).value")
                default_select.select_option(custom_id)
                modal.get_by_role('button',name='Mặc định cho nhanh / batch',exact=True).click()
                expect(page.locator('#batchVoiceSelect')).to_have_value(custom_id)
                expect(page.locator('#dubVoiceSelect')).to_have_value(custom_id)
                page.screenshot(path=str(output/'ui-voices.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert modal.evaluate('el=>el.scrollWidth<=el.clientWidth+1'),'Voice manager overflows on mobile'
                page.screenshot(path=str(output/'ui-voices-mobile.png'),full_page=True)
                modal.get_by_role('button',name='Đóng',exact=True).click()
                page.set_viewport_size({'width':1440,'height':1000})
                voice_select=page.locator('.ns select[aria-label="Giọng mặc định"]')
                choice=voice_select.locator('option').evaluate_all("opts => opts.find(o=>o.value.includes('vieneu:custom:')).value")
                voice_select.select_option(choice)
                page.locator('.ns').get_by_role('button',name='Tạo giọng cho 2 câu cần cập nhật',exact=True).click()
                expect(page.locator('.ns .job')).to_contain_text('Hoàn tất',timeout=20000)
                page.screenshot(path=str(output/'ui-studio.png'),full_page=True)
                page.locator('.ns').get_by_role('button',name='Tiếp tục: Xuất video').click()
                expect(page.locator('.ns').get_by_label('Tốc độ giọng tối đa')).to_have_value('1.65')
                expect(page.locator('.ns').get_by_label('Chồng tiếng tối đa (giây)')).to_have_value('1')
                expect(page.locator('.ns').get_by_label('Tự ẩn phụ đề sau (giây)')).to_have_value('12')
                assert page.locator('#batchVoiceSelect option').evaluate_all("opts=>opts.some(o=>o.value.startsWith('vieneu:custom:'))")
                assert page.locator('#dubVoiceSelect option').evaluate_all("opts=>opts.some(o=>o.value.startsWith('vieneu:custom:'))")
                assert not errors, errors
                browser.close()
                print(json.dumps({'upload':True,'studio_tts':True,'quick_and_batch_catalog':True,'page_errors':errors}))
        finally:
            process.terminate()
            try:process.wait(10)
            except subprocess.TimeoutExpired:process.kill();process.wait(5)


if __name__=='__main__':
    main()
