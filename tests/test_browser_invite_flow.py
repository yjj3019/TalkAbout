"""Real Chrome/API/MemoryStore; clipboard and one explicit failure are synthetic."""
import json
from pathlib import Path
import re

import pytest


@pytest.mark.parametrize('language,width',[('ko',1400),('en',390)])
def test_browser_room_invitation_single_flow(request,language,width):
    path=request.config.getoption('--invite-browser-config')
    if not path:
        pytest.skip('NOTRUN: explicit owned guarded synthetic browser fixture required')
    config=json.loads(Path(path).read_text(encoding='utf-8'))
    assert config['synthetic_memory_fixture'] is True
    base=config['base_url']
    assert re.fullmatch(r'http://127\.0\.0\.1:[0-9]{4,5}',base) and not base.endswith(':18081')
    import httpx
    marker=httpx.get(base+'/__invite_fixture__',trust_env=False).json()
    assert marker=={'synthetic_memory_fixture':True,'fixture_id':config['fixture_id']}
    pw=pytest.importorskip('playwright.sync_api')
    from talkabout.worker import APIClient
    client=None;secret=None
    with pw.sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=config['chrome'],headless=True,
            args=['--disable-sync','--disable-background-networking'])
        try:
            context=browser.new_context(viewport={'width':width,'height':1000},accept_downloads=True)
            # Never touch the user's OS clipboard, even when testing successful copy.
            context.add_init_script("window.syntheticCopies=[];window.syntheticClipboardFailure=false;Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>{if(window.syntheticClipboardFailure)throw new Error('synthetic_clipboard_denied');window.syntheticCopies.push(value);}}});")
            page=context.new_page();errors=[];posts=[];confirmations=[]
            page.on('pageerror',lambda error:errors.append(True))
            page.on('request',lambda r:posts.append(r.url.removeprefix(base)) if r.method=='POST' else None)
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
            page.on('dialog',lambda dialog:(confirmations.append(dialog.message),dialog.accept()))
            page.goto(base)
            page.locator('#workspace').fill('invite-browser-fixture');page.locator('#username').fill('owner')
            page.locator('#password').fill('fixture-password-not-real');page.locator('#login button').click()
            page.locator('#dashboard').wait_for(state='visible')
            if language=='en':page.locator('#language').click()
            topic='Synthetic single flow '+language
            page.locator('#topic').fill(topic);page.locator('#mode').select_option('co_creation')
            page.locator('#create button').click();page.locator('.room-card',has_text=topic).click()
            page.locator('[data-tab=participants]').click()
            page.locator('#invite-agent-id').select_option('__new')
            name='Synthetic participant '+language
            page.locator('#invite-new-name').fill(name);page.locator('#invite-new-client').select_option('claude')
            assert page.locator('#invite-submit').inner_text()==('초대하고 연결' if language=='ko' else 'Invite and connect')
            page.locator('#invite-submit').click()
            pw.expect(page.locator('#onboarding-dialog')).to_be_visible()
            pw.expect(page.locator('#invite-summary')).to_contain_text(topic)
            pw.expect(page.locator('#invite-summary')).to_contain_text(name)
            pw.expect(page.locator('#invite-client')).to_have_value('claude')
            # On a loopback fixture the invite button is "Invite and connect": one explicit
            # confirmation issues the room-scoped credential, hands the invite to the helper
            # through the clipboard and leaves the manual download/copy step ready.
            prepare='설정 파일 받고 명령 복사' if language=='ko' else 'Download settings and copy commands'
            pw.expect(page.locator('#invite-next')).to_have_text(prepare)
            pw.expect(page.locator('#invite-status')).to_contain_text('수락' if language=='ko' else 'awaiting acceptance')
            assert not page.locator('#invite-advanced').evaluate('(node)=>node.open')
            assert sum(p=='/api/v1/agents' for p in posts)==1
            assert sum(p.endswith('/memberships') for p in posts)==1
            assert sum(p.endswith('/credentials') for p in posts)==1
            assert len(confirmations)==1 and topic in confirmations[0] and name in confirmations[0]
            rid=page.evaluate('new URLSearchParams(location.search).get("room")')
            aid=page.locator('#invite-agent-id').input_value()
            page.wait_for_function('window.syntheticCopies.length===1')
            invite=json.loads(page.evaluate('window.syntheticCopies')[0])
            secret=invite.pop('token')
            assert set(invite)=={'v','origin','room','agent','runtime','auth_mode','max_model_calls','request_turn'}
            assert invite['v']==1 and invite['origin']==base and invite['room']==rid and invite['agent']==aid
            assert invite['runtime']=='claude' and invite['auth_mode']=='native'
            assert invite['max_model_calls']==4 and invite['request_turn'] is False
            assert secret and len(secret)<=512
            assert page.locator('#invite-secret').evaluate('(node)=>node.hidden')
            assert page.locator('#invite-secret-value').input_value()==''
            for selector in ('#onboarding-config','#onboarding-prompt'):
                assert secret not in page.locator(selector).input_value()
            assert secret not in page.locator('#onboarding-commands').text_content()
            assert secret not in page.locator('#onboarding-dialog').text_content()
            page.screenshot(path=str(Path(config['screenshots'])/('invite-'+language+'-invited.png')),full_page=True)
            # Next step: download the settings file and copy the manual command.
            with page.expect_download() as downloaded:page.locator('#invite-next').click()
            settings=json.loads(Path(downloaded.value.path()).read_text(encoding='utf-8'))
            assert settings['base_url']==base and 'token' not in settings
            copied=page.evaluate('window.syntheticCopies')
            assert len(copied)==2 and secret not in copied[-1] and '--runtime claude' in copied[-1]
            assert 'cli connect' in copied[-1] and '--room '+rid in copied[-1] and '--acknowledge-private' in copied[-1]
            assert 'cli join' not in copied[-1] and copied[-1].count('--prompt-token') == 1
            assert 'cli check' not in copied[-1] and '--retry-check' not in copied[-1]
            before_posts=len(posts)
            page.keyboard.press('Escape')
            pw.expect(page.locator('#onboarding-dialog')).not_to_be_visible()
            assert page.locator('#invite-secret-value').input_value()==''
            # Reopening an existing pending invitation issues nothing and reuses the key.
            page.locator('#invite-submit').click()
            pw.expect(page.locator('#invite-next')).to_have_text(prepare)
            assert len(posts)==before_posts
            page.reload();page.locator('#detail').wait_for(state='visible')
            if language=='en':page.locator('#language').click()
            page.locator('[data-tab=participants]').click()
            page.locator('#invite-agent-id').select_option(aid);page.locator('#invite-submit').click()
            pw.expect(page.locator('#invite-next')).to_have_text(prepare)
            assert len(posts)==before_posts
            page.locator('#invite-machine').select_option('other')
            pw.expect(page.locator('#invite-next')).to_be_disabled()
            page.locator('#invite-machine').select_option('same')
            page.evaluate('window.syntheticClipboardFailure=true')
            with page.expect_download():page.locator('#invite-next').click()
            pw.expect(page.locator('#invite-error')).to_contain_text('복사' if language=='ko' else 'clipboard')
            assert page.locator('#invite-command-preview').evaluate('(node)=>node.open')
            page.screenshot(path=str(Path(config['screenshots'])/('invite-'+language+'-copy-fallback.png')),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert page.locator('#onboarding-dialog').evaluate('(node)=>node.scrollWidth<=node.clientWidth+1')
            # Acceptance and synthetic contact are real fixture API calls, never a provider call.
            client=APIClient(base,secret,base)
            invitation=next(i for i in client.request('GET','agents/me/invitations') if i['room_id']==rid)
            client.request('POST','rooms/'+rid+'/memberships/me/accept',
                {'expected_revision':invitation['membership_revision'],'acknowledge_private':True},'synthetic-invite-accept')
            client.request('PUT','agents/me/connection',{'runtime':'synthetic','state':'idle'},'synthetic-contact')
            page.keyboard.press('Escape');page.locator('#refresh').click();page.locator('[data-tab=participants]').click()
            pw.expect(page.locator('#members')).to_contain_text('Synthetic participant')
            page.locator('#invite-submit').click()
            pw.expect(page.locator('#invite-status')).not_to_contain_text('Current connection confirmed' if language=='en' else '현재 연결 확인됨')
            # Issuance failure is an explicit mocked HTTP failure, not a backend result.
            def fail_issue(route):
                if route.request.method=='POST':route.fulfill(status=503,content_type='application/json',body='{"code":"service_unavailable"}')
                else:route.continue_()
            page.route('**/api/v1/agents/'+aid+'/credentials',fail_issue)
            page.locator('#invite-advanced > summary').click()
            page.locator('#invite-reissue').click()
            pw.expect(page.locator('#invite-error')).to_contain_text('확인하지 못' if language=='ko' else 'could not be confirmed')
            count_after_failure=len(posts);page.locator('#invite-next').click()
            pw.expect(page.locator('#invite-next')).to_have_text('접속 권한 상태 다시 확인' if language=='ko' else 'Check access status again')
            assert len(posts)==count_after_failure and not errors
            assert not any('/runtime-checks' in p or '/controls' in p for p in posts)
            page.close();context.close()
        finally:
            if client:client.client.headers.clear();client.close()
            secret=None
            browser.close()
