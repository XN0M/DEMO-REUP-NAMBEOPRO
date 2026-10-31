"""Functional browser checks with synthetic API responses; no live account access."""
import json
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright
from server import app


def main():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0)); sock.listen(128)
    url = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    # Disable lifespan: tests must never start real channel monitoring.
    server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    checks, errors = [], []
    state = {'channels': [], 'videos': [], 'mode': 'complete', 'token':'synthetic-token',
             'page_id':'123', 'page_name':'Test Page', 'fb_success':False, 'saved_tokens':[], 'delete_failure':False,
             'legacy_status':0, 'legacy_unavailable':False, 'legacy_failure_id':None, 'tasks':{}, 'delete_requests':[]}
    def api(route):
        request = route.request
        path, method = urlsplit(request.url).path, request.method
        data = request.post_data_json if request.post_data else {}
        result, status = {}, 200
        if path == '/api/channels' and method == 'POST':
            channel = dict(data, id='test-channel', sec_uid='test-profile', last_status=None, active=True)
            state['channels'] = [channel]; result = {'channel':channel}
        elif path.endswith('/check'):
            ch = state['channels'][0]
            if state['mode'] == 'error':
                ch.update(last_status='error',last_error={'code':'douyin_verification','message':'Douyin giới hạn phiên khách'})
                result, status = {'detail':'Douyin giới hạn phiên khách','code':'douyin_verification'}, 502
            else:
                state['videos'] = [dict(uuid=str(i),video_id=str(i),platform='douyin',title='Test '+str(i),
                    channel_uuid='test-channel',channel_name='Test',status='pending',collection='Chung',
                    url='https://www.douyin.com/video/'+str(i),published='2026-10-26') for i in range(20)]
                partial = state['mode'] == 'partial'
                ch.update(last_status='partial' if partial else 'complete', last_error=None,
                          last_success='2026-10-26T00:00:00Z', last_message='Kiểm tra chưa đầy đủ' if partial else '')
                result = {'complete':not partial,'new_videos_found':20,'videos':state['videos'],
                          'message':ch['last_message']}
        elif path == '/api/channels/videos/delete-batch':
            state['delete_requests'].append(('batch',data['video_uuids']))
            if state['legacy_status']:
                result, status = {'detail':'Method Not Allowed'}, state['legacy_status']
            elif state['delete_failure']:
                result, status = {'detail':'Synthetic deletion failure'}, 503
            else:
                requested = set(data['video_uuids'])
                removed = [v['uuid'] for v in state['videos'] if v['uuid'] in requested and v['status'] != 'downloading']
                skipped = [v['uuid'] for v in state['videos'] if v['uuid'] in requested and v['status'] == 'downloading']
                state['videos'] = [v for v in state['videos'] if v['uuid'] not in removed]
                result = {'removed_count':len(removed),'removed_ids':removed,'skipped_ids':skipped,
                          'missing_ids':list(requested-set(removed)-set(skipped))}
        elif path.startswith('/api/channels/pending/') and method == 'DELETE':
            uuid = path.rsplit('/',1)[-1]
            state['delete_requests'].append(('legacy',uuid))
            row = next((v for v in state['videos'] if v['uuid']==uuid),None)
            if state['legacy_unavailable']:
                result, status = {'detail':'Method Not Allowed'}, 405
            elif uuid == state['legacy_failure_id']:
                result, status = {'detail':'Synthetic legacy deletion failure'}, 503
            elif row:
                row['status']='dismissed'  # Old backend keeps the record.
                result = {'dismissed':True}
            else:
                result, status = {'detail':'Video pending không tồn tại'}, 404
        elif path == '/api/tasks': result = state['tasks']
        elif path == '/api/channels': result = {'channels':state['channels']}
        elif path == '/api/channels/videos': result = {'videos':state['videos']}
        elif path == '/api/channels/pending': result = {'pending':state['videos']}
        elif path == '/api/fb/config':
            if method == 'POST':
                token = data.get('page_access_token') or ''
                state['saved_tokens'].append(token)
                if token: state['token'] = token
                state['page_name'] = data.get('page_name','')
                result = {'saved':True}
            else:
                result = {'page_id':state['page_id'],'page_name':state['page_name'],'token_set':True,'token_hint':'••••••••'}
        elif path == '/api/fb/test': result = {'ok':True,'page_id':'123','page_name':'Verified Page','fans':10}
        elif path == '/api/fb/pages': result = {'pages':[{'page_id':'123','page_name':'Test Page','token_set':True}]}
        elif path == '/api/fb/pages/123/post':
            result, status = ({'success':True,'video_id':'987','page_id':'123'} if state['fb_success'] else
                              {'detail':'Chưa xác định kết quả đăng. Kiểm tra Page trước khi đăng lại.','code':'facebook_outcome_unknown'}), (200 if state['fb_success'] else 504)
        elif path == '/api/collections': result = {'collections':['Chung']}
        elif path == '/api/config': result = {'download_dir':'test','default_dir':'test'}
        elif path == '/api/history': result = {'videos':[]}
        elif 'voices' in path: result = {'voices':[]}
        route.fulfill(status=status, content_type='application/json', body=json.dumps(result))
    try:
        deadline=time.time()+10
        while not server.started and time.time()<deadline: time.sleep(.05)
        assert server.started, 'Local UI server did not start'
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1000})
            # External presentation assets are stubbed; this verifies behavior, not styling.
            context.route('https://cdn.tailwindcss.com/**',lambda r:r.fulfill(content_type='text/javascript',body=''))
            context.route('https://unpkg.com/**',lambda r:r.fulfill(content_type='text/javascript',body='window.lucide={createIcons(){}};'))
            context.route('**/api/**',api)
            context.add_init_script('window.EventSource = class { close() {} };')
            page=context.new_page()
            accept_dialog = [True]
            dialogs = []
            def on_dialog(dialog):
                dialogs.append(dialog.message)
                dialog.accept() if accept_dialog[0] else dialog.dismiss()
            page.on('dialog', on_dialog)
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(url,wait_until='domcontentloaded')
            page.add_style_tag(content='.hidden { display:none !important; }')
            page.wait_for_function('typeof openAddChannelModal === "function" && typeof switchTab === "function"')
            page.evaluate("switchTab('monitor')")
            page.evaluate('openAddChannelModal()')
            page.locator('#addChannelUrl').fill('https://www.douyin.com/user/test-profile')
            page.locator('#addChannelName').fill('Test Douyin')
            page.locator('#addChannelIntervalVal').fill('15')
            page.evaluate('submitAddChannel()')
            page.wait_for_function('document.querySelectorAll(".nb-channel-item").length === 1')
            page.wait_for_function('monitorVideos.length === 20')
            assert state['channels'][0]['platform']=='douyin'
            checks.append('add_douyin_and_twenty_initial_videos')
            state['mode']='error'
            page.evaluate("checkChannelNow('test-channel', null)")
            assert 'Douyin giới hạn phiên khách' in page.locator('#monitorChannelsList').inner_text()
            checks.append('channel_error_visible')
            state['mode']='partial'
            page.evaluate("checkChannelNow('test-channel', null)")
            assert 'Kiểm tra chưa đầy đủ' in page.locator('#monitorChannelsList').inner_text()
            checks.append('retry_and_partial_status')
            # Legacy config is hidden by the multi-Page UI; exercise its retained
            # compatibility handler. The visible Page workflow has its own UI suite.
            page.locator('#fbTokenInput').evaluate("e=>e.value='new-synthetic-token'")
            page.evaluate('testFbConnection()')
            assert state['saved_tokens'][-2:]==['new-synthetic-token','']
            assert state['token']=='new-synthetic-token'
            assert page.locator('#fbTokenInput').input_value()==''
            checks.append('facebook_save_before_test_and_keep_token')
            page.evaluate("openFbPostModal('sample.mp4','Test')")
            page.locator('#fbPostPage').select_option('123')
            page.evaluate('submitFbPost()')
            page.wait_for_function('!document.getElementById("btnSubmitFbPost").disabled')
            assert 'Chưa xác định' in page.locator('#fbPostStatus').inner_text()
            assert page.locator('#fbPostModal').is_visible()
            state['fb_success']=True
            page.evaluate('submitFbPost()')
            page.wait_for_function('document.getElementById("fbPostStatus").textContent.includes("Facebook đã nhận")')
            page.evaluate('closeFbPostModal()')
            checks.append('facebook_unknown_then_confirmed_outcome')

            # Reproduce previously invisible actions on dismissed rows, then verify
            # deletion scopes and preservation of in-progress/other-channel items.
            state['videos'][0]['status']='dismissed'
            state['videos'][1]['status']='downloaded'
            state['videos'][2]['status']='downloading'
            state['videos'].append(dict(state['videos'][3], uuid='other',video_id='other',channel_uuid='other-channel'))
            page.evaluate('loadMonitorVideos()')
            assert page.locator('#videoCard-0').count()==0
            assert page.locator('[data-monitor-remove="1"]').count()==1
            assert page.locator('[data-monitor-select="2"]').is_disabled()
            checks.append('hidden_legacy_rows_and_delete_controls_for_downloaded_items')

            page.evaluate("selectChannelView('test-channel'); setMonitorStatusFilter('pending'); selectAllMonitorVideos(true)")
            assert page.evaluate('selectedMonitorVideoIds.size')==17
            accept_dialog[0]=False
            before=len(state['videos'])
            page.evaluate("deleteMonitorList('filtered')")
            assert len(state['videos'])==before
            accept_dialog[0]=True
            page.evaluate("selectAllMonitorVideos(false); toggleMonitorVideoSelection('3',true)")
            state['delete_failure']=True
            page.evaluate("deleteMonitorList('selected')")
            assert len(state['videos'])==before
            assert page.evaluate("selectedMonitorVideoIds.has('3')")
            state['delete_failure']=False
            page.evaluate("deleteMonitorList('selected')")
            assert not any(v['uuid']=='3' for v in state['videos'])
            assert page.evaluate('selectedMonitorVideoIds.size')==0
            checks.append('selection_cancel_failure_and_retry')

            page.evaluate("setMonitorStatusFilter('all'); deleteMonitorList('downloaded')")
            assert not any(v['uuid']=='1' for v in state['videos'])
            page.evaluate("deleteMonitorVideo('4')")
            assert not any(v['uuid']=='4' for v in state['videos'])
            page.evaluate("setMonitorStatusFilter('pending'); deleteMonitorList('filtered')")
            assert {v['uuid'] for v in state['videos']}=={'0','2','other'}
            page.evaluate("setMonitorStatusFilter('all')")
            assert page.locator('#btnClearMonitorView').is_disabled()
            assert page.locator('#monitorViewCountBadge').inner_text()=='1'
            assert any('Video đã tải vẫn được giữ' in text for text in dialogs)
            checks.append('delete_downloaded_single_and_filtered_without_other_channel_or_active_items')

            def legacy_rows(*ids):
                return [dict(uuid=uuid,video_id=uuid,platform='douyin',title='Legacy '+uuid,
                             channel_uuid='test-channel',channel_name='Test',status='pending',collection='Chung',
                             url='https://www.douyin.com/video/'+uuid,published='2026-10-26') for uuid in ids]

            state['legacy_status']=405
            state['videos'].extend(legacy_rows('legacy-a','legacy-b','legacy-busy'))
            state['tasks']={'legacy-busy':{'status':'downloading'}}
            page.evaluate("loadMonitorVideos()")
            page.evaluate("setMonitorStatusFilter('pending'); deleteMonitorList('filtered')")
            assert all(v['status']=='dismissed' for v in state['videos'] if v['uuid'] in ('legacy-a','legacy-b'))
            assert page.locator('#videoCard-legacy-a').count()==0
            assert page.locator('#videoCard-legacy-busy').count()==1
            assert ('legacy','legacy-busy') not in state['delete_requests']
            page.evaluate('loadMonitorVideos()')
            assert page.locator('#videoCard-legacy-b').count()==0
            checks.append('405_fallback_to_legacy_and_preserve_active_downloads')

            # Partial legacy writes keep accurate progress and allow a safe retry.
            state['videos'].extend(legacy_rows('legacy-c','legacy-d'))
            state['legacy_failure_id']='legacy-d'
            page.evaluate('loadMonitorVideos()')
            page.evaluate("selectAllMonitorVideos(false); toggleMonitorVideoSelection('legacy-c',true); toggleMonitorVideoSelection('legacy-d',true); deleteMonitorList('selected')")
            assert page.locator('#videoCard-legacy-c').count()==0
            assert page.evaluate("selectedMonitorVideoIds.has('legacy-d')")
            state['legacy_failure_id']=None
            page.evaluate("deleteMonitorList('selected')")
            assert page.locator('#videoCard-legacy-d').count()==0
            checks.append('legacy_partial_failure_and_retry')

            state['legacy_status']=404
            state['legacy_unavailable']=True
            state['videos'].extend(legacy_rows('legacy-e'))
            page.evaluate('monitorUseLegacyDelete=false; loadMonitorVideos()')
            page.evaluate("deleteMonitorVideo('legacy-e')")
            assert page.locator('#videoCard-legacy-e').count()==1
            state['legacy_unavailable']=False
            page.evaluate('monitorUseLegacyDelete=false')
            page.evaluate("deleteMonitorVideo('legacy-e')")
            assert page.locator('#videoCard-legacy-e').count()==0
            checks.append('404_fallback_and_no_false_success_when_legacy_route_missing')
            assert not errors, errors
            context.close();browser.close()
        print(json.dumps({'ok':True,'checks':checks,'page_errors':errors},ensure_ascii=True))
    finally:
        server.should_exit=True
        thread.join(10)
        sock.close()


if __name__=='__main__':
    main()
