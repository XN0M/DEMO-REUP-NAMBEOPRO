"""Offline Studio workspace acceptance: real UI, synthetic media, mocked TTS/jobs."""
import json
import io
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
from PIL import Image
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
        for name in ['bilingual.mp4', 'empty.mp4']:
            (folder / name).write_bytes((folder / 'landscape.mp4').read_bytes())
        store.atomic_json(store.paths(folder / 'bilingual.mp4')[0], {
            'detected_language': 'zh', 'segments': [
                dict(id=f'cue-{i}', start=i * 6 / 223, end=(i + 1) * 6 / 223,
                     chinese=f'原文 {i+1}', vietnamese=f'Bản dịch câu {i+1}.') for i in range(223)]})
        store.atomic_json(store.paths(folder / 'empty.mp4')[0], {'segments': []})
        old_root, old_db = server.current_download_dir, server.studio_service.database
        server.current_download_dir = str(folder)
        server.studio_service.database = str(folder / 'jobs.sqlite3')
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(128)
        base = 'http://127.0.0.1:' + str(sock.getsockname()[1])
        service = uvicorn.Server(uvicorn.Config(server.app, lifespan='off', log_level='error'))
        thread = threading.Thread(target=lambda: service.run(sockets=[sock]), daemon=True)
        thread.start()
        calls = {'tts': 0, 'save': 0, 'poll': 0, 'translate': 0, 'transcribe': 0, 'preview': 0, 'export': 0}
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
            elif path in ['/api/studio/' + kind for kind in ('translate', 'transcribe', 'preview', 'export')]:
                kind = path.rsplit('/', 1)[1]; calls[kind] += 1
                body = route.request.post_data_json
                job = dict(id='mock-' + kind, filepath=body['filepath'], kind=kind, status='processing',
                           revision=body['revision'], segment_ids=body.get('segment_ids') or [],
                           progress=10, message='Mock: ' + kind)
                value = job
            elif path.startswith('/api/studio/jobs/mock-') and job and path.endswith(job['id']):
                if job['kind'] == 'transcribe':
                    job = {**job, 'status': 'error', 'message': 'Mock: nhận diện lỗi để thử giao diện',
                           'failed': [{'error': 'Lỗi nhận diện giả lập'}]}
                else:
                    job = {**job, 'status': 'completed', 'progress': 100}
                    if job['kind'] == 'translate': job['proposals'] = [{'id': 'cue-0', 'text': 'Bản dịch giả lập đã duyệt.'}]
                    else:
                        job.update(artifact='jobs/' + job['id'] + '/output.mp4',
                                   output_summary={'audio_mode': 'original', 'has_audio': False})
                value = job
            elif path == '/api/studio/jobs':
                value = {'jobs': ([job] if job else []) + [proposal] + ([rendered] if include_render else [])}
            elif path == '/api/studio/recent-jobs': value = {'jobs': [job] if job and job['kind'] == 'export' else []}
            elif path == '/api/studio/cleanup/plan':
                value = {'id': 'mock-cleanup', 'mode': 'compact', 'bytes': 1024, 'remove': ['jobs/old-preview.mp4']}
            elif path == '/api/studio/branding/presets': value = {'presets': []}
            elif path == '/api/studio/asset':
                route.fulfill(response=route.fetch(url=base + '/api/history/stream/landscape.mp4')); return
            elif path.startswith('/api/studio/') and route.request.method not in ['GET', 'HEAD'] and path not in ['/api/studio/branding/logo', '/api/studio/branding/preview']:
                route.fulfill(status=500, content_type='application/json', body='{"detail":"Unmocked processing blocked"}'); return
            elif path.startswith(('/api/studio/', '/api/history/stream/')):
                route.continue_(); return
            elif path == '/api/config':
                value = {'download_dir': 'Synthetic media', 'has_gemini_key': True, 'default_ai_provider': 'gemini'}
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
                # The global choice supersedes the trial choice, without touching it.
                for current, old, expected in [(None, None, 'quad'), (None, 'legacy', 'legacy'),
                                                (None, 'quad', 'quad'), ('legacy', 'quad', 'legacy'),
                                                ('quad', 'legacy', 'quad')]:
                    context = browser.new_context()
                    migration = context.new_page()
                    migration.route('https://**/*', lambda route: route.abort())
                    migration.route('**/api/**', route_api)
                    seeds = {}
                    if current: seeds['nambeo.studio.layout.v1'] = current
                    if old: seeds['nambeo.studio.voice.quad.v1'] = old
                    context.add_init_script('for(const [key,value] of Object.entries(' + json.dumps(seeds) + '))localStorage.setItem(key,value)')
                    migration.goto(base, wait_until='domcontentloaded')
                    migration.evaluate("openSubtitleStudio('landscape.mp4','subtitles')")
                    expect(migration.locator('.ns .quadModeSwitch')).to_be_visible()
                    assert migration.locator('.ns').evaluate('e=>e.classList.contains("quadWorkspace")') == (expected == 'quad')
                    assert migration.evaluate("localStorage.getItem('nambeo.studio.layout.v1')") == expected
                    assert migration.evaluate("localStorage.getItem('nambeo.studio.voice.quad.v1')") == old
                    context.close()
                page = browser.new_page(viewport={'width': 1366, 'height': 768})
                errors = []; page.on('pageerror', lambda error: errors.append(error.stack))
                page.route('https://**/*', lambda route: route.abort())
                page.route('**/api/**', route_api)
                page.goto(base, wait_until='domcontentloaded')
                page.evaluate("openSubtitleStudio('landscape.mp4','voice')")
                expect(page.locator('.ns.quadWorkspace')).to_be_visible()
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
                        return {cards:cards.map(rect), grid:rect(document.querySelector('.studioQuad')),
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
                expect(page.locator('.quadWorkspace')).to_be_visible()
                page.locator('.cue textarea').first.fill('Giữ thay đổi chưa lưu khi chuyển bước.')
                expand(4)
                page.locator('.ns').get_by_role('tab', name='2. Phụ đề', exact=True).evaluate('b=>b.click()')
                expect(page.locator('.quadDialog')).to_have_count(0)
                expect(page.locator('.quadWorkspace')).to_be_visible()
                for step in ['1. Bản thoại & Dịch', '3. Thương hiệu', '5. Xuất video']:
                    page.locator('.ns').get_by_role('tab', name=step, exact=True).click()
                    assert page.locator('.ns').evaluate('e=>e.classList.contains("quadWorkspace")')
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
                expect(page.locator('.quadWorkspace')).to_be_visible()
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
                expect(page.locator('.quadWorkspace')).to_be_visible()
                expect(page.locator('.quadDialog')).to_have_count(0)

                # Every step shares the four panels, including contextual configuration.
                page.locator('.ns').get_by_role('button', name='Quay lại thư viện', exact=True).click()
                include_render = False; job = None
                page.evaluate("openSubtitleStudio('bilingual.mp4','transcript')")
                expect(page.locator('.quadWorkspace')).to_be_visible()
                page.wait_for_function("document.querySelector('.ns .previewViewport video').readyState>=2")
                page.evaluate('''() => {
                    window.allStepsPlayer=document.querySelector('.ns .previewViewport video');
                    window.allStepsLoads=0;allStepsPlayer.addEventListener('loadstart',()=>allStepsLoads++);
                    allStepsPlayer.currentTime=1;allStepsPlayer.playbackRate=1.25;
                }''')
                step_titles = {'transcript': 'Nhận diện & dịch', 'subtitles': 'Kiểu chữ & phụ đề',
                               'branding': 'Logo & watermark', 'voice': 'Cấu hình giọng', 'export': 'Cấu hình xuất video'}

                def step(key):
                    page.locator(f'.ns .workflowTabs [data-step="{key}"]').click()
                    expect(page.locator('.quadCard4>.quadHeader h3')).to_contain_text(step_titles[key])
                    expect(page.locator(f'.quadCard4 #studio-panel-{key}')).to_be_visible()

                for width, height in [(1366, 768), (1920, 1080), (390, 844)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    for key in step_titles:
                        step(key); page.wait_for_timeout(100)
                        g = geometry(); assert not g['overflow'], (key, width, g)
                        assert page.locator('.stepActions').evaluate('e=>e.scrollHeight<=e.clientHeight+1'), (key, width)
                        page.locator('.ns').evaluate('e=>e.scrollTop=0')
                        page.locator('.ns').screenshot(path=str(runtime / f'studio-all-{key}-{width}.png'))
                        if key == 'transcript':
                            assert len(page.locator('.cueMain').first.evaluate('e=>getComputedStyle(e).gridTemplateColumns').split()) == 1
                        for index in [1, 2, 3, 4]:
                            expand(index)
                            if index == 2 and key == 'transcript':
                                columns = page.locator('.cueMain').first.evaluate('e=>getComputedStyle(e).gridTemplateColumns')
                                assert len(columns.split()) == (2 if width >= 1024 else 1), columns
                            if index == 4:
                                expect(page.locator('.quadDialog h3').first).to_contain_text(step_titles[key])
                                if width == 1366:
                                    page.locator('.ns').screenshot(path=str(runtime / f'studio-all-{key}-expanded.png'))
                            collapse()
                        assert page.evaluate('allStepsLoads') == 0
                        assert page.evaluate('allStepsPlayer===document.querySelector(".ns .previewViewport video")')
                        assert page.evaluate('allStepsPlayer.playbackRate') == 1.25

                page.set_viewport_size({'width': 1366, 'height': 768})
                # Translation review opens above the expanded configuration and applies once.
                step('transcript'); expand(2)
                original = page.get_by_label('Gốc câu 1', exact=True)
                original.fill('原文 giữ nguyên nội dung chưa lưu'); original.press('Tab')
                page.locator('.ns .studioSave').click()
                expect(page.locator('[data-save-status]')).to_have_attribute('data-dirty', 'false')
                collapse()
                page.locator('.stepActions').get_by_role('button', name='Dịch lại toàn bộ', exact=True).click()
                expand(4)
                review = page.locator('.popup:not(.quadDialog)')
                expect(review).to_be_visible()
                review.get_by_label('Áp dụng đề xuất câu 1', exact=True).check()
                review.get_by_role('button', name='Áp dụng bản dịch', exact=True).click()
                expect(review).to_have_count(0); expect(page.locator('.quadDialog')).to_be_visible()
                assert calls['translate'] == 1
                collapse()
                expect(page.get_by_label('Bản dịch câu 1', exact=True)).to_have_value('Bản dịch giả lập đã duyệt.')
                expand(4)
                page.locator('.quadDialog').get_by_role('button', name='Cấu hình AI dịch', exact=True).click()
                expect(page.locator('.popup:not(.quadDialog) h3')).to_have_text('Cấu hình AI trong Studio')
                page.keyboard.press('Escape'); expect(page.locator('.quadDialog')).to_be_visible()
                page.keyboard.press('Escape'); expect(page.locator('.quadDialog')).to_have_count(0)

                # Subtitle changes use the existing preview and a mocked render task.
                step('subtitles'); expand(4)
                size = page.locator('.quadDialog').get_by_label('Cỡ chữ', exact=True)
                size.evaluate("e=>{e.value='54';e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))}")
                page.locator('.ns .studioSave').click()
                expect(page.locator('[data-save-status]')).to_have_attribute('data-dirty', 'false')
                assert store.load(folder / 'bilingual.mp4')['studio_config']['style']['size'] == 54
                collapse()
                page.locator('.stepActions button').filter(has_text='Render thử').click()
                expand(2)
                expect(page.locator('.quadCard3 .job')).to_have_attribute('data-status', 'completed')
                expect(page.locator('.quadDialog')).to_be_visible()
                assert calls['preview'] == 1
                collapse()

                # A still-image branding dialog stacks over the configuration popup.
                step('branding'); expand(4)
                logo_bytes = io.BytesIO(); Image.new('RGBA', (120, 80), (240, 80, 40, 180)).save(logo_bytes, format='PNG')
                page.get_by_label('Tải logo PNG, WebP hoặc JPG', exact=True).set_input_files(
                    {'name': 'synthetic.png', 'mimeType': 'image/png', 'buffer': logo_bytes.getvalue()})
                expect(page.locator('[data-brand-status]')).to_contain_text('Đã tải logo')
                page.locator('.quadDialog').get_by_role('button', name='Mở ảnh preview để đặt logo', exact=True).click()
                still = page.locator('.brandingStillDialog')
                expect(still.locator('.brandingStillBackground')).to_be_visible()
                expect(still.locator('[data-brand-kind=logo]')).to_be_visible()
                logo = still.locator('[data-brand-kind=logo]'); box = logo.bounding_box()
                before = store.load(folder / 'bilingual.mp4')['studio_config']['branding']['logo']['x']
                page.mouse.move(box['x'] + box['width']/2, box['y'] + box['height']/2)
                page.mouse.down(); page.mouse.move(box['x'] + box['width']/2 - 45, box['y'] + box['height']/2 + 15, steps=5); page.mouse.up()
                still.get_by_role('button', name='Lưu vị trí', exact=True).click()
                expect(page.locator('[data-save-status]')).to_have_attribute('data-dirty', 'false')
                assert store.load(folder / 'bilingual.mp4')['studio_config']['branding']['logo']['x'] < before
                page.keyboard.press('Escape'); expect(still).to_have_count(0)
                expect(page.locator('.quadDialog')).to_be_visible()
                expect(page.locator('.quadDialog .brandStillOpen')).to_be_focused()
                page.keyboard.press('Escape')

                # Export completion updates while another panel is expanded; cleanup stacks above it.
                step('export'); expand(4)
                page.locator('.quadDialog').get_by_label('Tốc độ video', exact=True).fill('1.1')
                page.locator('.quadDialog').get_by_label('Tốc độ video', exact=True).dispatch_event('input')
                page.locator('.ns .studioSave').click()
                expect(page.locator('[data-save-status]')).to_have_attribute('data-dirty', 'false')
                assert store.load(folder / 'bilingual.mp4')['studio_config']['render']['speed'] == 1.1
                collapse()
                page.locator('.stepActions').get_by_role('button', name='Xuất video âm thanh gốc', exact=True).click()
                expand(4)
                expect(page.locator('.quadCard3 .job')).to_have_attribute('data-status', 'completed')
                expect(page.locator('.studioExportResult')).to_have_count(1, timeout=6000)
                assert page.locator('.studioExportResult').evaluate('e=>Number(getComputedStyle(e).zIndex)') < 80
                assert calls['export'] == 1
                collapse(); expand(3)
                page.locator('.quadDialog .jobDetail>summary').click()
                page.locator('.quadDialog').get_by_role('button', name='Duyệt và dọn dung lượng', exact=True).click()
                expect(page.locator('.popup:not(.quadDialog)')).to_be_visible()
                page.locator('.popup:not(.quadDialog)').get_by_role('button', name='Xem trước dung lượng', exact=True).click()
                expect(page.locator('.popup:not(.quadDialog)')).to_contain_text('old-preview.mp4')
                page.keyboard.press('Escape'); expect(page.locator('.quadDialog')).to_be_visible()
                page.keyboard.press('Escape')
                assert page.locator('.studioExportResult').evaluate('e=>Number(getComputedStyle(e).zIndex)') > 80
                page.locator('.studioExportResult').get_by_role('button', name='Đóng', exact=True).click()

                # Global legacy choice survives step switches and reload, preserving the old splitter preference.
                legacy_before = page.evaluate("localStorage.getItem('nambeo.studio.workspace.v2')")
                page.locator('.quadModeSwitch').get_by_role('button', name='Bố cục cũ', exact=True).click()
                for key in step_titles:
                    page.locator(f'.workflowTabs [data-step="{key}"]').click()
                    expect(page.locator('.quadModeSwitch')).to_be_visible()
                    expect(page.get_by_role('separator')).to_be_visible()
                assert page.evaluate("localStorage.getItem('nambeo.studio.workspace.v2')") == legacy_before
                page.reload(wait_until='domcontentloaded'); page.evaluate("openSubtitleStudio('bilingual.mp4','branding')")
                expect(page.get_by_role('separator')).to_be_visible()
                page.locator('.quadModeSwitch').get_by_role('button', name='4 ô', exact=True).click()
                expect(page.locator('.quadWorkspace')).to_be_visible()
                page.locator('.ns').get_by_role('button', name='Quay lại thư viện', exact=True).click()

                # Empty projects have the same workspace and can report a mocked recognition error.
                job = None
                page.evaluate("openSubtitleStudio('empty.mp4','transcript')")
                for key in step_titles:
                    step(key); expect(page.locator('.emptyCues')).to_be_visible()
                step('transcript')
                page.locator('.stepActions').get_by_role('button', name='Tạo bản thoại', exact=True).click()
                expand(1)
                expect(page.locator('.quadCard3 .job')).to_have_attribute('data-status', 'error')
                expect(page.locator('.quadCard3 .job')).to_contain_text('nhận diện lỗi')
                assert calls['transcribe'] == 1
                collapse()

                page.locator('.ns').get_by_role('button', name='Quay lại thư viện', exact=True).click()
                job = None
                page.evaluate("openSubtitleStudio('portrait.mp4','transcript')")
                expect(page.locator('.quadWorkspace')).to_be_visible()
                page.wait_for_function("document.querySelector('.ns .previewViewport video').readyState>=2")
                page.evaluate('''() => {
                    window.portraitPlayer=document.querySelector('.ns .previewViewport video');
                    window.portraitLoads=0;portraitPlayer.addEventListener('loadstart',()=>portraitLoads++);
                }''')
                for width, height in [(1366, 768), (1920, 1080), (390, 844)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    for key in step_titles:
                        step(key); page.wait_for_timeout(100)
                        assert not geometry()['overflow'], (key, width, geometry())
                        expand(1)
                        ratio = page.locator('.previewViewport>.stage:not(.renderedStage)').evaluate('e=>e.clientWidth/e.clientHeight')
                        assert abs(ratio - 9/16) < .015, (key, width, ratio)
                        collapse()
                        assert page.evaluate('portraitLoads') == 0
                        assert page.evaluate('portraitPlayer===document.querySelector(".ns .previewViewport video")')
                    page.locator('.ns').evaluate('e=>e.scrollTop=0')
                    page.locator('.ns').screenshot(path=str(runtime / f'studio-all-portrait-{width}.png'))
                assert not errors, errors
                print('PASS: Studio four-panel layout, media/state, modals, jobs, responsive and legacy. Mock calls:', calls)
                browser.close()
        finally:
            service.should_exit = True; thread.join(timeout=10)
            server.current_download_dir, server.studio_service.database = old_root, old_db


if __name__ == '__main__':
    main()
