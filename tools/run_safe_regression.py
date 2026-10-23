"""Windows CI contracts only: no provider, real credential, install or deployment actions."""
import collections
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    'test_mcp_stdio_process_interoperability': 'prebound sockets startup remains blocked by the offline guard',
    'test_postgres_real_loopback_health_readiness_and_shutdown': 'prebound sockets startup remains blocked by the offline guard',
    'test_browser_register_invite_setup_start_and_text_artifact': 'separate browser fixture validation; not exercised by this CI job',
    'test_beginner_register_invite_acceptance_guide_reload': 'verified separately with guarded server children; not exercised by this CI job',
    'test_browser_upload_read_reply_view_download_revoke_delete': 'separate browser fixture validation; not exercised by this CI job',
}


def python_contracts(output):
    sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
    os.chdir(ROOT)
    from ci_guard import install
    install()
    import pytest
    class Reports:
        def pytest_collection_modifyitems(self, session, config, items):
            skipped = [i for i in items if i.originalname in EXCLUDED]
            items[:] = [i for i in items if i.originalname not in EXCLUDED]
            (output/'selection.json').write_text(json.dumps([
                {'nodeid':i.nodeid,'reason':EXCLUDED[i.originalname]} for i in skipped],indent=2),encoding='utf-8')
            config.hook.pytest_deselected(items=skipped)
        def pytest_runtest_logreport(self, report):
            if report.when=='call' or report.failed or report.skipped:
                with (output/'cases.jsonl').open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps({'nodeid':report.nodeid,'phase':report.when,'outcome':report.outcome})+'\n')
    return pytest.main(['-q','-ra','tests','-p','no:cacheprovider','--maxfail=1',
                        '--basetemp='+str(output/'temp')],plugins=[Reports()])


def main():
    if len(sys.argv)>1 and sys.argv[1]=='child':
        return python_contracts(Path(sys.argv[2]))
    sys.path.insert(0,str(ROOT))
    from talkabout.processes import ManagedProcess
    output=ROOT/'.ci-results'
    output.mkdir(exist_ok=False)
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONNOUSERSITE='1')
    for key in list(env):
        if any(word in key.upper() for word in ('TOKEN','API_KEY','DATABASE_URL','PASSWORD')):env.pop(key)
    results={}
    for name,command,timeout in (
        ('python',[sys.executable,'-B',str(Path(__file__)),'child',str(output)],600),
        ('node',['node','--test',*[str(p) for p in sorted((ROOT/'tests').glob('*.mjs'))]],180),
    ):
        owned=None
        with (output/(name+'.log')).open('wb') as log:
            try:
                owned=ManagedProcess(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
                results[name]={'exit':owned.process.wait(timeout=timeout)}
            except subprocess.TimeoutExpired:
                results[name]={'exit':None,'timeout':True}
            finally:
                results.setdefault(name,{})['job_stopped']=owned.stop() if owned else True
        print((output/(name+'.log')).read_text(encoding='utf-8',errors='replace')[-9000:],flush=True)
        if name=='python' and (output/'cases.jsonl').exists():
            rows=[json.loads(s) for s in (output/'cases.jsonl').read_text(encoding='utf-8').splitlines()]
            results[name]['counts']=dict(collections.Counter(r['outcome'] for r in rows))
    (output/'result.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results),flush=True)
    return int(any(r.get('exit')!=0 or not r['job_stopped'] for r in results.values()))


if __name__=='__main__':raise SystemExit(main())
