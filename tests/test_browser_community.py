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
    chrome=Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
    if not chrome.exists():pytest.skip('Installed isolated Chromium executable unavailable; browser NOTRUN')
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
            page.goto(base)
            page.locator('#workspace').fill('browser-fixture');page.locator('#username').fill('owner')
            page.locator('#password').fill('fixture-password-not-real');page.locator('#login button').click()
            page.locator('#dashboard').wait_for(state='visible')
            page.locator('#nav-agents').click();page.locator('#agent-name').fill('Fixture Blue');page.locator('#agent-handle').fill('fixture-blue')
            page.locator('#register-agent button[type=submit]').click()
            playwright.expect(page.locator('#agents-list')).to_contain_text('Fixture Blue')
            page.locator('#nav-rooms').click();page.locator('#topic').fill('Synthetic shared text');page.locator('#mode').select_option('co_creation')
            page.locator('#create button').click();page.locator('.room-card',has_text='Synthetic shared text').click()
            playwright.expect(page.locator('#room-state')).to_contain_text('준비 중')
            page.locator('[data-tab=participants]').click()
            page.locator('#invite-agent-id').select_option(label='Fixture Blue @fixture-blue');page.locator('#invite-agent button[type=submit]').click()
            playwright.expect(page.locator('#members')).to_contain_text('초대 대기')
            participant=page.locator('.member-card',has_text='Fixture Blue')
            participant.get_by_role('button',name='참여 안내',exact=True).click()
            assert 'Synthetic shared text' in page.locator('#onboarding-prompt').input_value()
            assert 'TALKABOUT_TOKEN' not in page.locator('#onboarding-prompt').input_value()
            config=page.locator('#onboarding-config').input_value();assert base in config
            page.locator('#onboarding-close').click()
            participant.get_by_role('button',name='1시간 토큰 발급',exact=True).click()
            page.locator('#credential-dialog').wait_for(state='visible')
            raw=page.locator('#credential-value').input_value()
            assert raw and page.locator('#credential-value').get_attribute('type')=='password'
            assert raw not in config and all(raw not in url for url in urls)
            page.locator('#credential-close').click();assert page.locator('#credential-value').input_value()==''
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
            page.locator('#refresh').click();page.locator('[data-tab=discussion]').click()
            playwright.expect(page.locator('#messages')).to_contain_text('Synthetic fixture contribution: Synthetic shared text')
            page.locator('[data-tab=artifacts]').click()
            playwright.expect(page.locator('#artifacts')).to_contain_text('Synthetic fixture contribution: Synthetic shared text')
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
            browser.close()
    finally:
        if client:client.close()
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
