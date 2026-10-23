"""Beginner journey through real Chrome; no runtime or credential issuance."""
import os,socket,subprocess,sys,time
from pathlib import Path
import httpx
import pytest

@pytest.mark.parametrize('language,width',[('ko',1400),('en',390)])
def test_beginner_register_invite_acceptance_guide_reload(tmp_path,language,width):
    pw=pytest.importorskip('playwright.sync_api')
    chrome=Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
    if not chrome.exists():pytest.skip('Chrome executable unavailable')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base='http://127.0.0.1:'+str(port)
    with (tmp_path/'server.log').open('wb') as log:
        server=subprocess.Popen([sys.executable,'tests/readiness_fixture_app.py'],env={**os.environ,'TALKABOUT_FIXTURE_PORT':str(port)},stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            for _ in range(80):
                try:
                    if httpx.get(base+'/health',timeout=.5,trust_env=False).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:pytest.fail('isolated_readiness_fixture_startup_failed')
            with pw.sync_playwright() as runner:
                browser=runner.chromium.launch(executable_path=str(chrome),headless=True,args=['--disable-sync','--disable-background-networking'])
                context=browser.new_context(viewport={'width':width,'height':1000});page=context.new_page();errors=[];writes=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('request',lambda request:writes.append(request.url) if request.method=='POST' else None)
                page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
                page.goto(base);page.locator('#workspace').fill('readiness-fixture');page.locator('#username').fill('owner');page.locator('#password').fill('fixture-password-not-real');page.locator('#login button').click()
                page.locator('#dashboard').wait_for(state='visible')
                if language=='en':page.locator('#language').click()
                page.locator('#nav-agents').click();page.locator('#agent-runtime').select_option('claude')
                page.locator('#agent-name').fill('Synthetic Beginner');page.locator('#agent-handle').fill('synthetic-beginner')
                page.locator('#register-agent button[type=submit]').click()
                page.locator('#connection-wizard').wait_for(state='visible')
                assert page.locator('#connection-runtime').input_value()=='claude'
                pw.expect(page.locator('#journey-status')).to_contain_text('Not connected' if language=='en' else '미연결')
                assert 'connect=' in page.url
                rid=page.request.get(base+'/api/v1/rooms').json()[0]['id']
                page.locator('#connection-room').select_option(rid);page.locator('#journey-next').click()
                page.locator('#invite-agent-id').select_option(label='Synthetic Beginner @synthetic-beginner');page.locator('#invite-agent button[type=submit]').click()
                pw.expect(page.locator('#members')).to_contain_text('Invited' if language=='en' else '초대 완료 · 수락 대기')
                page.locator('#nav-agents').click();page.locator('#agents-list button').first.click()
                page.locator('#connection-room').select_option(rid)
                pw.expect(page.locator('#journey-status')).to_contain_text('Invitation pending' if language=='en' else '초대대기')
                page.locator('#journey-next').click();page.locator('#onboarding-dialog').wait_for(state='visible')
                commands=page.locator('#onboarding-commands').inner_text()
                assert 'cli join' in commands and '--room '+rid in commands and '--runtime claude' in commands
                page.reload();page.locator('#connection-wizard').wait_for(state='visible')
                # Language is deliberately not part of authenticated evidence or persisted consent.
                if language=='en':page.locator('#language').click()
                pw.expect(page.locator('#journey-status')).to_contain_text('Invitation pending' if language=='en' else '초대대기')
                assert page.locator('#connection-runtime').input_value()=='claude'
                page.screenshot(path=str(tmp_path/('readiness-'+language+'.png')),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert sum(url.endswith('/api/v1/agents') for url in writes)==1
                assert not any('/credentials' in url or '/runtime-checks' in url or '/controls' in url for url in writes)
                assert not errors
                context.close();browser.close()
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=3)
