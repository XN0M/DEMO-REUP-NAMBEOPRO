"""Offline voice workspace acceptance: real UI, synthetic media, mocked TTS/jobs."""
import json
import socket
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import uvicorn
from playwright.sync_api import sync_playwright, expect
import server
from core import studio_media as media, studio_store as store


def main():
    runtime = ROOT / '.runtime'
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='studio-quad-', dir=runtime) as temporary:
        folder = Path(temporary)
        for name, size in [('landscape.mp4', '640x360'), ('portrait.mp4', '360x640')]:
            media.run(['-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=25:duration=6',
                       '-c:v', 'libx264', '-pix_fmt', 'yuv420p', folder / name])
            store.atomic_json(store.paths(folder / name)[0], {
                'detected_language': 'vi', 'segments': [
                    dict(id=f'cue-{i}', start=i * 6 / 223, end=(i + 1) * 6 / 223,
                         text=f'Câu kiểm thử {i + 1}: chỉnh lời đọc tiếng Việt và nghe trong cảnh.')
                    for i in range(223)]})
        old_root, old_db = server.current_download_dir, server.studio_service.database
        server.current_download_dir = str(folder)
        server.studio_service.database = str(folder / 'jobs.sqlite3')
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(128)
        base = 'http://127.0.0.1:' + str(sock.getsockname()[1])
        service = uvicorn.Server(uvicorn.Config(server.app, lifespan='off', log_level='error'))
        thread = threading.Thread(target=lambda: service.run(sockets=[sock]), daemon=True)
        thread.start()
        calls = {'tts': 0, 'save': 0, 'poll': 0}
        job = None
        include_render = False
        rendered = dict(id='mock-render', kind='preview', status='completed', revision=1,
                        progress=100, segment_ids=[], artifact='jobs/mock-render/output.mp4',
                        output_summary={'audio_mode': 'dubbed', 'has_audio': True})
        proposal = dict(id='mock-proposal', kind='shorten', status='completed', revision=0,
                        progress=100, segment_ids=['cue-0'],
                        proposals=[dict(id='cue-0', text='Lời đọc rút gọn để duyệt.')])

        def route_api(route):
            nonlocal job
            path = urlsplit(route.request.url).path
            value = {}
            if path == '/api/studio/project':
                if route.request.method == 'PUT': calls['save'] += 1
                response = route.fetch(); value = response.json()
                if 'project' in value:
                    value['audio'] = {s['id']: dict(status=['ready', 'missing', 'needs_timing'][i % 3],
                                                   play_start=s['start'], play_end=s['end'], speed=1.2,
                                                   duration=.1, available_duration=.08)
                                      for i, s in enumerate(value['project']['segments'])}
            elif path == '/api/studio/tts':
                calls['tts'] += 1
                body = route.request.post_data_json
                job = dict(id='mock-tts', kind='tts', status='processing', revision=body['revision'],
                           segment_ids=body['segment_ids'], progress=5, message='Mock: đang tạo giọng')
                value = job
            elif path == '/api/studio/jobs/mock-tts':
                calls['poll'] += 1
                job = {**job, 'progress': min(95, 10 + calls['poll'] * 10),
                       'message': f'Mock: {calls["poll"]} câu hoàn tất'}
                value = job
            elif path == '/api/studio/jobs':
                value = {'jobs': ([job] if job else []) + [proposal] + ([rendered] if include_render else [])}
            elif path == '/api/studio/asset':
                route.fulfill(response=route.fetch(url=base + '/api/history/stream/landscape.mp4')); return
            elif path.startswith(('/api/studio/', '/api/history/stream/')):
                route.continue_(); return
            elif path == '/api/config': value = {'download_dir': 'Synthetic media'}
            elif path == '/api/history': value = {'videos': []}
            elif path == '/api/fb/pages': value = {'pages': []}
            elif path == '/api/collections': value = {'collections': ['Chung']}
            elif path == '/api/channels': value = {'channels': []}
            elif path == '/api/channels/videos': value = {'videos': []}
            elif path == '/api/folders': value = {'folders': []}
            elif 'voices' in path:
                value = {'voices': [{'id': 'vi-VN-HoaiMyNeural', 'provider': 'edge', 'name': 'Hoài My'}]}
            route.fulfill(content_type='application/json', body=json.dumps(value))

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1366, 'height': 768})
                errors = []; page.on('pageerror', lambda error: errors.append(error.stack))
                page.route('https://**/*', lambda route: route.abort())
                page.route('**/api/**', route_api)
                page.goto(base, wait_until='domcontentloaded')
                page.evaluate("openSubtitleStudio('landscape.mp4','voice')")
                expect(page.locator('.ns.quadVoice')).to_be_visible()
                page.wait_for_function("document.querySelector('.ns .previewViewport>.stage:not(.renderedStage)>video').readyState>=2")
                page.wait_for_timeout(200)
                page.evaluate('''() => {
                    window.quadTestPlayer = document.querySelector('.ns .previewViewport>.stage:not(.renderedStage)>video');
                    window.mediaLoads = 0; quadTestPlayer.addEventListener('loadstart', () => mediaLoads++);
                    quadTestPlayer.currentTime = 1.5; quadTestPlayer.playbackRate = 1.25;
                    window.cueList = document.querySelector('.list');
                    window.cueEditor = document.querySelector('.cue textarea');
                }''')

                def geometry():
                    return page.evaluate('''() => {
                        const rect=n=>n.getBoundingClientRect().toJSON();
                        const cards=[...document.querySelectorAll('.quadCard')];
                        return {cards:cards.map(rect), grid:rect(document.querySelector('.voiceQuad')),
                            font:getComputedStyle(document.querySelector('.cue textarea')).fontSize,
                            overflow:[...document.querySelectorAll('.quadCard,.quadBody,.cueViewport,.properties,.ns')]
                                .filter(n=>n.getClientRects().length&&n.scrollWidth>n.clientWidth+2).map(n=>[n.className,n.scrollWidth,n.clientWidth])};
                    }''')

                def expand(index):
                    page.locator(f'.quadCard{index} .quadExpand').click()
                    expect(page.locator('.quadDialog')).to_be_visible()
                    page.wait_for_timeout(150)

                def collapse():
                    page.locator('.quadCollapse').click()
                    expect(page.locator('.quadDialog')).to_have_count(0)
                    page.wait_for_timeout(150)

                for width, height in [(1366, 768), (1920, 1080)]:
                    page.set_viewport_size({'width': width, 'height': height}); page.wait_for_timeout(200)
                    g = geometry(); a, b, c, d = g['cards']
                    assert abs(a['width'] / (a['width'] + b['width']) - .55) < .01, g
                    assert abs(a['height'] / (a['height'] + c['height']) - .55) < .01, g
                    assert abs(a['top'] - b['top']) < 1 and abs(c['top'] - d['top']) < 1, g
                    assert g['font'] == '16px' and not g['overflow'], g
                    assert d['bottom'] <= height, g
                    page.locator('.ns').screenshot(path=str(runtime / f'studio-quad-{width}.png'))

                # The same elements, filters, selection, caret, scroll and unsaved text survive.
                page.locator('.cue input[type=checkbox]').first.check()
                edit = page.locator('.cue textarea').first
                edit.fill('Bản chỉnh chưa lưu vẫn giữ nguyên khi mở rộng và thu về.')
                edit.evaluate('e=>{e.focus();e.setSelectionRange(3,9)}')
                for index in [2, 2, 1, 3, 4]:
                    expand(index)
                    assert page.evaluate('quadTestPlayer===document.querySelector(".ns .previewViewport>.stage:not(.renderedStage)>video") && cueList===document.querySelector(".list") && cueEditor===document.querySelector(".cue textarea")'), page.evaluate('({quadTestPlayer:quadTestPlayer===document.querySelector(".ns .previewViewport>.stage:not(.renderedStage)>video"),list:cueList===document.querySelector(".list"),editor:cueEditor===document.querySelector(".cue textarea"),old:cueEditor.closest(".cue").dataset.id,first:document.querySelector(".cue").dataset.id})')
                    assert page.evaluate('mediaLoads') == 0
                    assert abs(page.evaluate('quadTestPlayer.currentTime') - 1.5) < .1
                    assert page.evaluate('quadTestPlayer.playbackRate') == 1.25
                    if index == 2:
                        assert edit.evaluate('e=>getComputedStyle(e).fontSize') == '18px'
                        assert edit.evaluate('e=>[e.selectionStart,e.selectionEnd]') == [3, 9]
                        page.locator('.ns').screenshot(path=str(runtime / 'studio-quad-transcript-expanded.png'))
                    if index == 1:
                        page.evaluate('quadTestPlayer.muted=true;quadTestPlayer.play()')
                        page.wait_for_timeout(80)
                        page.locator('.ns').screenshot(path=str(runtime / 'studio-quad-video-expanded.png'))
                        collapse()
                        assert page.evaluate('!quadTestPlayer.paused')
                        page.evaluate('quadTestPlayer.pause();quadTestPlayer.currentTime=1.5')
                    else: collapse()
                    expect(edit).to_have_value('Bản chỉnh chưa lưu vẫn giữ nguyên khi mở rộng và thu về.')
                expect(page.locator('.cue input[type=checkbox]').first).to_be_checked()
                assert calls['tts'] == 0 and calls['save'] == 0
                page.locator('.livePreview').get_by_role('button', name='Lồng tiếng', exact=True).click()
                expand(1); collapse()
                expect(page.locator('.livePreview').get_by_role('button', name='Lồng tiếng', exact=True)).to_have_attribute('aria-pressed', 'true')
                page.locator('.livePreview').get_by_role('button', name='Tiếng gốc', exact=True).click()

                expand(2)
                search = page.locator('.cueHeading input[type=search]')
                search.fill('kiểm thử 20'); expect(page.locator('.cueHeading h3')).to_contain_text('11/223')
                collapse(); expect(search).to_have_value('kiểm thử 20')
                search.fill('')
                page.locator('.cueViewport').evaluate('e=>e.scrollTop=10000'); page.wait_for_timeout(150)
                top = page.locator('.cueViewport').evaluate('e=>e.scrollTop')
                expand(2); collapse()
                assert abs(page.locator('.cueViewport').evaluate('e=>e.scrollTop') - top) < 3
                page.locator('.cueViewport').evaluate('e=>e.scrollTop=0'); page.wait_for_timeout(100)

                # Undo/redo and save still use the existing handlers, including in a popup.
                expand(2)
                page.locator('.ns').get_by_role('button', name='Hoàn tác', exact=True).click()
                assert page.locator('.cue textarea').first.input_value().startswith('Câu kiểm thử 1:')
                page.locator('.ns').get_by_role('button', name='Làm lại', exact=True).click()
                expect(page.locator('.cue textarea').first).to_have_value('Bản chỉnh chưa lưu vẫn giữ nguyên khi mở rộng và thu về.')
                page.locator('.ns').get_by_role('button', name='Lưu', exact=True).click()
                expect(page.locator('[data-save-status]')).to_contain_text('Đã lưu')
                assert calls['save'] == 1
                # Tab wraps within the popup and Esc closes only the topmost proposal.
                page.locator('.quadCollapse').focus(); page.keyboard.press('Shift+Tab')
                assert page.evaluate('document.querySelector(".quadDialog").contains(document.activeElement)')
                collapse()
                expand(3)
                page.locator('.jobHistory summary').click()
                page.locator('.jobHistory button').filter(has_text='Rút gọn lời đọc').click()
                page.locator('.quadDialog .jobDetail>summary').click()
                page.locator('.quadDialog').get_by_role('button', name='Duyệt đề xuất', exact=True).click()
                expect(page.locator('.popup:not(.quadDialog)')).to_be_visible()
                page.keyboard.press('Escape')
                expect(page.locator('.popup:not(.quadDialog)')).to_have_count(0)
                expect(page.locator('.quadDialog')).to_be_visible()
                page.keyboard.press('Escape'); expect(page.locator('.quadDialog')).to_have_count(0)

                # Timeline canvas uses its new geometry for seeking/editing.
                canvas = page.locator('.timeline canvas'); box = canvas.bounding_box()
                page.mouse.click(box['x'] + box['width'] * .5, box['y'] + 3)
                assert 2.7 < page.evaluate('quadTestPlayer.currentTime') < 3.3

                legacy = page.evaluate("localStorage.getItem('nambeo.studio.workspace.v2')")
                page.locator('.ns').get_by_role('button', name='Bố cục cũ', exact=True).click()
                expect(page.get_by_role('separator')).to_be_visible()
                assert page.evaluate('quadTestPlayer===document.querySelector(".ns .previewViewport video") && mediaLoads===0')
                assert page.evaluate("localStorage.getItem('nambeo.studio.workspace.v2')") == legacy
                page.reload(wait_until='domcontentloaded'); page.evaluate("openSubtitleStudio('landscape.mp4','voice')")
                expect(page.get_by_role('separator')).to_be_visible()
                page.locator('.ns').get_by_role('button', name='4 ô', exact=True).click()
                expect(page.locator('.quadVoice')).to_be_visible()
                page.locator('.cue textarea').first.fill('Giữ thay đổi chưa lưu khi chuyển bước.')
                expand(4)
                page.locator('.ns').get_by_role('tab', name='2. Phụ đề', exact=True).evaluate('b=>b.click()')
                expect(page.locator('.quadDialog')).to_have_count(0)
                expect(page.get_by_role('separator')).to_be_visible()
                for step in ['1. Bản thoại & Dịch', '3. Thương hiệu', '5. Xuất video']:
                    page.locator('.ns').get_by_role('tab', name=step, exact=True).click()
                    assert not page.locator('.ns').evaluate('e=>e.classList.contains("quadVoice")')
                page.locator('.ns').get_by_role('tab', name='4. Lồng tiếng', exact=True).click()
                expect(page.locator('.cue textarea').first).to_have_value('Giữ thay đổi chưa lưu khi chuyển bước.')

                # A single mocked TTS task continues polling while another card is expanded.
                page.locator('.stepActions button').filter(has_text='Tạo giọng cho').click()
                expect(page.locator('.quadCard3 .job')).to_contain_text('Mock: đang tạo giọng')
                expand(2)
                page.wait_for_timeout(1300)
                assert calls['poll'] >= 2 and calls['tts'] == 1, calls
                expect(page.locator('.quadCard3 .job')).to_contain_text('câu hoàn tất')
                collapse()

                for width, height in [(1000, 850), (1366, 690), (390, 844)]:
                    page.set_viewport_size({'width': width, 'height': height}); page.wait_for_timeout(200)
                    g = geometry(); assert not g['overflow'], g
                    assert all(g['cards'][i]['bottom'] < g['cards'][i + 1]['top'] for i in range(3)), g
                    for index in [1, 2, 3, 4]:
                        page.locator(f'.quadCard{index} .quadExpand').scroll_into_view_if_needed()
                        scroll_before = page.locator('.ns').evaluate('e=>e.scrollTop')
                        expand(index); collapse()
                        assert abs(page.locator('.ns').evaluate('e=>e.scrollTop') - scroll_before) < 3
                    if width == 390:
                        page.locator('.ns').evaluate('e=>e.scrollTop=0')
                        page.locator('.ns').screenshot(path=str(runtime / 'studio-quad-mobile.png'))

                page.locator('.ns').get_by_role('button', name='Quay lại thư viện', exact=True).click()
                job = None
                page.set_viewport_size({'width': 1366, 'height': 768})
                page.evaluate("openSubtitleStudio('portrait.mp4','voice')")
                expect(page.locator('.quadVoice')).to_be_visible()
                expand(1)
                page.wait_for_function("document.querySelector('.ns .previewViewport>.stage:not(.renderedStage)>video').readyState>=2")
                ratio = page.locator('.ns .previewViewport>.stage:not(.renderedStage)').evaluate('e=>e.clientWidth/e.clientHeight')
                assert abs(ratio - 9/16) < .01, ratio
                page.locator('.ns').screenshot(path=str(runtime / 'studio-quad-portrait-expanded.png'))
                collapse()

                # Render playback is the same player, too; no src/load on expansion.
                page.locator('.ns').get_by_role('button', name='Quay lại thư viện', exact=True).click()
                include_render = True
                rendered['revision'] = store.load(folder / 'landscape.mp4')['revision']
                page.evaluate("openSubtitleStudio('landscape.mp4','voice')")
                page.wait_for_function("!document.querySelector('.renderedStage').classList.contains('hidden') && document.querySelector('.renderedStage video').readyState>=2")
                page.evaluate('''() => {
                    window.quadRenderedPlayer=document.querySelector('.renderedStage video');
                    window.renderLoads=0;quadRenderedPlayer.addEventListener('loadstart',()=>renderLoads++);
                    quadRenderedPlayer.currentTime=1;quadRenderedPlayer.playbackRate=1.5;
                }''')
                page.wait_for_function('!quadRenderedPlayer.seeking && Math.abs(quadRenderedPlayer.currentTime-1)<.1')
                expand(1); collapse()
                assert page.evaluate('quadRenderedPlayer===document.querySelector(".renderedStage video") && renderLoads===0 && quadRenderedPlayer.playbackRate===1.5 && Math.abs(quadRenderedPlayer.currentTime-1)<.1'), page.evaluate('({same:quadRenderedPlayer===document.querySelector(".renderedStage video"),loads:renderLoads,rate:quadRenderedPlayer.playbackRate,time:quadRenderedPlayer.currentTime,paused:quadRenderedPlayer.paused,ready:quadRenderedPlayer.readyState})')
                expect(page.locator('.renderedStage')).to_be_visible()
                page.reload(wait_until='domcontentloaded')
                page.evaluate("openSubtitleStudio('landscape.mp4','voice')")
                expect(page.locator('.quadVoice')).to_be_visible()
                expect(page.locator('.quadDialog')).to_have_count(0)
                assert not errors, errors
                print('PASS: voice four-panel layout, media/state, modals, jobs, responsive and legacy. Mock calls:', calls)
                browser.close()
        finally:
            service.should_exit = True; thread.join(timeout=10)
            server.current_download_dir, server.studio_service.database = old_root, old_db


if __name__ == '__main__':
    main()
