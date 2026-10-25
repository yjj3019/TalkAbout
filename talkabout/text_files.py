"""Opt-in, inert UTF-8 attachments; no malware scanner, parser or provider calls."""
import copy
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
import time

from .domain import (Problem, authorize, digest, emit, fencing, ident, replay,
                     require, save_response, token_hash)

MAX_BYTES = 65536
MAX_CHARS = 8000
TASK_CHARS = 24000
EXTENSIONS = {'.txt', '.md'}


def validate_text(name, content):
    require(isinstance(name,str) and 1<=len(name)<=120 and not name.startswith('.')
            and not any(c in name for c in '/\\:\x00') and all(ord(c)>=32 for c in name), 'invalid_filename',422)
    require(Path(name).suffix.lower() in EXTENSIONS and '..' not in name,'text_format_not_supported',415)
    require(type(content) is bytes and 0<len(content)<=MAX_BYTES,'file_size_limit',413)
    try:text=content.decode('utf-8',errors='strict')
    except UnicodeDecodeError:raise Problem('invalid_utf8',415) from None
    require(not any(ord(c)<32 and c not in '\n\t\r' for c in text),'binary_content_denied',415)
    prefix=content.lstrip().lower()
    require(not prefix.startswith((b'%pdf-',b'%!ps',b'pk\x03\x04',b'mz',b'\x7felf',b'<?xml',
                                   b'<!doctype html',b'<html',b'<svg',b'<script')),'active_or_binary_format_denied',415)
    return text


class PrivateTextStorage:
    """Server-generated keys only. Host OS access controls remain an operator duty."""
    def __init__(self, root, *, create=False):
        self.root=Path(root).absolute()
        require(self.root!=Path(self.root.anchor),'invalid_storage_root',422)
        web=Path(__file__).parent/'web'
        require(self.root!=web and web not in self.root.parents,'invalid_storage_root',422)
        self._check_parents()
        if create:
            # POSIX enforces 0o700. Windows ignores the mode value, and forwarding it can
            # still leave a directory the creating token cannot use when the process runs
            # under a restricted token (sandbox, AppContainer, service identity). Containment
            # on Windows therefore stays an operator duty, as the class docstring says.
            if os.name == 'nt':
                self.root.mkdir(parents=True,exist_ok=True)
            else:
                self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        require(self.root.is_dir(),'attachment_storage_unavailable',503)
        self._check_parents()

    def _check_parents(self):
        for path in (self.root,*self.root.parents):
            if path.exists() or path.is_symlink():
                attrs=path.lstat()
                require(not path.is_symlink() and not (getattr(attrs,'st_file_attributes',0)&1024),
                        'unsafe_storage_path',503)

    def _path(self, key):
        require(isinstance(key,str) and re.fullmatch('[a-f0-9]{32}',key),'invalid_object_key',422)
        self._check_parents()
        return self.root/(key+'.blob')

    def read(self,key,expected_hash,expected_size):
        path=self._path(key)
        try:
            flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
            require(not path.is_symlink() and not (getattr(path.lstat(),'st_file_attributes',0)&1024),'unsafe_storage_path',503)
            with os.fdopen(os.open(path,flags),'rb') as source:
                info=os.fstat(source.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size==expected_size<=MAX_BYTES,'storage_integrity_failed',503)
                content=source.read(MAX_BYTES+1)
        except OSError:raise Problem('attachment_storage_unavailable',503) from None
        require(len(content)==expected_size and hashlib.sha256(content).hexdigest()==expected_hash,'storage_integrity_failed',503)
        return content

    def write(self,key,content,expected_hash):
        path=self._path(key)
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0),0o600)
        except FileExistsError:
            self.read(key,expected_hash,len(content));return
        except OSError:raise Problem('attachment_storage_unavailable',503) from None
        with os.fdopen(fd,'wb') as target:
            target.write(content);target.flush();os.fsync(target.fileno())


def migrate_room(room):
    """Additive room-payload migration, under the existing room DB transaction."""
    version=room.get('text_attachment_schema',0)
    require(type(version) is int and version in {0,1},'unsupported_attachment_schema',503)
    if version==0:
        require('text_files' not in room,'attachment_migration_conflict',409)
        room['text_files']={};room['text_attachment_schema']=1
    return room


def public_task(task):
    return {k:copy.deepcopy(v) for k,v in task.items() if not k.startswith('_file_')}


def claim_proof(token,room_id,task,key):
    value=digest(['text-file-lease-v1',room_id,task['id'],task['attempt'],task['generation'],
                  task['membership_revision'],task['lease_id'],key])
    return hmac.new(token.encode(),value.encode(),'sha256').hexdigest()


def ref_of(file):
    return {k:file[k] for k in ('file_id','version_id','sha256')}


def _visible_message(room,member,file):
    return any(m['seq']>=member['read_start_seq'] and ref_of(file) in m.get('attachment_refs',[])
               for m in room['messages'].values())


def can_view_file(actor,room,member,file):
    if file.get('state')=='deleted':return False
    own=actor['id']==file['uploader_id'] and member['epoch']==file['uploader_epoch']
    if actor['kind']=='human':return own or _visible_message(room,member,file)
    return (file.get('allow_agent_processing') is True and actor['id'] in file.get('readers',[])
            and file.get('reader_epochs',{}).get(actor['id'])==member['epoch']
            and _visible_message(room,member,file))


def filter_refs(actor,room,member,refs):
    return [copy.deepcopy(ref) for ref in refs if isinstance(ref,dict)
            and all(isinstance(ref.get(k),str) for k in ('file_id','version_id','sha256'))
            and (file:=room.get('text_files',{}).get(ref.get('file_id'))) and ref==ref_of(file)
            and can_view_file(actor,room,member,file)]


def validate_refs(actor,room,member,refs):
    require(isinstance(refs,list) and len(refs)<=4 and all(isinstance(r,dict) for r in refs),'invalid_attachment_refs',422)
    require(all(set(r)=={'file_id','version_id','sha256'} and all(isinstance(v,str) for v in r.values())
                for r in refs),'invalid_attachment_refs',422)
    require(len({r.get('file_id') for r in refs})==len(refs),'duplicate_attachment_ref',422)
    for ref in refs:
        file=room.get('text_files',{}).get(ref.get('file_id'))
        require(file and ref==ref_of(file) and can_view_file(actor,room,member,file),'attachment_not_found',404)
        require(file['state']=='validated_utf8_text' and file['malware_scan']=='not_run'
                and file['ack_unscanned'] is True,'attachment_not_ready',409)


def metadata(file):
    keys=('file_id','version_id','sha256','name','size','state','malware_scan','revision',
          'readers','allow_agent_processing','expires_at')
    return {k:copy.deepcopy(file[k]) for k in keys} | {'media_type':'text/plain; charset=utf-8',
        'interpretation':'inert_utf8_text','malware_safety':'not_asserted'}


class TextAttachments:
    def __init__(self,store,storage):
        require(type(storage) is PrivateTextStorage,'invalid_attachment_storage',503)
        self.store,self.storage=store,storage

    @staticmethod
    def capabilities():
        return {'enabled':True,'extensions':['.txt','.md'],'max_bytes':MAX_BYTES,
                'malware_scan':'not_run','active_preview':False,'agent_read_requires_consent':True}

    @staticmethod
    def _sharing(room,body):
        readers=body.get('readers',[]);consent=body.get('allow_agent_processing',False)
        require(isinstance(readers,list) and len(readers)<=16 and all(isinstance(x,str) for x in readers)
                and len(set(readers))==len(readers) and type(consent) is bool,'invalid_file_sharing',422)
        require(not readers or consent,'agent_processing_consent_required',422)
        require(all((m:=room['members'].get(pid)) and m['kind']=='agent' and m['status']=='active'
                    and 'room:read' in m['permissions'] for pid in readers),'invalid_file_readers',422)
        return sorted(readers),consent

    def mutate(self,token,rid,operation,key,body,content=None):
        with self.store.transaction(rid,token) as (actor,room):
            member=authorize(actor,room,'room:contribute',True)
            require(room['state']!='closed' or operation.endswith(('/delete','/consent')),'room_closed',409)
            previous,lookup,fingerprint=replay(room,actor,'text-files/'+operation,key,body)
            if previous is not None:return previous
            migrate_room(room);now=time.time();parts=operation.split('/')
            if operation=='init':
                require(set(body)=={'name','size','sha256','media_type','readers','allow_agent_processing','ack_unscanned'},'invalid_upload_fields',422)
                require(type(body['size']) is int and 0<body['size']<=MAX_BYTES,'file_size_limit',413)
                require(isinstance(body['sha256'],str) and re.fullmatch('[a-f0-9]{64}',body['sha256']),'invalid_file_hash',422)
                validate_text(body['name'],b'validation placeholder')
                require(body['media_type'] in {'','text/plain','text/markdown'},'media_type_mismatch',415)
                require(body['ack_unscanned'] is True,'unscanned_acknowledgement_required',422)
                readers,consent=self._sharing(room,body)
                require(len(room['text_files'])<16,'room_file_capacity',429)
                fid=digest([actor['id'],rid,'text-file',key])[:32]
                file={'file_id':fid,'version_id':digest([fid,'version-one'])[:32],
                    'name':body['name'],'size':body['size'],'sha256':body['sha256'],
                    'uploader_id':actor['id'],'uploader_epoch':member['epoch'],'readers':readers,
                    'reader_epochs':{pid:room['members'][pid]['epoch'] for pid in readers},
                    'allow_agent_processing':consent,'ack_unscanned':True,'state':'awaiting_content',
                    'malware_scan':'not_run','revision':1,'expires_at':now+900}
                room['text_files'][fid]=file;response=metadata(file)
            else:
                file=room['text_files'].get(parts[0])
                require(file and file['state']!='deleted','attachment_not_found',404)
                require((actor['id']==file['uploader_id'] and member['epoch']==file['uploader_epoch'])
                        or ('room:control' in member['permissions'] and 'room:control' in actor['scopes']),'forbidden',403)
                action=parts[1]
                if action=='content':
                    require(file['state']=='awaiting_content' and now<file['expires_at'],'upload_expired_or_complete',409)
                    require(content is not None and len(content)==file['size'] and hashlib.sha256(content).hexdigest()==file['sha256'],'file_hash_or_size_mismatch',422)
                    validate_text(file['name'],content)
                    self.storage.write(file['version_id'],content,file['sha256'])
                    file['state']='content_received'
                elif action=='complete':
                    require(set(body)=={'expected_revision'} and body['expected_revision']==file['revision'],'file_revision_conflict',409)
                    require(file['state']=='content_received' and now<file['expires_at'],'upload_not_received',409)
                    validate_text(file['name'],self.storage.read(file['version_id'],file['sha256'],file['size']))
                    file['state']='validated_utf8_text'
                elif action=='delete':
                    require(set(body)=={'expected_revision'} and body['expected_revision']==file['revision'],'file_revision_conflict',409)
                    file['state']='deleted'
                elif action=='consent':
                    require(set(body)=={'expected_revision','readers','allow_agent_processing'} and body['expected_revision']==file['revision'],'file_revision_conflict',409)
                    file['readers'],file['allow_agent_processing']=self._sharing(room,body)
                    file['reader_epochs']={pid:room['members'][pid]['epoch'] for pid in file['readers']}
                else:raise Problem('not_found',404)
                file['revision']+=1;response=metadata(file)
            emit(room,'file.changed',file_id=file['file_id'],state=file['state'],actor_id=actor['id'],
                 operation=operation.split('/')[-1],revision=file['revision'],
                 readers=list(file['readers']),allow_agent_processing=file['allow_agent_processing'])
            return save_response(room,lookup,fingerprint,response,member['epoch'])

    def list(self,token,rid):
        with self.store.read_transaction(rid,token) as (actor,room):
            member=authorize(actor,room,'room:read')
            return [metadata(f)|{'can_manage':actor['kind']=='human' and (
                (actor['id']==f['uploader_id'] and member['epoch']==f['uploader_epoch']) or
                ('room:control' in actor['scopes'] and 'room:control' in member['permissions']))}
                for f in room.get('text_files',{}).values() if can_view_file(actor,room,member,f)]

    def human_read(self,token,rid,fid,mode):
        with self.store.read_transaction(rid,token) as (actor,room):
            member=authorize(actor,room,'room:read',True)
            file=room.get('text_files',{}).get(fid)
            require(file and can_view_file(actor,room,member,file),'attachment_not_found',404)
            require(file['state']=='validated_utf8_text' and file['malware_scan']=='not_run' and file['ack_unscanned'] is True,'attachment_not_ready',409)
            data=self.storage.read(file['version_id'],file['sha256'],file['size'])
            if mode=='download':return data,file['file_id']+'.txt'
            text=data.decode('utf-8')
            return {'ref':ref_of(file),'text':text[:MAX_CHARS],'truncated':len(text)>MAX_CHARS,
                    'untrusted_data':True,'malware_scan':'not_run'}

    def agent_read(self,token,rid,tid,fid,key,body):
        require(set(body)=={'generation','membership_revision','attempt','lease_proof','start','end'},'invalid_file_read_fields',422)
        with self.store.transaction(rid,token) as (actor,room):
            member=authorize(actor,room,'room:read');authorize(actor,room,'task:claim')
            require(actor['kind']=='agent','agent_required',403)
            task=room['tasks'].get(tid);fencing(room,member,task,actor,body,time.time())
            proof=body.get('lease_proof')
            require(isinstance(proof,str) and len(proof)==64 and task.get('_file_credential_binding')==token_hash(token)
                    and hmac.compare_digest(task.get('_file_lease_hash',''),token_hash(proof)),'lease_owner_required',403)
            file=room.get('text_files',{}).get(fid)
            require(file and ref_of(file) in task.get('attachment_refs',[]) and can_view_file(actor,room,member,file),'task_attachment_not_found',404)
            require(file['allow_agent_processing'] is True and actor['id'] in file['readers'],'agent_processing_consent_required',403)
            require(file['state']=='validated_utf8_text' and file['malware_scan']=='not_run','attachment_not_ready',409)
            require(isinstance(key,str) and 1<=len(key)<=128,'idempotency_key_required',422)
            fingerprint=digest([fid,body])
            reads=task.setdefault('_file_reads',{})
            if key in reads:
                require(reads[key]['fingerprint']==fingerprint,'idempotency_conflict',409)
                start,end=reads[key]['range']
            else:
                start,end=body['start'],body['end']
                require(type(start) is int and type(end) is int and 0<=start<end,'invalid_text_range',422)
                used=task.get('_file_chars',0)
                require(used<TASK_CHARS and len(reads)<32,'file_read_budget_exhausted',429)
                end=min(end,start+MAX_CHARS,start+TASK_CHARS-used)
            text=self.storage.read(file['version_id'],file['sha256'],file['size']).decode('utf-8')
            require(start<len(text),'invalid_text_range',422);end=min(end,len(text))
            if key not in reads:
                task['_file_chars']=task.get('_file_chars',0)+end-start
                reads[key]={'fingerprint':fingerprint,'range':[start,end]}
                emit(room,'file.read',file_id=fid,actor_id=actor['id'],task_id=tid,chars=end-start,
                     delivery='agent_context_not_provider_receipt')
            return {'ref':ref_of(file),'name':file['name'],'text':text[start:end],'start':start,'end':end,
                    'line_start':text.count('\n',0,start)+1,'line_end':text.count('\n',0,end-1)+1,
                    'truncated':end<len(text),'untrusted_data':True,'malware_scan':'not_run'}
