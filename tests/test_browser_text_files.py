"""Real Chrome, synthetic files and loopback fixture only."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
import pytest
from talkabout.worker import APIClient,Journal,SyntheticRuntime,Worker


@pytest.mark.parametrize('language,width',[('ko',1400),('en',390)])
def test_browser_upload_read_reply_view_download_revoke_delete(tmp_path,language,width):
    pw=pytest.importorskip('playwright.sync_api')
    chrome=Path(os.environ.get('TALKABOUT_TEST_CHROMIUM') or 'C:/Program Files/Google/Chrome/Application/chrome.exe')
    if not chrome.exists():pytest.skip('Chrome executable unavailable')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base='http://127.0.0.1:'+str(port)
    env={**os.environ,'TALKABOUT_FIXTURE_PORT':str(port),'TALKABOUT_TEXT_FIXTURE_ROOT':str(tmp_path/'private-files')}
    with (tmp_path/'server.log').open('wb') as log:
        server=subprocess.Popen([sys.executable,'tests/text_files_fixture_app.py'],env=env,stdout=log,stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            for _ in range(80):
                try:
                    if httpx.get(base+'/health',timeout=.5,trust_env=False).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:pytest.fail('isolated_text_fixture_startup_failed')
            with pw.sync_playwright() as runner:
                browser=runner.chromium.launch(executable_path=str(chrome),headless=True,args=['--disable-sync','--disable-background-networking'])
                context=browser.new_context(viewport={'width':width,'height':1000},accept_downloads=True)
                page=context.new_page();errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('dialog',lambda dialog:dialog.accept())
                page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
                page.goto(base);page.locator('#workspace').fill('text-fixture');page.locator('#username').fill('owner')
                page.locator('#password').fill('fixture-password-not-real');page.locator('#login button').click()
                page.locator('#dashboard').wait_for(state='visible')
                if language=='en':page.locator('#language').click()
                page.locator('.room-card',has_text='Synthetic text attachment').click()
                page.locator('#text-files-panel').wait_for(state='visible')
                rid=page.evaluate('new URLSearchParams(location.search).get("room")')
                uploads=[];page.on('request',lambda request:uploads.append(request.url) if request.method=='POST' and request.url.endswith('/uploads') else None)
                page.locator('#text-file-input').set_input_files({'name':'unsupported.pdf','mimeType':'application/pdf','buffer':b'%PDF-fixture'})
                pw.expect(page.locator('#file-upload-status')).to_contain_text('.txt/.md')
                assert not uploads
                content=b'# Fixture\nThe launch color is cobalt-blue.\n<script>window.fileExecuted=true</script>\n'
                page.locator('#text-file-input').set_input_files({'name':'source.md','mimeType':'text/markdown','buffer':content})
                page.locator('#file-unscanned-ack').check();page.locator('#file-agent-consent').check()
                page.locator('#file-upload-button').click()
                pw.expect(page.locator('#pending-text-files')).to_contain_text('source.md')
                assert page.locator('#file-upload-progress').get_attribute('value')=='3'
                page.locator('#message-text').fill('Use the attached fixture color.');page.locator('#message button[type=submit]').click()
                pw.expect(page.locator('#pending-text-files')).not_to_contain_text('source.md')
                client=APIClient(base,'fixture-file-agent',base);journal=Journal(tmp_path/'worker.sqlite')
                try:assert Worker(client,journal,SyntheticRuntime()).step()=='completed'
                finally:journal.close();client.client.close()
                page.locator('#refresh').click()
                pw.expect(page.locator('#messages')).to_contain_text('Synthetic file read')
                pw.expect(page.locator('#messages')).to_contain_text('cobalt-blue')
                file=page.request.get(base+'/api/v1/rooms/'+rid+'/files').json()[0]
                assert file['malware_scan']=='not_run' and file['malware_safety']=='not_asserted'
                card=page.locator('#text-file-list li').first
                card.get_by_role('button',name='View attachment' if language=='en' else '첨부 조회',exact=True).click()
                pw.expect(page.locator('#text-file-output')).to_contain_text('<script>')
                assert page.evaluate('window.fileExecuted') is None
                page.locator('#text-file-view-close').click()
                with page.expect_download() as download:card.locator('a').click()
                assert Path(download.value.path()).read_bytes()==content
                card.get_by_role('button',name='Revoke agent read consent' if language=='en' else '에이전트 읽기 동의 철회',exact=True).click()
                pw.expect(card).to_contain_text('Agent content processing not consented' if language=='en' else '에이전트 내용 처리 동의 없음')
                page.screenshot(path=str(tmp_path/('text-files-'+language+'.png')),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.locator('[data-file-delete]').click()
                pw.expect(page.locator('#text-file-list li')).to_have_count(0)
                assert page.request.get(base+'/api/v1/rooms/'+rid+'/files/'+file['file_id']+'/download').status==404
                assert not errors
                context.close();browser.close()
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=3)
