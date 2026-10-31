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
                page=browser.new_page(viewport={'width':1440,'height':1000},has_touch=True)
                errors=[];page.on('pageerror',lambda error:errors.append(error.stack))
                page.route('https://**/*',lambda route:route.abort())
                page.route('**/api/**',route_api)
                page.goto(base,wait_until='domcontentloaded')
                page.evaluate("openSubtitleStudio('landscape.mp4','transcript')")
                expect(page.locator('.ns .cue textarea').first).to_be_visible()
                page.wait_for_function("document.querySelector('.ns .previewViewport').clientHeight>100")
                page.wait_for_function("document.querySelector('.ns .previewViewport video').readyState>=2")
                def geometry():
                    return page.evaluate('''() => {
                        const rect=s=>document.querySelector('.ns '+s).getBoundingClientRect().toJSON();
                        return {stage:rect('.stage:not(.hidden)'),viewport:rect('.previewViewport'),timeline:rect('.timeline'),cues:rect('.cueViewport'),player:rect('.playerPanel'),root:rect('.layout'),live:rect('.livePreview'),tabs:[...document.querySelectorAll('.ns .workflowTabs button')].map(e=>e.getBoundingClientRect().toJSON())};
                    }''')
                def fit(expected_ratio):
                    values=geometry();stage,viewport=values['stage'],values['viewport']
                    assert abs(stage['width']/stage['height']-expected_ratio)<.02,values
                    assert stage['left']>=viewport['left']-1 and stage['right']<=viewport['right']+1,values
                    assert stage['top']>=viewport['top']-1 and stage['bottom']<=viewport['bottom']+1,values
                    assert values['timeline']['bottom']<=values['player']['bottom']+1,values
                    assert values['cues']['height']>=55,values
                    assert abs((stage['left']+stage['right'])/2-(viewport['left']+viewport['right'])/2)<2,values
                    assert values['live']['bottom']<=viewport['top']+1,values
                    assert all(abs(t['top']-values['tabs'][0]['top'])<1 for t in values['tabs']),values
                    assert page.locator('.ns .previewViewport > :not(.stage)').count()==0
                    return values
                page.wait_for_timeout(200)
                values=fit(16/9)
                # The extra live-audio row consumes height; retain the existing cue allocation.
                assert values['stage']['height']>=330-values['live']['height']-6,values
                page.locator('.ns').screenshot(path=str(runtime/'studio-workspace-desktop.png'))
                for width,height in [(1920,1080),(2560,1440),(1280,800)]:
                    page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(150);fit(16/9)
                page.set_viewport_size({'width':1440,'height':1000});page.wait_for_timeout(150)
                page.locator('.ns .desktopPropertyButton').click();page.wait_for_timeout(250);fit(16/9)
                page.locator('.ns .desktopPropertyButton').click();page.wait_for_timeout(250);fit(16/9)
                # Dragging changes actual pane sizes and persists only after release.
                grip=page.get_by_role('separator',name='Thay đổi chiều cao video và bảng câu')
                def drag(delta, cancel=False):
                    box=grip.bounding_box();x,y=box['x']+box['width']/2,box['y']+box['height']/2
                    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x,y+delta,steps=6)
                    if cancel:page.keyboard.press('Escape')
                    page.mouse.up();page.wait_for_timeout(100)
                starting=geometry();drag(-80);smaller=fit(16/9)
                assert smaller['player']['height']<starting['player']['height']-60,(starting,smaller)
                assert smaller['cues']['height']>starting['cues']['height']+60,(starting,smaller)
                drag(30);restored=fit(16/9)
                assert restored['player']['height']>smaller['player']['height']+20
                stored=page.evaluate("localStorage.getItem('nambeo.studio.workspace.v2')")
                drag(60,cancel=True);assert abs(geometry()['player']['height']-restored['player']['height'])<2
                assert page.evaluate("localStorage.getItem('nambeo.studio.workspace.v2')")==stored
                assert not page.locator('.ns').evaluate("e=>e.classList.contains('workspaceResizing')")
                page.reload(wait_until='domcontentloaded');page.evaluate("openSubtitleStudio('landscape.mp4','transcript')")
                page.wait_for_function("document.querySelector('.ns .previewViewport video').readyState>=2")
                page.wait_for_timeout(150);assert abs(geometry()['player']['height']-restored['player']['height'])<2
                grip.focus();before_key=geometry()['player']['height'];page.keyboard.press('ArrowUp');page.wait_for_timeout(50)
                assert geometry()['player']['height']<before_key-5
                page.keyboard.press('Shift+ArrowDown');page.wait_for_timeout(50);assert geometry()['player']['height']>before_key+15
                grip.dblclick();page.wait_for_timeout(100)
                assert abs(int(grip.get_attribute('aria-valuenow'))-50)<=1
                page.locator('.ns .resetWorkspace').click();page.wait_for_timeout(100)
                values=fit(16/9)
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
                text.fill('Bản sửa chưa lưu vẫn giữ nguyên');text.evaluate('e=>e.setSelectionRange(2,8)')
                drag(-25)
                expect(text).to_be_focused();expect(text).to_have_value('Bản sửa chưa lưu vẫn giữ nguyên')
                assert text.evaluate('e=>[e.selectionStart,e.selectionEnd]')==[2,8]
                text.press('Tab')
                page.evaluate("document.querySelector('.ns .previewViewport video').currentTime=1.2")
                page.locator('.ns .previewTop .studioCut').click()
                expect(page.locator('.cutEditorDialog')).to_be_visible()
                page.wait_for_function("document.getElementById('cutSourcePlayer').readyState>=2")
                page.wait_for_function("Math.abs(document.getElementById('cutSourcePlayer').currentTime-1.2)<.1")
                page.get_by_role('button',name='Thêm mốc tại đầu phát',exact=True).click()
                expect(page.locator('#btnStartCropTrim')).to_have_text('Xuất 2 đoạn')
                page.locator('.cutEditorDialog').screenshot(path=str(runtime/'studio-workspace-cutter.png'))
                page.locator('#btnStartCropTrim').click()
                page.wait_for_function("!cutBusy && cutEl('cutOutputList').children.length===2",timeout=30000)
                page.get_by_role('button',name='Đóng · Quay lại Studio',exact=True).click()
                expect(page.locator('.cutEditorDialog')).to_have_count(0)
                expect(page.locator('.ns .cue textarea').first).to_have_value('Bản sửa chưa lưu vẫn giữ nguyên')
                expect(page.locator('.ns [data-save-status]')).to_contain_text('Chưa lưu')
                assert not page.locator('.ns').evaluate('e=>e.inert')
                page.wait_for_timeout(50)
                assert page.locator('.ns .previewTop .studioCut').evaluate('e=>e===document.activeElement')
                # Restore the preset after the deliberate editing-time drag.
                page.locator('.ns .resetWorkspace').click();page.wait_for_timeout(100)
                # Portrait and smaller desktop screen use the same fitting calculation.
                page.on('dialog',lambda dialog:dialog.accept())
                page.evaluate("openSubtitleStudio('portrait.mp4','transcript')")
                page.wait_for_function("document.querySelector('.ns .studioFilename').textContent==='portrait.mp4'")
                page.wait_for_function("document.querySelector('.ns .previewViewport video').readyState>=2")
                page.wait_for_timeout(100);portrait=fit(9/16)
                assert portrait['stage']['height']>=330-portrait['live']['height']-6,portrait
                page.locator('.ns').screenshot(path=str(runtime/'studio-workspace-portrait.png'))
                page.set_viewport_size({'width':1280,'height':800});page.wait_for_timeout(250);fit(9/16)
                page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(250);mobile=fit(9/16)
                assert mobile['stage']['height']>=280,mobile
                # Real touch input exercises pointer capture and touch-action on the handle.
                grip.scroll_into_view_if_needed();box=grip.bounding_box();x,y=box['x']+box['width']/2,box['y']+box['height']/2
                before_touch=geometry()['player']['height']
                now=int(grip.get_attribute('aria-valuenow'));maximum=int(grip.get_attribute('aria-valuemax'));delta=25 if maximum-now>3 else -25
                client=page.context.new_cdp_session(page)
                client.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y}]})
                client.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x,'y':y+delta}]})
                client.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]});page.wait_for_timeout(100)
                assert abs(geometry()['player']['height']-before_touch)>10
                assert not page.locator('.ns').evaluate("e=>e.classList.contains('workspaceResizing')")
                assert page.locator('.ns').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
                expect(page.locator('.ns .cueHeading')).to_be_visible()
                expect(page.locator('.ns .cue textarea').first).to_be_visible()
                page.locator('.ns .mobilePropertyButton').click();expect(page.locator('.ns .properties')).to_be_visible()
                page.locator('.ns .mobilePropertyButton').click();expect(page.locator('.ns .properties')).not_to_be_visible()
                page.locator('.ns').screenshot(path=str(runtime/'studio-workspace-mobile.png'))
                page.locator('.ns .studioControls .studioCut').click();expect(page.locator('.cutEditorDialog')).to_be_visible()
                page.keyboard.press('Escape');expect(page.locator('.cutEditorDialog')).to_have_count(0)
                assert not errors,errors
                browser.close()
                print('PASS: mouse/touch drag, persistence, Escape cancel, keyboard resize, typing focus, presets, responsive preview and cutter return')
        finally:
            service.should_exit=True;thread.join(timeout=5)
            server.current_download_dir=old_root;server.studio_service.database=old_db


if __name__=='__main__':main()
