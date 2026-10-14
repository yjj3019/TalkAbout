import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

spec=importlib.util.spec_from_file_location('auto_install',Path(__file__).resolve().parents[1]/'deploy/podman/install.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
IMAGE='localhost/talkabout:'+'a'*40


class FakeEngine:
    def __init__(self):self.objects={};self.values={};self.calls=[];self.fail=None;self.owner=None;self.runtime=None;self.provisioned=False
    def inspect(self,kind,name):return self.objects.get((kind,name))
    def run(self,args,data=None,check=True):
        self.calls.append(list(args))
        if args[0]=='info':return SimpleNamespace(returncode=0,stdout=b'true')
        if args[:2]==['image','inspect']:
            return SimpleNamespace(returncode=0,stdout=json.dumps([{'Config':{'User':'10001:10001','Labels':{'io.talkabout.project':'talkabout','org.opencontainers.image.revision':'a'*40}}}]).encode())
        labels=dict(args[i+1].split('=',1) for i,v in enumerate(args) if v=='--label')
        if args[:2]==['secret','create']:
            name=args[-2]
            if self.fail=='secret' and name.endswith('-db-password'):self.fail=None;raise module.InstallError('synthetic_interruption')
            self.objects['secret',name]={'Spec':{'Labels':labels}};self.values[name]=data
        elif args[:2]==['secret','inspect']:return SimpleNamespace(returncode=0,stdout=self.values[args[-1]]+b'\n')
        elif args[:2]==['network','create']:self.objects['network',args[-1]]={'labels':labels,'internal':True}
        elif args[:2]==['volume','create']:self.objects['volume',args[-1]]={'Labels':labels}
        elif args[0]=='run':
            name=args[args.index('--name')+1]
            if '--detach' in args:
                self.objects['container',name]={'Config':{'Labels':labels},'State':{'Running':True}}
                if name.endswith('-db'):self.owner=self.values[name[:-3]+'-db-password']
            else:
                prefix=name[:-10]
                if self.values[prefix+'-db-password']!=self.owner:raise module.InstallError('synthetic_db_mismatch')
                runtime=self.values[prefix+'-db-url']
                if self.provisioned and runtime!=self.runtime:raise module.InstallError('synthetic_db_mismatch')
                self.runtime=runtime;self.provisioned=True
                if self.fail=='after_db_commit':self.fail=None;raise module.InstallError('synthetic_interruption')
        return SimpleNamespace(returncode=0,stdout=b'')


def installer(tmp_path,engine):return module.Installer(IMAGE,None,tmp_path/'state',prefix='talkabout-fixture-unit',engine=engine,synthetic_fixture=True)


def test_first_install_rerun_and_no_secret_output_or_argv(tmp_path,capsys):
    e=FakeEngine();i=installer(tmp_path,e)
    result=i.install();snapshot=dict(e.values);calls=len(e.calls)
    assert result['secrets_returned'] is False and len(snapshot)==4
    bundle=json.loads(snapshot[i.names['bundle']]);assert bundle['owner']!=bundle['runtime'] and len(bundle['owner'])==64
    assert installer(tmp_path,e).install()==result and e.values==snapshot
    assert not any(c[:2] in [['secret','create'],['volume','create'],['network','create']] for c in e.calls[calls:])
    journal=(tmp_path/'state/state.json').read_text();output=capsys.readouterr()
    for value in bundle.values():assert value not in str(e.calls)+journal+output.out+output.err+json.dumps(result)


@pytest.mark.parametrize('phase',['secret','after_db_commit'])
def test_interrupted_install_resumes_without_rotation(tmp_path,phase):
    e=FakeEngine();e.fail=phase;i=installer(tmp_path,e)
    with pytest.raises(module.InstallError):i.install()
    bundle=e.values[i.names['bundle']]
    assert installer(tmp_path,e).install()['status']=='database_installed'
    assert e.values[i.names['bundle']]==bundle
    assert sum(c[:2]==['secret','create'] and c[-2]==i.names['bundle'] for c in e.calls)==1


def test_missing_derived_secret_restored_but_missing_master_and_volume_refused(tmp_path):
    e=FakeEngine();i=installer(tmp_path,e);i.install();value=e.values[i.names['runtime']]
    del e.objects['secret',i.names['runtime']];del e.values[i.names['runtime']]
    installer(tmp_path,e).install();assert e.values[i.names['runtime']]==value
    del e.objects['secret',i.names['bundle']]
    with pytest.raises(module.InstallError,match='missing_master'):installer(tmp_path,e).install()
    e2=FakeEngine();j=module.Installer(IMAGE,None,tmp_path/'other',prefix='talkabout-fixture-other',engine=e2,synthetic_fixture=True);j.install()
    del e2.objects['volume',j.names['volume']]
    with pytest.raises(module.InstallError,match='missing_persistence'):j.install()


def test_preexisting_volume_and_corrupt_derived_secret_preserved(tmp_path,monkeypatch):
    e=FakeEngine();i=installer(tmp_path,e);e.objects['volume',i.names['volume']]={'Labels':{}}
    monkeypatch.setattr(module.secrets,'token_urlsafe',lambda n: (_ for _ in ()).throw(AssertionError('must_not_generate')))
    with pytest.raises(module.InstallError,match='manual_review'):i.install()
    assert not e.values and ('volume',i.names['volume']) in e.objects
    monkeypatch.undo();e=FakeEngine();i=installer(tmp_path,e);i.install()
    e.values[i.names['runtime']]=b'synthetic-wrong'
    with pytest.raises(module.InstallError,match='reference_mismatch'):i.install()
    assert e.values[i.names['runtime']]==b'synthetic-wrong'


def test_database_password_mismatch_is_not_rotated(tmp_path):
    e=FakeEngine();i=installer(tmp_path,e);i.install();saved=dict(e.values);e.owner=b'synthetic-other-db-password'
    with pytest.raises(module.InstallError,match='db_mismatch'):i.install()
    assert e.values==saved


def test_lock_rejects_simultaneous_installation_and_releases(tmp_path):
    with module.installation_lock(tmp_path/'state'):
        with pytest.raises(module.InstallError,match='locked'):
            with module.installation_lock(tmp_path/'state'):pass
    with module.installation_lock(tmp_path/'state'):pass
