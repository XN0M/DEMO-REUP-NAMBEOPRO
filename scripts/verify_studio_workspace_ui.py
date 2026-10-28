"""Offline Studio geometry and cutter handoff regression, with synthetic videos."""
import json
import socket
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import uvicorn
from playwright.sync_api import sync_playwright, expect
import server
from core import studio_media as media, studio_store as store


def main():
    runtime=ROOT/'.runtime';runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='studio-workspace-',dir=runtime) as temporary:
        folder=Path(temporary)
        for name,size in [('landscape.mp4','640x360'),('portrait.mp4','360x640')]:
            media.run(['-f','lavfi','-i',f'testsrc2=size={size}:rate=25:duration=6','-c:v','libx264','-pix_fmt','yuv420p',folder/name])
            store.atomic_json(store.paths(folder/name)[0], {'detected_language':'vi','segments':[
                {'id':f'cue-{i}','start':i*.2,'end':i*.2+.18,'text':f'Câu kiểm thử {i+1}'} for i in range(25)]})
        old_root,old_db=server.current_download_dir,server.studio_service.database
        server.current_download_dir=str(folder);server.studio_service.database=str(folder/'jobs.sqlite3')
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
        base='http://127.0.0.1:'+str(sock.getsockname()[1])
        service=uvicorn.Server(uvicorn.Config(server.app,lifespan='off',log_level='error'))
        thread=threading.Thread(target=lambda:service.run(sockets=[sock]),daemon=True);thread.start()
        def route_api(route):
            path=urlsplit(route.request.url).path
            if path.startswith(('/api/studio/','/api/video/crop-trim','/api/history/stream/')):
                route.continue_();return
            value={}
            if path=='/api/config':value={'download_dir':'Synthetic media'}
            elif path=='/api/history':value={'videos':[]}
            elif path=='/api/fb/pages':value={'pages':[]}
            elif path=='/api/collections':value={'collections':['Chung']}
            elif path=='/api/channels':value={'channels':[]}
            elif path=='/api/channels/videos':value={'videos':[]}
            elif path=='/api/folders':value={'folders':[]}
            elif 'voices' in path:value={'voices':[]}
            route.fulfill(content_type='application/json',body=json.dumps(value))
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000})
                errors=[];page.on('pageerror',lambda error:errors.append(error.stack))
                page.route('**/api/**',route_api)
                page.goto(base,wait_until='domcontentloaded')
                page.evaluate("openSubtitleStudio('landscape.mp4','transcript')")
                expect(page.locator('.ns .cue textarea').first).to_be_visible()
                page.wait_for_function("document.querySelector('.ns .previewViewport').clientHeight>100")
                page.wait_for_function("document.querySelector('.ns .previewViewport video').readyState>=2")
                def geometry():
                    return page.evaluate('''() => {
                        const rect=s=>document.querySelector('.ns '+s).getBoundingClientRect().toJSON();
                        return {stage:rect('.stage:not(.hidden)'),viewport:rect('.previewViewport'),timeline:rect('.timeline'),cues:rect('.cueViewport'),player:rect('.playerPanel'),root:rect('.layout')};
                    }''')
                def fit(expected_ratio):
                    values=geometry();stage,viewport=values['stage'],values['viewport']
                    assert abs(stage['width']/stage['height']-expected_ratio)<.02,values
                    assert stage['left']>=viewport['left']-1 and stage['right']<=viewport['right']+1,values
                    assert stage['top']>=viewport['top']-1 and stage['bottom']<=viewport['bottom']+1,values
                    assert values['timeline']['bottom']<=values['player']['bottom']+1,values
                    assert values['cues']['height']>=55,values
                    return values
                page.wait_for_timeout(200)
                values=fit(16/9)
                assert values['stage']['width']>=600 and values['stage']['height']>=330,values
                page.screenshot(path=str(runtime/'studio-workspace-desktop.png'),full_page=True)
                # Splitter presets change real image size, leave a cue viewport, and can reset.
                initial=values['stage']['height']
                page.locator('.ns .presetBalance').click();page.wait_for_timeout(100)
                balanced=fit(16/9)['stage']['height']
                assert balanced<initial
                page.locator('.ns .presetVideo').click();page.wait_for_timeout(100)
                assert fit(16/9)['stage']['height']>balanced
                page.locator('.ns .resetWorkspace').click();page.wait_for_timeout(100)
                # Compact timeline still edits cue handles at their drawn position.
                canvas=page.locator('.ns .timeline canvas');canvas.scroll_into_view_if_needed();box=canvas.bounding_box()
                page.mouse.move(box['x']+box['width']*.2/6,box['y']+box['height']*.58)
                page.mouse.down();page.mouse.move(box['x']+box['width']*.24/6,box['y']+box['height']*.58);page.mouse.up()
                expect(page.locator('.ns [data-save-status]')).to_contain_text('Chưa lưu')
                text=page.locator('.ns .cue textarea').first
                text.fill('Bản sửa chưa lưu vẫn giữ nguyên');text.press('Tab')
                page.evaluate("document.querySelector('.ns .previewViewport video').currentTime=1.2")
                page.locator('.ns .previewTop .studioCut').click()
                expect(page.locator('.cutEditorDialog')).to_be_visible()
                page.wait_for_function("document.getElementById('cutSourcePlayer').readyState>=2")
                page.wait_for_function("Math.abs(document.getElementById('cutSourcePlayer').currentTime-1.2)<.1")
                page.get_by_role('button',name='Thêm mốc tại đầu phát',exact=True).click()
                expect(page.locator('#btnStartCropTrim')).to_have_text('Xuất 2 đoạn')
                page.screenshot(path=str(runtime/'studio-workspace-cutter.png'),full_page=True)
                page.locator('#btnStartCropTrim').click()
                page.wait_for_function("!cutBusy && cutEl('cutOutputList').children.length===2",timeout=30000)
                page.get_by_role('button',name='Đóng · Quay lại Studio',exact=True).click()
                expect(page.locator('.cutEditorDialog')).to_have_count(0)
                expect(page.locator('.ns .cue textarea').first).to_have_value('Bản sửa chưa lưu vẫn giữ nguyên')
                expect(page.locator('.ns [data-save-status]')).to_contain_text('Chưa lưu')
                assert not page.locator('.ns').evaluate('e=>e.inert')
                page.wait_for_timeout(50)
                assert page.locator('.ns .previewTop .studioCut').evaluate('e=>e===document.activeElement')
                # Portrait and smaller desktop screen use the same fitting calculation.
                page.on('dialog',lambda dialog:dialog.accept())
                page.evaluate("openSubtitleStudio('portrait.mp4','transcript')")
                page.wait_for_function("document.querySelector('.ns .studioFilename').textContent==='portrait.mp4'")
                page.wait_for_function("document.querySelector('.ns .previewViewport video').readyState>=2")
                page.wait_for_timeout(100);portrait=fit(9/16)
                assert portrait['stage']['height']>=330,portrait
                page.screenshot(path=str(runtime/'studio-workspace-portrait.png'),full_page=True)
                page.set_viewport_size({'width':1280,'height':800});page.wait_for_timeout(250);fit(9/16)
                page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(250);mobile=fit(9/16)
                assert mobile['stage']['height']>=280,mobile
                assert page.locator('.ns').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
                expect(page.locator('.ns .cueHeading')).to_be_visible()
                expect(page.locator('.ns .cue textarea').first).to_be_visible()
                page.locator('.ns .mobilePropertyButton').click();expect(page.locator('.ns .properties')).to_be_visible()
                page.locator('.ns .mobilePropertyButton').click();expect(page.locator('.ns .properties')).not_to_be_visible()
                page.screenshot(path=str(runtime/'studio-workspace-mobile.png'),full_page=True)
                page.locator('.ns .studioControls .studioCut').click();expect(page.locator('.cutEditorDialog')).to_be_visible()
                page.keyboard.press('Escape');expect(page.locator('.cutEditorDialog')).to_have_count(0)
                assert not errors,errors
                browser.close()
                print('PASS: large landscape/portrait previews, splitter presets, compact cue editing, desktop/mobile cutter, unsaved edits and Escape return')
        finally:
            service.should_exit=True;thread.join(timeout=5)
            server.current_download_dir=old_root;server.studio_service.database=old_db


if __name__=='__main__':main()
