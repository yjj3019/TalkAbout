"""Windows CI contracts only: no provider, real credential, install or deployment actions.

Each run requires a fresh evidence directory. `TALKABOUT_CI_RESULTS` points that
directory elsewhere, so a stale or foreign-owned `.ci-results` cannot block a local
run; CI keeps the default path.
"""
import collections
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
# Windows consoles default to a legacy code page; a child log containing a
# check mark must not turn a completed run into a UnicodeEncodeError.
try:
    sys.stdout.reconfigure(errors='backslashreplace')
except (AttributeError, ValueError):
    pass
EXCLUDED = {
    'test_mcp_stdio_process_interoperability': 'prebound sockets startup remains blocked by the offline guard',
    'test_postgres_real_loopback_health_readiness_and_shutdown': 'prebound sockets startup remains blocked by the offline guard',
    'test_browser_register_invite_setup_start_and_text_artifact': 'separate browser fixture validation; not exercised by this CI job',
    'test_beginner_register_invite_acceptance_guide_reload': 'verified separately with guarded server children; not exercised by this CI job',
    'test_browser_upload_read_reply_view_download_revoke_delete': 'separate browser fixture validation; not exercised by this CI job',
}


def python_contracts(output, postgres_config=None):
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
    arguments=['-q','-ra','tests','-p','no:cacheprovider','--maxfail=1',
               '--basetemp='+str(output/'temp')]
    if postgres_config is not None: arguments += ['--postgres-config', str(postgres_config)]
    return pytest.main(arguments,plugins=[Reports()])


def main():
    arguments=list(sys.argv[1:])
    postgres_config=None
    if '--postgres-config' in arguments:
        index=arguments.index('--postgres-config')
        if len(arguments)<=index+1: raise SystemExit('--postgres-config requires a path')
        postgres_config=Path(arguments[index+1]).resolve()
        del arguments[index:index+2]
        if ROOT in postgres_config.parents:
            raise SystemExit('refusing a fixture config inside the repository: keep it in a private directory')
        if not postgres_config.is_file():
            raise SystemExit('fixture config not found: '+str(postgres_config))
    if arguments and arguments[0]=='child':
        return python_contracts(Path(arguments[1]), postgres_config)
    sys.path.insert(0,str(ROOT))
    from talkabout.processes import ManagedProcess
    output = Path(os.environ.get('TALKABOUT_CI_RESULTS') or ROOT/'.ci-results').resolve()
    output.mkdir(exist_ok=False)
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONNOUSERSITE='1')
    for key in list(env):
        if any(word in key.upper() for word in ('TOKEN','API_KEY','DATABASE_URL','PASSWORD')):env.pop(key)
    results={}
    for name,command,timeout in (
        ('python',[sys.executable,'-B',str(Path(__file__)),'child',str(output),*(['--postgres-config',str(postgres_config)] if postgres_config else [])],600),
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
