"""Explicit disposable rootless installer smoke. Synthetic secrets; no publish."""
import importlib.util,json,uuid,hashlib,argparse
from pathlib import Path
source=Path(__file__).with_name('install.py') if Path(__file__).with_name('install.py').exists() else Path.cwd()/'deploy/podman/install.py'
spec=importlib.util.spec_from_file_location('installer',source)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--image',required=True)
image=parser.parse_args().image;engine=m.Engine();fixtures=[];checks=[];extra=[]

class Interrupted(m.Engine):
    def __init__(self,phase):self.phase=phase
    def run(self,args,data=None,check=True):
        if self.phase=='secret' and args[:2]==['secret','create'] and args[-2].endswith('-db-password'):
            self.phase=None;raise m.InstallError('synthetic_interruption')
        if self.phase=='db_committed' and args[0]=='run' and 'provision-db' in args:
            result=super().run(args,data,check);self.phase=None;raise m.InstallError('synthetic_response_lost')
        if self.phase=='existing_db' and args[0]=='run' and 'provision-db' in args:
            db=args[args.index('--name')+1][:-10]+'-db'
            super().run(['exec','-i',db,'psql','-U','talkabout_owner','-d','talkabout','-v','ON_ERROR_STOP=1'],b"CREATE TABLE fixture_existing_data(value text); INSERT INTO fixture_existing_data VALUES('synthetic-marker');")
            self.phase=None
        return super().run(args,data,check)

def fresh(phase=None):
    prefix='talkabout-fixture-auto-'+uuid.uuid4().hex[:12]
    i=m.Installer(image,None,Path.cwd()/('.'+prefix),prefix=prefix,engine=Interrupted(phase) if phase else engine,synthetic_fixture=True)
    fixtures.append(i);return i

def repeat(i):return m.Installer(image,None,i.directory,prefix=i.prefix,engine=engine,synthetic_fixture=True)
def expect_failure(i):
    try:i.install()
    except m.InstallError:return
    raise AssertionError('fixture_expected_safe_refusal')
def fingerprint(i):return hashlib.sha256(i.secret_value(i.names['bundle'])).digest()
def sql(i,text):return engine.run(['exec','-i',i.names['db'],'psql','-U','talkabout_owner','-d','talkabout','-At','-v','ON_ERROR_STOP=1'],text.encode()).stdout.strip()

result={'status':'FAIL','fixture_only':True,'published_ports':False,'operating_credentials_used':False}
try:
    i=fresh('secret');expect_failure(i);before=fingerprint(i)
    assert repeat(i).install()['status']=='database_installed';assert fingerprint(i)==before
    checks.append('first_install_after_secret_interruption')
    assert sql(i,"SELECT rolsuper||':'||rolcreatedb||':'||rolcreaterole||':'||rolreplication||':'||rolbypassrls FROM pg_roles WHERE rolname='talkabout_app'")==b'false:false:false:false:false'
    checks.append('runtime_role_no_admin_privileges')
    assert repeat(i).install()['secrets_returned'] is False and fingerprint(i)==before
    checks.append('successful_rerun_without_rotation')
    bundle=json.loads(i.secret_value(i.names['bundle']))
    observable=(i.directory/'state.json').read_bytes()
    for container in (i.names['db'],i.names['app']):
        log=engine.run(['logs',container],check=False);observable+=log.stdout+log.stderr
    assert all(value.encode() not in observable for value in bundle.values())
    checks.append('generated_credentials_absent_from_logs_and_journal')
    value=i.secret_value(i.names['runtime']);engine.run(['secret','rm',i.names['runtime']]);repeat(i).install()
    assert i.secret_value(i.names['runtime'])==value and fingerprint(i)==before
    checks.append('missing_derived_secret_restored_same_value')
    with m.installation_lock(i.directory):expect_failure(repeat(i))
    checks.append('concurrent_installation_lock')
    # Installer does not create a human/setup code; exercise the existing GUI API
    # using only this disposable fixture's generated credentials.
    probe="""from talkabout.container import read_secret
from talkabout.store import PostgresStore
from talkabout.setup import issue_code
import httpx
s=PostgresStore(read_secret());assert s.setup_required()
with s.installation() as (state,legacy,_):assert not state and not legacy
c=httpx.Client(base_url='http://127.0.0.1:8080',trust_env=False)
assert c.get('/api/v1/rooms').status_code==503
nonce=c.get('/api/v1/setup/csrf').json()['csrf_token']
code=issue_code(s)
body=dict(code=code,workspace_id='fixture-auto',display_name='Fixture Owner',username='owner',password='synthetic-human-password',confirmation='synthetic-human-password')
assert c.post('/api/v1/setup/complete',json=body,headers={'Origin':'http://127.0.0.1:8080','X-CSRF-Token':nonce}).status_code==200
assert c.post('/api/v1/session',json=dict(workspace_id='fixture-auto',username='owner',password='synthetic-human-password'),headers={'Origin':'http://127.0.0.1:8080'}).status_code==200
assert c.get('/ready').status_code==200
print('synthetic_gui_setup_pass')
"""
    assert engine.run(['exec','-i',i.names['app'],'python','-'],probe.encode()).stdout.strip()==b'synthetic_gui_setup_pass'
    checks.append('no_installer_owner_code_then_existing_gui_setup')
    sql(i,"ALTER ROLE talkabout_app PASSWORD 'synthetic-mismatch-only';")
    expect_failure(repeat(i));assert fingerprint(i)==before
    assert sql(i,'SELECT count(*) FROM principals;')==b'1'
    checks.append('database_password_mismatch_preserves_data_and_secrets')
    engine.run(['secret','rm',i.names['bundle']]);expect_failure(repeat(i))
    assert engine.inspect('volume',i.names['volume'])
    checks.append('missing_master_refuses_reinitialization')
    j=fresh('db_committed');expect_failure(j);before=fingerprint(j)
    repeat(j).install();assert fingerprint(j)==before
    checks.append('database_commit_response_loss_resumes_same_credentials')
    k=fresh('existing_db');expect_failure(k)
    assert sql(k,'SELECT value FROM fixture_existing_data;')==b'synthetic-marker'
    assert sql(k,"SELECT count(*) FROM pg_roles WHERE rolname='talkabout_app'")==b'0'
    checks.append('unmarked_existing_database_preserved')
    n=fresh();mark=uuid.uuid4().hex
    engine.run(['volume','create','--label','io.talkabout.project=talkabout','--label','io.talkabout.fixture='+mark,n.names['volume']])
    extra.append((n.names['volume'],mark));expect_failure(n)
    assert engine.inspect('volume',n.names['volume']) and not engine.inspect('secret',n.names['bundle'])
    checks.append('unknown_existing_volume_refused_before_secret_generation')
    result['status']='PASS'
except Exception as error:
    result['error_type']=type(error).__name__
    result['error_code']=str(error) if isinstance(error,m.InstallError) else 'fixture_assertion_failed'
finally:
    cleanup=True
    for i in fixtures:
        if not hasattr(i,'state'):continue
        resources=[('container',i.names['app']),('container',i.prefix+'-provision'),('container',i.names['db']),*[('secret',i.names[k]) for k in ('runtime','owner','password','bundle')],('volume',i.names['volume']),('network',i.names['network'])]
        for kind,name in resources:
            try:
                obj=engine.inspect(kind,name)
                if obj:
                    if m.metadata(kind,obj).get('io.talkabout.installation')!=i.state['id']:raise RuntimeError('fixture_ownership_mismatch')
                    engine.run([kind,'rm',*(['--force'] if kind=='container' else []),name])
            except Exception:cleanup=False
    for name,mark in extra:
        try:
            obj=engine.inspect('volume',name)
            if obj:
                assert m.metadata('volume',obj).get('io.talkabout.fixture')==mark
                engine.run(['volume','rm',name])
        except Exception:cleanup=False
    result.update(checks=checks,fixture_cleanup=cleanup)
print(json.dumps(result))
raise SystemExit(0 if result['status']=='PASS' and result['fixture_cleanup'] else 1)
