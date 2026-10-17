"""Actual Chromium against a new memory fixture; never an operating user session or model."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
import pytest
import httpx
from talkabout.worker import APIClient, Journal, SyntheticRuntime, Worker


def test_browser_register_invite_setup_start_and_text_artifact(tmp_path):
    playwright=pytest.importorskip('playwright.sync_api')
    chrome=Path(os.environ.get('TALKABOUT_TEST_CHROMIUM') or 'C:/Program Files/Google/Chrome/Application/chrome.exe')
    if not chrome.exists():pytest.skip('Browser NOTRUN: set TALKABOUT_TEST_CHROMIUM for an isolated executable or install test Chrome')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base='http://127.0.0.1:'+str(port)
    env={**os.environ,'TALKABOUT_FIXTURE_PORT':str(port)}
    server=subprocess.Popen([sys.executable,'tests/browser_fixture_app.py'],env=env,stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    client=None
    try:
        for _ in range(60):
            try:
                if httpx.get(base+'/health',timeout=.5,trust_env=False).status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.1)
        else:pytest.fail('isolated_browser_fixture_startup_failed')
        with playwright.sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=str(chrome),headless=True,args=['--disable-sync','--disable-background-networking'])
            context=browser.new_context(viewport={'width':1400,'height':1000})
            page=context.new_page();errors=[];urls=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('dialog',lambda dialog:dialog.accept())
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
            page.on('request',lambda request:urls.append(request.url))
            shots=Path(__file__).resolve().parents[2]/'release-artifacts';shots.mkdir(exist_ok=True)
            page.goto(base)
            page.locator('#workspace').fill('browser-fixture');page.locator('#username').fill('owner')
            page.locator('#password').fill('fixture-password-not-real');page.locator('#login button').click()
            page.locator('#dashboard').wait_for(state='visible')
            page.locator('#nav-agents').click();page.locator('#agent-name').fill('Fixture Blue');page.locator('#agent-handle').fill('fixture-blue')
            page.locator('#register-agent button[type=submit]').click()
            playwright.expect(page.locator('#agents-list')).to_contain_text('Fixture Blue')
            # Registration opens the new connection wizard, without issuing a credential.
            page.locator('#connection-close').click()
            page.locator('#nav-rooms').click();page.locator('#topic').fill('Synthetic shared text');page.locator('#mode').select_option('co_creation')
            page.locator('#create button').click();page.locator('.room-card',has_text='Synthetic shared text').click()
            playwright.expect(page.locator('#room-state')).to_contain_text('준비 중')
            page.locator('[data-tab=participants]').click()
            page.locator('#invite-agent-id').select_option(label='Fixture Blue @fixture-blue');page.locator('#invite-agent button[type=submit]').click()
            playwright.expect(page.locator('#members')).to_contain_text('초대 대기')
            page.screenshot(path=str(shots/'community-invitation-ko.png'),full_page=True)
            participant=page.locator('#members .member-card',has_text='Fixture Blue')
            participant.get_by_role('button',name='초대 수락·연결 안내 열기',exact=True).click()
            assert 'Synthetic shared text' in page.locator('#onboarding-prompt').input_value()
            assert 'TALKABOUT_TOKEN' not in page.locator('#onboarding-prompt').input_value()
            config=page.locator('#onboarding-config').input_value();assert base in config
            playwright.expect(page.get_by_role('dialog',name='이 방의 참여·연결 안내',exact=True)).to_be_visible()
            commands=page.locator('#onboarding-commands').inner_text()
            room_id=page.evaluate('new URLSearchParams(location.search).get("room")')
            expected_commands=[
                'python -m talkabout.cli check --config agent.local.json --prompt-token',
                'python -m talkabout.cli join --config agent.local.json --room '+room_id+' --acknowledge-private --prompt-token',
                'python -m talkabout.cli connect --config agent.local.json --runtime codex --auth-mode native --approve-external --max-model-calls 4 --steps 1000 --max-seconds 300 --prompt-token',
            ]
            assert page.locator('#onboarding-action').input_value()=='start'
            assert [line for line in commands.splitlines() if line.startswith('python -m talkabout.cli ')]==expected_commands
            assert commands.index('cli connect') < commands.index('브라우저 관리 탭')
            page.locator('#onboarding-commands').scroll_into_view_if_needed()
            page.screenshot(path=str(shots/'community-guide-ko.png'),full_page=True)
            page.locator('#onboarding-close').click();page.locator('#language').click()
            participant.get_by_role('button',name='Open acceptance and connection guide',exact=True).click()
            playwright.expect(page.get_by_role('dialog',name='Room participation and connection',exact=True)).to_be_visible()
            commands=page.locator('#onboarding-commands').inner_text()
            assert page.locator('#onboarding-action').input_value()=='start'
            assert [line for line in commands.splitlines() if line.startswith('python -m talkabout.cli ')]==expected_commands
            assert commands.index('cli connect') < commands.index('Start discussion in the browser Management tab')
            page.locator('#onboarding-commands').scroll_into_view_if_needed()
            page.screenshot(path=str(shots/'community-guide-en.png'),full_page=True)
            page.locator('#onboarding-close').click();page.locator('#language').click()
            participant.get_by_role('button',name='초대 수락·연결 안내 열기',exact=True).click()
            page.locator('#onboarding-close').click()
            participant.get_by_role('button',name='1시간 토큰 발급',exact=True).click()
            page.locator('#credential-dialog').wait_for(state='visible')
            raw=page.locator('#credential-value').input_value()
            assert raw and page.locator('#credential-value').get_attribute('type')=='password'
            assert raw not in config and all(raw not in url for url in urls)
            page.locator('#credential-close').click();assert page.locator('#credential-value').input_value()==''
            participant.get_by_role('button',name='1시간 토큰 발급',exact=True).click()
            page.locator('#credential-dialog').wait_for(state='visible');page.locator('#credential-close').click()
            participant.get_by_role('button',name='자격증명 상태',exact=True).click()
            playwright.expect(participant.locator('.credential-list li')).to_have_count(2)
            playwright.expect(participant.locator('.credential-list')).to_contain_text('room:read')
            assert raw not in participant.locator('.credential-list').inner_text()
            page.screenshot(path=str(shots/'community-credential-metadata-ko.png'),full_page=True)
            client=APIClient(base,raw,base)
            invitation=client.request('GET','agents/me/invitations')[0]
            rid=invitation['room_id']
            client.request('POST','rooms/'+rid+'/memberships/me/accept',{'expected_revision':invitation['membership_revision'],'acknowledge_private':True},str(uuid.uuid4()))
            client.request('PUT','agents/me/connection',{'runtime':'synthetic','state':'idle'},str(uuid.uuid4()))
            page.locator('#refresh').click()
            playwright.expect(page.locator('#members')).to_contain_text('최근 인증 통신')
            page.locator('[data-tab=management]').click();page.locator('[data-action=start]').click()
            playwright.expect(page.locator('#room-state')).to_contain_text('진행 중')
            journal=Journal(tmp_path/'worker.sqlite3')
            try:
                worker=Worker(client,journal,SyntheticRuntime())
                for _ in range(3):assert worker.step()=='completed'
            finally:journal.close()
            page.locator('#refresh').click();page.locator('[data-tab=management]').click()
            page.locator('[data-action=pause]').click()
            playwright.expect(page.locator('#workflow')).to_contain_text('일시정지')
            page.screenshot(path=str(shots/'community-paused-ko.png'),full_page=True)
            page.locator('[data-action=resume]').click()
            page.locator('[data-tab=discussion]').click()
            playwright.expect(page.locator('#messages')).to_contain_text('Synthetic fixture contribution: Synthetic shared text')
            page.locator('[data-tab=artifacts]').click()
            playwright.expect(page.locator('#artifacts')).to_contain_text('Synthetic fixture contribution: Synthetic shared text')
            page.locator('[data-tab=discussion]').click()
            playwright.expect(page.locator('#workflow')).to_contain_text('채택을 기다립니다')
            page.screenshot(path=str(shots/'community-awaiting-adoption-ko.png'),full_page=True)
            page.locator('[data-tab=artifacts]').click()
            assert not errors,'browser_page_errors'
            page.locator('#language').click()
            playwright.expect(page.locator('#room-title')).to_contain_text('Synthetic shared text')
            playwright.expect(page.locator('[data-tab=participants]')).to_have_text('Participants')
            page.locator('#language').click()
            shots=Path(__file__).resolve().parents[2]/'release-artifacts';shots.mkdir(exist_ok=True)
            page.screenshot(path=str(shots/'community-flow-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),'mobile_horizontal_overflow'
            page.screenshot(path=str(shots/'community-flow-mobile.png'),full_page=True)
            page.locator('#artifacts').get_by_role('button',name='이 버전 채택',exact=True).click()
            playwright.expect(page.locator('#room-state')).to_contain_text('종료')
            assert not errors,'browser_page_errors'
            # A real server account with no registry/credential management permissions.
            limited=browser.new_context(viewport={'width':1400,'height':1000})
            manager=limited.new_page();manager_errors=[];manager_calls=[]
            manager.on('pageerror',lambda error:manager_errors.append(str(error)))
            manager.on('request',lambda request:manager_calls.append(request.url))
            manager.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
            manager.goto(base);manager.locator('#workspace').fill('browser-manager-fixture');manager.locator('#username').fill('manager')
            manager.locator('#password').fill('fixture-password-not-real');manager.locator('#login button').click()
            manager.locator('.room-card',has_text='Synthetic manager view').wait_for()
            playwright.expect(manager.locator('.room-card')).to_contain_text('준비 중')
            manager.locator('#language').click()
            playwright.expect(manager.locator('.room-card')).to_contain_text('Preparing')
            manager.screenshot(path=str(shots/'community-room-list-en.png'),full_page=True)
            manager.locator('.room-card',has_text='Synthetic manager view').click()
            manager.locator('[data-tab=participants]').click()
            playwright.expect(manager.locator('#detail')).to_be_visible()
            playwright.expect(manager.locator('#nav-agents')).to_be_hidden()
            playwright.expect(manager.locator('#invite-agent')).to_be_hidden()
            assert all(not url.endswith('/api/v1/agents') and '/credentials' not in url for url in manager_calls)
            assert manager.request.get(base+'/api/v1/agents').status==403
            manager.screenshot(path=str(shots/'community-limited-manager-en.png'),full_page=True)
            manager_room=manager.evaluate('new URLSearchParams(location.search).get("room")')
            failed_path='**/api/v1/rooms/'+manager_room
            manager.route(failed_path,lambda route:route.fulfill(status=503,json={'code':'synthetic_unavailable'}))
            manager.locator('#refresh').click()
            playwright.expect(manager.locator('#notice')).to_contain_text('temporarily unavailable')
            playwright.expect(manager.locator('#detail')).to_be_visible()
            manager.screenshot(path=str(shots/'community-transient-error-en.png'),full_page=True)
            manager.locator('#language').click()
            playwright.expect(manager.locator('#notice')).to_contain_text('마지막 확인 화면')
            manager.screenshot(path=str(shots/'community-transient-error-ko.png'),full_page=True)
            manager.unroute(failed_path);manager.locator('#refresh').click()
            playwright.expect(manager.locator('#notice')).to_have_text('')
            playwright.expect(manager.locator('#room-title')).to_have_text('Synthetic manager view')
            manager.route(failed_path,lambda route:route.fulfill(status=403,json={'code':'forbidden'}))
            manager.locator('#refresh').click()
            playwright.expect(manager.locator('#detail')).to_be_hidden()
            playwright.expect(manager.locator('#room-title')).to_have_text('')
            manager.screenshot(path=str(shots/'community-access-revoked-ko.png'),full_page=True)
            assert not manager_errors,'manager_browser_page_errors'
            limited.close();browser.close()
    finally:
        if client:client.close()
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
