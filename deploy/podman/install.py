"""Explicit rootless installation. No default passwords, secret output or rotation."""
import argparse
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
import uuid


class InstallError(Exception):
    pass


class Engine:
    def run(self, args, data=None, check=True):
        try:
            r=subprocess.run(['podman',*args],input=data,capture_output=True,timeout=120)
        except Exception:
            raise InstallError('engine_unavailable') from None
        if check and r.returncode:
            raise InstallError('engine_operation_failed')
        return r

    def inspect(self, kind, name):
        r=self.run([kind,'inspect',name],check=False)
        if r.returncode:
            # Exit codes cannot distinguish absence from daemon/storage failure.
            command=[kind,'exists',name] if kind=='container' else ['secret','exists',name] if kind=='secret' else [kind,'exists',name]
            exists=self.run(command,check=False)
            if exists.returncode!=1: raise InstallError('resource_inspection_failed')
            return None
        try: return json.loads(r.stdout)[0]
        except Exception: raise InstallError('resource_inspection_failed') from None


def metadata(kind,obj):
    if kind=='container': return obj.get('Config',{}).get('Labels',{})
    if kind=='secret': return obj.get('Spec',{}).get('Labels',{})
    return obj.get('Labels') or obj.get('labels') or {}


@contextmanager
def installation_lock(directory):
    directory=Path(directory)
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    st=directory.lstat()
    if directory.is_symlink() or (os.name!='nt' and (st.st_uid!=os.getuid() or st.st_mode & 0o077)):
        raise InstallError('private_state_directory_required')
    fd=os.open(directory/'install.lock',os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        if os.name=='nt':
            import msvcrt
            if os.fstat(fd).st_size==0:os.write(fd,b'0')
            os.lseek(fd,0,0)
            try:msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            except OSError:raise InstallError('installation_locked') from None
        else:
            import fcntl
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise InstallError('installation_locked') from None
        yield
    finally:os.close(fd)


class Installer:
    def __init__(self,image,host_port,directory,*,prefix='talkabout',engine=None,synthetic_fixture=False):
        if not re.fullmatch(r'localhost/talkabout:[a-f0-9]{40}',image):raise InstallError('source_sha_image_required')
        if not re.fullmatch(r'talkabout(?:-[a-z0-9]+)*',prefix) or len(prefix)>60:raise InstallError('project_prefix_required')
        if host_port is None:
            if not synthetic_fixture or not prefix.startswith('talkabout-fixture-'):raise InstallError('explicit_loopback_port_required')
        elif type(host_port)!=int or not 1024<=host_port<=65535:raise InstallError('explicit_loopback_port_required')
        self.image,self.port,self.directory,self.prefix=image,host_port,Path(directory),prefix
        self.engine=engine or Engine()
        self.names={k:prefix+'-'+v for k,v in {'network':'private','volume':'pg18-data','db':'db','app':'app','bundle':'db-bundle','password':'db-password','owner':'db-init-url','runtime':'db-url'}.items()}
        self.resources=[('secret',self.names[k]) for k in ('bundle','password','owner','runtime')]+[('network',self.names['network']),('volume',self.names['volume']),('container',self.names['db']),('container',self.names['app'])]
        self.config={'image':image,'port':host_port,'prefix':prefix}

    def save(self):
        temp=self.directory/('state-'+uuid.uuid4().hex+'.tmp')
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(self.state,f);f.flush();os.fsync(f.fileno())
        os.replace(temp,self.directory/'state.json')
        if os.name!='nt':
            fd=os.open(self.directory,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)

    @property
    def labels(self):return ['--label','io.talkabout.project=talkabout','--label','io.talkabout.installation='+self.state['id']]

    def owned(self,kind,name):
        obj=self.engine.inspect(kind,name)
        if obj is not None:
            labels=metadata(kind,obj)
            if labels.get('io.talkabout.project')!='talkabout' or labels.get('io.talkabout.installation')!=self.state['id']:
                raise InstallError('existing_resource_not_owned')
        return obj

    def secret_value(self,name):
        # Operator-owned rootless store only, captured internally; never forward output.
        self.owned('secret',name)
        data=self.engine.run(['secret','inspect','--showsecret','--format','{{.SecretData}}',name]).stdout.rstrip(b'\n')
        if len(data)>8192:raise InstallError('secret_format_invalid')
        return data

    def ensure_secret(self,name,value):
        if self.owned('secret',name):
            if not hmac.compare_digest(self.secret_value(name),value):raise InstallError('secret_reference_mismatch')
        else:self.engine.run(['secret','create',*self.labels,name,'-'],value)

    def prepare_secrets(self):
        name=self.names['bundle']
        if not self.owned('secret',name):
            if self.state.get('database_started') or any(self.engine.inspect(kind,n) for kind,n in self.resources if n!=name):
                raise InstallError('missing_master_secret_preserve_existing_resources')
            value=json.dumps({'owner':secrets.token_urlsafe(48),'runtime':secrets.token_urlsafe(48)},sort_keys=True).encode()
            self.engine.run(['secret','create',*self.labels,name,'-'],value)
        try:
            bundle=json.loads(self.secret_value(name))
            if set(bundle)!={'owner','runtime'} or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]{64}',v) for v in bundle.values()):raise ValueError()
            if bundle['owner']==bundle['runtime']:raise ValueError()
        except Exception:raise InstallError('master_secret_invalid') from None
        owner,app=bundle['owner'],bundle['runtime']
        def dsn(user,password):return f'host=talkabout-db port=5432 dbname=talkabout user={user} password={password}'
        for key,value in [('password',owner),('owner',dsn('talkabout_owner',owner)),('runtime',dsn('talkabout_app',app))]:
            self.ensure_secret(self.names[key],value.encode())
        self.state['secrets_ready']=True;self.save()

    def common(self):
        return [*self.labels,'--network',self.names['network'],'--cap-drop=all','--security-opt=no-new-privileges',
                '--pids-limit=128','--cpus=1','--memory=512m','--memory-swap=512m','--read-only','--read-only-tmpfs=false',
                '--tmpfs=/tmp:rw,nosuid,nodev,size=32m','--tmpfs=/run/secrets:rw,nosuid,nodev,size=1m,mode=0755',
                '--log-driver=k8s-file','--log-opt=max-size=10mb']

    def check_port(self):
        if self.port is not None:
            with socket.socket() as probe:
                try:probe.bind(('127.0.0.1',self.port))
                except OSError:raise InstallError('loopback_port_in_use') from None

    def ensure_container(self,name,args):
        fingerprint=hashlib.sha256(json.dumps(args).encode()).hexdigest()
        obj=self.owned('container',name)
        if obj:
            if metadata('container',obj).get('io.talkabout.install-config')!=fingerprint:raise InstallError('container_configuration_mismatch')
            if not obj['State']['Running']:self.engine.run(['start',name])
        else:self.engine.run(['run','--detach','--name',name,'--label','io.talkabout.install-config='+fingerprint,*args])

    def install(self):
        with installation_lock(self.directory):
            if self.engine.run(['info','--format','{{.Host.Security.Rootless}}']).stdout.strip()!=b'true':raise InstallError('rootless_required')
            image=self.engine.run(['image','inspect',self.image])
            try:
                cfg=json.loads(image.stdout)[0]['Config']
                if cfg['User']!='10001:10001' or cfg['Labels']['io.talkabout.project']!='talkabout' or cfg['Labels'].get('org.opencontainers.image.revision')!=self.image.split(':')[1]:raise ValueError()
            except Exception:raise InstallError('project_nonroot_image_required') from None
            path=self.directory/'state.json'
            if path.is_symlink():raise InstallError('private_state_file_required')
            if path.exists():
                if os.name!='nt' and (path.stat().st_mode & 0o077 or path.stat().st_uid!=os.getuid()):raise InstallError('private_state_file_required')
                try:self.state=json.loads(path.read_text(encoding='utf-8'))
                except Exception:raise InstallError('state_invalid') from None
                if self.state.get('version')!=1 or type(self.state.get('database_started'))!=bool or type(self.state.get('complete'))!=bool:raise InstallError('state_invalid')
                if self.state.get('config')!=self.config or not re.fullmatch(r'[a-f0-9]{32}',self.state.get('id','')):raise InstallError('installation_configuration_mismatch')
            else:
                if any(self.engine.inspect(kind,name) for kind,name in self.resources):raise InstallError('existing_installation_requires_manual_review')
                self.check_port()
                self.state={'version':1,'id':uuid.uuid4().hex,'config':self.config,'database_started':False,'complete':False};self.save()
            for kind,name in self.resources:self.owned(kind,name)
            if (self.state['database_started'] or self.state['complete'] or self.owned('container',self.names['db'])) and not self.owned('volume',self.names['volume']):raise InstallError('missing_persistence_never_reinitialize')
            self.prepare_secrets()
            if not self.owned('network',self.names['network']):self.engine.run(['network','create','--internal',*self.labels,self.names['network']])
            elif not self.owned('network',self.names['network']).get('internal',False):raise InstallError('private_network_required')
            if not self.owned('volume',self.names['volume']):self.engine.run(['volume','create',*self.labels,self.names['volume']])
            self.state['database_started']=True;self.save()
            pg=json.loads(Path(__file__).with_name('images.lock.json').read_text())['images']['postgres']['reference']
            self.ensure_container(self.names['db'],[*self.common(),'--user=999:999','--network-alias=talkabout-db','--volume',self.names['volume']+':/var/lib/postgresql:U',
                '--tmpfs=/var/run/postgresql:rw,nosuid,nodev,size=16m,mode=1777','--secret',self.names['password']+',target=pg-password,uid=999,gid=999,mode=0400',
                '-e','POSTGRES_PASSWORD_FILE=/run/secrets/pg-password','-e','POSTGRES_USER=talkabout_owner','-e','POSTGRES_DB=talkabout',pg,'-c','max_connections=30','-c','shared_buffers=64MB'])
            for _ in range(40):
                if self.engine.run(['exec',self.names['db'],'pg_isready','-U','talkabout_owner','-d','talkabout'],check=False).returncode==0:break
                time.sleep(.25)
            else:raise InstallError('database_readiness_timeout')
            job=self.prefix+'-provision'
            if self.engine.inspect('container',job):raise InstallError('provision_job_in_progress')
            self.engine.run(['run','--rm','--name',job,*self.common(),'--user=10001:10001',
                '--secret',self.names['owner']+',target=talkabout-db-url,uid=10001,gid=10001,mode=0400',
                '--secret',self.names['runtime']+',target=talkabout-runtime-url,uid=10001,gid=10001,mode=0400',self.image,'provision-db','--installation-id',self.state['id']])
            app_args=[*self.common(),'--user=10001:10001','--secret',self.names['runtime']+',target=talkabout-db-url,uid=10001,gid=10001,mode=0400']
            if self.port is not None:
                if not self.owned('container',self.names['app']):
                    self.check_port()
                app_args.append(f'--publish=127.0.0.1:{self.port}:8080')
            self.ensure_container(self.names['app'],[*app_args,self.image])
            health="import urllib.request; o=urllib.request.build_opener(urllib.request.ProxyHandler({})); r=o.open('http://127.0.0.1:8080/health',timeout=3); assert r.status==200"
            for _ in range(40):
                if self.engine.run(['exec',self.names['app'],'python','-c',health],check=False).returncode==0:break
                time.sleep(.25)
            else:raise InstallError('app_liveness_timeout')
            self.state['complete']=True;self.save()
            return {'status':'database_installed','owner_setup':'user_terminal_code_then_gui','secrets_returned':False,'rotation':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',action='store_true')
    parser.add_argument('--app-image')
    parser.add_argument('--host-port',type=int)
    parser.add_argument('--prefix',default='talkabout')
    args=parser.parse_args()
    if not args.install:
        print(json.dumps({'status':'NOT_EXECUTED','secrets_generated':False}));return 0
    try:
        if sys.platform!='linux':raise InstallError('linux_rootless_host_required')
        if not re.fullmatch(r'talkabout(?:-[a-z0-9]+)*',args.prefix):raise InstallError('project_prefix_required')
        directory=Path.home()/'.local/share/TalkAbout/installations'/args.prefix
        result=Installer(args.app_image or '',args.host_port,directory,prefix=args.prefix).install()
        print(json.dumps(result));return 0
    except BaseException as error:
        code=str(error) if isinstance(error,InstallError) else 'installation_interrupted_or_failed'
        print(json.dumps({'status':'incomplete_existing_resources_preserved','code':code,'secrets_returned':False}),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
