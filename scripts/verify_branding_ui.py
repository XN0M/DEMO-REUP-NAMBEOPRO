"""Browser acceptance using an isolated local Studio and synthetic media."""
import io
import socket
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright, expect
from PIL import Image
from core import studio_api, studio_store as store, studio_media as media


def main():
    parent=Path('.runtime/branding-ui-tests').resolve();parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as tmp:
        root=Path(tmp);video=root/'sample.mp4'
        media.run(['-f','lavfi','-i','color=c=blue:s=640x360:d=4','-c:v','libx264','-pix_fmt','yuv420p',video])
        data=store.normalize({'segments':[dict(id='cue',start=0,end=4,vietnamese='Phụ đề kiểm tra',text='Phụ đề kiểm tra')],'detected_language':'vi'})
        store.atomic_json(store.paths(video)[0],data)
        def safe(value):
            p=(root/value).resolve()
            if root not in p.parents:raise HTTPException(403,'outside')
            return str(p)
        app=FastAPI();app.mount('/static',StaticFiles(directory='web'),name='static')
        @app.get('/')
        def home():return HTMLResponse('<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/studio.css"><link rel="stylesheet" href="/static/app.css"><script defer src="/static/studio-branding.js"></script><script defer src="/static/studio.js"></script></head><body style="margin:0;background:#101522"><button onclick="openSubtitleStudio(\'sample.mp4\',\'branding\')">Open</button></body></html>')
        @app.get('/api/dub/voices')
        def voices():return {'voices':[{'id':'vi-VN-HoaiMyNeural','provider':'edge','name':'Hoài My'}]}
        @app.get('/api/history/stream/{path:path}')
        def stream(path:str):return FileResponse(safe(path))
        host=SimpleNamespace(app=app,DATA_DIR=str(root),current_download_dir=str(root),_resolve_safe_path=safe,HTTPException=HTTPException,
            _download_in_progress=lambda p:False,_reserve_source_job=lambda p:True,_release_source_job=lambda p:None)
        studio_api.attach(host,database=root/'jobs.db')
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
        url='http://127.0.0.1:'+str(sock.getsockname()[1])
        server=uvicorn.Server(uvicorn.Config(app,lifespan='off',log_level='error'))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1500,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(url);page.get_by_role('button',name='Open',exact=True).click()
                expect(page.get_by_role('tab')).to_have_count(5)
                expect(page.get_by_role('tab',name='3. Thương hiệu')).to_have_attribute('aria-selected','true')
                image=io.BytesIO();Image.new('RGBA',(120,60),(255,0,0,200)).save(image,format='PNG')
                page.get_by_label('Tải logo PNG hoặc WebP').set_input_files({'name':'logo.png','mimeType':'image/png','buffer':image.getvalue()})
                expect(page.locator('[data-brand-kind=logo]')).to_be_visible()
                page.get_by_label('Bật Watermark chữ',exact=True).check()
                page.get_by_label('Nội dung watermark',exact=True).fill('Thương hiệu Việt\nNambeo')
                page.get_by_label('Nội dung watermark',exact=True).press('Tab')
                expect(page.locator('[data-brand-kind=text]')).to_be_visible()
                page.get_by_role('button',name='Lưu',exact=True).click();expect(page.locator('[data-save-status]')).to_have_attribute('data-dirty','false')
                page.get_by_role('button',name='Đặt logo trên ảnh',exact=True).click()
                still=page.get_by_role('dialog',name='Ảnh preview đặt logo',exact=True)
                expect(still.locator('.brandingStillBackground')).to_be_visible()
                page.wait_for_function("document.querySelector('.brandingStillBackground').naturalWidth>0")
                assert still.locator('video').count()==0
                assert page.locator('.previewViewport video').first.evaluate('e=>e.paused')
                still.get_by_label('Mốc lấy ảnh (giây)',exact=True).fill('1.5');still.get_by_role('button',name='Lấy ảnh',exact=True).click()
                expect(still.locator('.brandingStillBackground')).to_be_visible()
                before=store.load(video)['studio_config']['branding']['logo']['x']
                logo=still.locator('[data-brand-kind=logo]');rect=logo.bounding_box()
                expect(page.locator('[data-brand-status]')).to_have_text('')
                rect=logo.bounding_box()
                page.mouse.move(rect['x']+rect['width']/2,rect['y']+rect['height']/2);page.mouse.down();page.mouse.move(rect['x']+rect['width']/2-70,rect['y']+rect['height']/2+20,steps=5);page.mouse.up()
                still.get_by_role('button',name='Lưu vị trí',exact=True).click();expect(page.locator('[data-save-status]')).to_have_attribute('data-dirty','false')
                expect(page.locator('[data-save-status]')).to_contain_text('Đã lưu')
                page.screenshot(path=str(parent/'branding-debug.png'),full_page=True)
                after=store.load(video)['studio_config']['branding']['logo']['x'];assert after<before,(before,after)
                expect(page.locator('[data-brand-status]')).to_have_text('')
                handle=still.locator('[data-brand-kind=logo] .brandingResize').bounding_box()
                width_before=store.load(video)['studio_config']['branding']['logo']['width']
                page.mouse.move(handle['x']+handle['width']/2,handle['y']+handle['height']/2);page.mouse.down();page.mouse.move(handle['x']+handle['width']/2+20,handle['y']+handle['height']/2,steps=4);page.mouse.up()
                still.get_by_role('button',name='Lưu vị trí',exact=True).click();expect(page.locator('[data-save-status]')).to_have_attribute('data-dirty','false')
                assert store.load(video)['studio_config']['branding']['logo']['width']>width_before
                still.get_by_label('Tỷ lệ khung hình khi xuất',exact=True).select_option('9:16')
                expect(still.locator('.brandingStillBackground')).to_be_visible()
                page.wait_for_function("Math.abs(document.querySelector('.brandingStillBackground').naturalWidth/document.querySelector('.brandingStillBackground').naturalHeight-9/16)<.01")
                expect(page.locator('[data-brand-status]')).to_have_text('')
                still.screenshot(path=str(parent/'branding-still-preview.png'))
                still.get_by_label('Tỷ lệ khung hình khi xuất',exact=True).select_option('original')
                expect(page.locator('[data-brand-status]')).to_have_text('')
                still.get_by_role('button',name='Lưu vị trí',exact=True).click();expect(page.locator('[data-save-status]')).to_have_attribute('data-dirty','false')
                still.get_by_role('button',name='Đóng',exact=True).click()
                page.get_by_role('button',name='Hoàn tác',exact=True).click();page.get_by_role('button',name='Lưu',exact=True).click();expect(page.locator('[data-save-status]')).to_have_attribute('data-dirty','false')
                page.get_by_label('Tên bộ thương hiệu',exact=True).fill('Kênh kiểm tra')
                page.get_by_role('button',name='Lưu bộ mới',exact=True).click()
                expect(page.get_by_label('Bộ thương hiệu đã lưu')).to_contain_text('Kênh kiểm tra')
                page.get_by_label('Bộ thương hiệu đã lưu').select_option(label='Kênh kiểm tra')
                page.get_by_role('button',name='Áp dụng bộ',exact=True).click()
                page.get_by_role('tab',name='3. Thương hiệu').focus();page.keyboard.press('End');expect(page.get_by_role('tab',name='5. Xuất video')).to_have_attribute('aria-selected','true')
                page.keyboard.press('Home');expect(page.get_by_role('tab',name='1. Bản thoại')).to_have_attribute('aria-selected','true')
                page.get_by_role('tab',name='3. Thương hiệu').click()
                page.locator('[data-brand-status]').filter(has_text='Đang cập nhật').wait_for(state='hidden') if page.locator('[data-brand-status]').filter(has_text='Đang cập nhật').count() else None
                page.screenshot(path=str(parent/'branding-desktop.png'),full_page=True)
                page.get_by_role('button',name='Quay lại thư viện').click();page.get_by_role('button',name='Open',exact=True).click()
                expect(page.get_by_label('Nội dung watermark',exact=True)).to_have_value('Thương hiệu Việt\nNambeo')
                page.set_viewport_size({'width':390,'height':844})
                page.get_by_role('button',name='Thuộc tính',exact=True).click()
                expect(page.get_by_label('Nội dung watermark',exact=True)).to_be_visible()
                page.screenshot(path=str(parent/'branding-mobile.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile horizontal overflow'
                page.get_by_role('button',name='Đặt logo trên ảnh',exact=True).click()
                still=page.get_by_role('dialog',name='Ảnh preview đặt logo',exact=True)
                expect(still.locator('.brandingStillBackground')).to_be_visible()
                assert still.locator('video').count()==0
                mobile_logo=still.locator('[data-brand-kind=logo]');mobile_logo.scroll_into_view_if_needed();rect=mobile_logo.bounding_box()
                assert page.evaluate('(r)=>!!document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest(".brandingStillDialog")',rect)
                page.mouse.move(rect['x']+rect['width']/2,rect['y']+rect['height']/2);page.mouse.down();page.mouse.move(rect['x']+rect['width']/2-15,rect['y']+rect['height']/2+10,steps=3);page.mouse.up()
                expect(page.locator('[data-brand-status]')).to_have_text('')
                still.screenshot(path=str(parent/'branding-still-mobile.png'))
                still.get_by_role('button',name='Đóng',exact=True).click()
                assert not errors,errors
                browser.close();print('PASS: still-image logo positioning, frame capture, aspect changes, resize/save, mobile, presets and reopening')
        finally:
            server.should_exit=True;thread.join(5);sock.close()

if __name__=='__main__':main()
