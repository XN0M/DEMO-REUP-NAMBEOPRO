"""Exercise multi-marker editing in Chromium against local synthetic media."""
import json
import socket
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright
import server
from tests.test_video_cuts import VideoCuts


def main():
    VideoCuts.setUpClass()
    old_root = server.current_download_dir
    server.current_download_dir = VideoCuts.temp.name
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(128)
    base = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    service = uvicorn.Server(uvicorn.Config(server.app, lifespan='off', log_level='error'))
    thread = threading.Thread(target=lambda: service.run(sockets=[sock]), daemon=True)
    thread.start()
    requests = []
    def route_api(route):
        request = route.request; path = urlsplit(request.url).path
        if path.startswith('/api/video/crop-trim') or path.startswith('/api/history/stream/'):
            if path == '/api/video/crop-trim' and request.method == 'POST':
                requests.append(request.post_data_json)
            route.continue_(); return
        value = {}
        if path.startswith('/api/transcript/'):
            route.fulfill(status=404, content_type='application/json', body='{}'); return
        if path == '/api/config': value = {'download_dir': 'Synthetic media'}
        elif path == '/api/history': value = {'videos': []}
        elif path == '/api/fb/pages': value = {'pages': []}
        elif path == '/api/collections': value = {'collections': ['Chung']}
        elif path == '/api/channels': value = {'channels': []}
        elif path == '/api/channels/videos': value = {'videos': []}
        elif path == '/api/folders': value = {'folders': []}
        elif 'voices' in path: value = {'voices': []}
        route.fulfill(content_type='application/json', body=json.dumps(value))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100}, has_touch=True)
            errors=[]; page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/**', route_api)
            page.goto(base, wait_until='domcontentloaded')
            page.wait_for_function("typeof resetCutsForSource === 'function'")
            page.evaluate('path => openLegacyTranscriptModal(encodeURIComponent(path), 5)', VideoCuts.source.name)
            page.wait_for_function('cutInfo?.fps === 25')
            page.wait_for_function("document.getElementById('cutSourcePlayer').readyState >= 2")
            assert page.locator('#trimStartInput').count() == 1
            assert not page.locator('#losslessTrimCheck').is_checked()
            for point in [.8, 1.6]:
                page.evaluate('t => {cutEl("cutSourcePlayer").currentTime=t;}', point)
                page.get_by_role('button', name='Thêm mốc tại đầu phát', exact=True).click()
            assert page.locator('#cutSegmentsList > div').count() == 3
            assert page.locator('#cutMarkerLabels').inner_text().count('00:00:') == 4
            assert page.locator('#btnStartCropTrim').inner_text() == 'Xuất 3 đoạn'
            # A/B edits keep interior markers and do not overwrite the other input.
            page.locator('#cutStartInput').fill('0.2'); page.locator('#cutStartInput').press('Tab')
            page.locator('#cutEndInput').fill('2.8'); page.locator('#cutEndInput').press('Tab')
            assert page.evaluate('trimState.start') == .2
            assert page.evaluate('trimState.end') == 2.8
            # Pointer dragging, crossing prevention and arrow keys.
            track = page.locator('#trimTimelineTrack'); track.scroll_into_view_if_needed()
            box=track.bounding_box(); marker=page.locator('[data-cut-index="1"]'); marker.hover()
            page.mouse.down(); page.mouse.move(box['x']+box['width']*.4,box['y']+16,steps=8);page.mouse.up()
            assert 1.1 < page.evaluate('cutMarks[0]') < 1.3
            page.locator('[data-cut-index="1"]').focus()
            before=page.evaluate('cutMarks[0]')
            page.keyboard.press('ArrowRight')
            assert abs(page.evaluate('cutMarks[0]')-before-.04)<1e-6
            page.locator('#cutSelectedInput').fill('99');page.locator('#cutSelectedInput').press('Tab')
            assert page.evaluate('cutMarks[1]-cutMarks[0] >= cutFrame()-1e-6')
            # Dense labels occupy separate rows and stay inside their container.
            page.screenshot(path=str(Path('.runtime/cuts-ui.png').resolve()),full_page=True)
            assert page.evaluate('''() => {
                const holder=cutEl('cutMarkerLabels').getBoundingClientRect();
                const labels=[...cutEl('cutMarkerLabels').querySelectorAll('button')].map(e=>e.getBoundingClientRect());
                return labels.every((a,i)=>a.left>=holder.left-1&&a.right<=holder.right+1&&labels.every((b,j)=>i===j||a.right<=b.left||b.right<=a.left||a.bottom<=b.top||b.bottom<=a.top));
            }''')
            page.get_by_role('button', name='Xóa mốc đã chọn', exact=True).click()
            assert page.locator('#cutSegmentsList > div').count() == 2
            # Touch PointerEvents use the same constrained editing path.
            page.locator('[data-cut-index="1"]').dispatch_event('pointerdown', {'pointerType':'touch','pointerId':2,'clientX':box['x']+box['width']*.5})
            page.dispatch_event('body','pointermove',{'pointerType':'touch','pointerId':2,'clientX':box['x']+box['width']*.6})
            page.dispatch_event('body','pointerup',{'pointerType':'touch','pointerId':2})
            assert 1.7 < page.evaluate('cutMarks[0]') < 1.9
            page.locator('#cutSegmentsList button').first.click()
            page.wait_for_function("!cutEl('cutSourcePlayer').paused")
            page.evaluate("cutEl('cutSourcePlayer').pause()")
            page.locator('#btnStartCropTrim').click()
            page.wait_for_function("!cutBusy && cutEl('cutOutputList').children.length===2", timeout=30000)
            assert len(requests)==1 and len(requests[0]['segments'])==2
            assert requests[0]['lossless_trim'] is False
            assert page.locator('#cutOutputList a').count()==2
            with page.expect_download() as download:
                page.locator('#cutOutputList a').first.click()
            assert download.value.suggested_filename=='part_001.mp4'
            page.set_viewport_size({'width':390,'height':844})
            page.wait_for_function("cutEl('cutMarkerLabels').scrollWidth <= cutEl('cutMarkerLabels').clientWidth")
            page.locator('#trimTimelineTrack').scroll_into_view_if_needed()
            page.screenshot(path=str(Path('.runtime/cuts-mobile.png').resolve()),full_page=True)
            assert page.evaluate("cutEl('cutMarkerLabels').scrollWidth <= cutEl('cutMarkerLabels').clientWidth"), page.evaluate("({track:cutEl('trimTimelineTrack').getBoundingClientRect().toJSON(),holder:cutEl('cutMarkerLabels').getBoundingClientRect().toJSON(),scroll:cutEl('cutMarkerLabels').scrollWidth,labels:[...cutEl('cutMarkerLabels').children].map(e=>e.getBoundingClientRect().toJSON())})")
            # A response from an earlier session must not overwrite the next video.
            page.evaluate('''() => {
                const session=cutSession;cropTrimTaskId='old-task';
                window.originalCutFetch=window.fetch;
                window.fetch=()=>new Promise(resolve=>window.resolveOldCut=()=>resolve({ok:true,json:async()=>({status:'completed',outputs:[{index:9,url:'old',start:0,end:1}]})}));
                pollCropTrimProgress('old-task',session);
                currentTranscriptRelPath='next.mp4';resetCutsForSource('next.mp4');
                window.fetch=window.originalCutFetch;window.resolveOldCut();
            }''')
            page.wait_for_timeout(100)
            assert page.locator('#cropTrimResultCard').evaluate("e=>e.classList.contains('hidden')")
            assert page.evaluate('cutMarks.length') == 0
            assert not errors, errors
            browser.close()
            print('PASS: markers, mouse/touch/keyboard, labels, A/B, preview, export/download, stale responses')
    finally:
        service.should_exit = True; thread.join(timeout=5)
        server.current_download_dir = old_root
        VideoCuts.tearDownClass()


if __name__ == '__main__':
    main()
