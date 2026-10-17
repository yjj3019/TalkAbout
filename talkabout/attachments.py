"""Unwired memory-only UTF-8 attachments. No real parser, scanner or transfer."""
from dataclasses import dataclass, replace
import hashlib
import threading
import time
import uuid

from .foundation_authority import (canonical, check_fence, digest, expiry,
                                   identifier, positive, require)


@dataclass(frozen=True)
class FileAccess:
    actor_id: str
    room_id: str
    membership_revision: int
    expires_at: float
    permissions: frozenset[str]
    kind: str = 'agent'
    provider_targets: frozenset[str] = frozenset()

    def __post_init__(self):
        identifier(self.actor_id); identifier(self.room_id)
        positive(self.membership_revision); expiry(self.expires_at)
        require(self.kind in {'human', 'agent'}, 'invalid_actor_kind')
        object.__setattr__(self, 'permissions', frozenset(self.permissions))
        object.__setattr__(self, 'provider_targets', frozenset(self.provider_targets))


@dataclass(frozen=True)
class FileRef:
    file_id: str
    version_id: str
    sha256: str


@dataclass(frozen=True)
class FileVersion:
    ref: FileRef
    room_id: str
    uploader_id: str
    display_name: str
    media_type: str
    size: int
    state: str = 'quarantine'
    extraction_state: str = 'not_run'
    readers: frozenset[str] = frozenset()
    provider_agents: frozenset[str] = frozenset()
    revision: int = 1
    untrusted_data: bool = True
    provenance: str = 'synthetic_fixture'


@dataclass(frozen=True)
class MessageSnapshot:
    message_id: str
    room_id: str
    author_id: str
    revision: int
    readers: frozenset[str]
    state: str = 'draft'

    def __post_init__(self):
        for value in (self.message_id, self.room_id, self.author_id):identifier(value)
        positive(self.revision)
        object.__setattr__(self, 'readers', frozenset(self.readers))
        require(self.state in {'draft','published','deleted'},'invalid_message_state')


@dataclass(frozen=True)
class TextChunk:
    ref: FileRef
    text: str
    start: int
    end: int
    line_start: int
    line_end: int
    truncated: bool
    untrusted_data: bool = True
    provenance: str = 'synthetic_utf8_fixture'


class FakeFileInspector:
    """Explicit synthetic outcomes, never a malware safety assertion."""
    def __init__(self, verdict='passed_fixture', extraction='ready'):
        require(verdict in {'passed_fixture', 'unknown', 'rejected', 'failed'}, 'invalid_scan_state')
        require(extraction in {'ready', 'failed'}, 'invalid_extraction_state')
        self.verdict, self.extraction = verdict, extraction

    def inspect(self, content):
        return hashlib.sha256(content).hexdigest(), self.verdict, self.extraction


class MemoryFileStore:
    def __init__(self):
        self.versions, self.blobs, self.texts, self.upload_keys, self.messages = {}, {}, {}, {}, {}
        self.budgets, self.transfers = {}, []
        self.lock = threading.RLock()


class AttachmentService:
    extensions = frozenset({'.txt', '.md', '.py', '.js'})

    def __init__(self, store, resolve_access, resolve_authority, resolve_message,
                 resolve_task_messages, resolve_capabilities, clock=time.time,
                 max_bytes=1048576, max_chunk=8000, task_chars=24000, capacity=128):
        require(type(store) is MemoryFileStore, 'persistent_storage_not_enabled')
        for value, maximum in ((max_bytes,1048576),(max_chunk,8000),(task_chars,24000),(capacity,1024)):
            positive(value, maximum)
        self.store, self.resolve_access, self.resolve_authority = store, resolve_access, resolve_authority
        self.resolve_message, self.resolve_task_messages = resolve_message, resolve_task_messages
        self.resolve_capabilities, self.clock = resolve_capabilities, clock
        self.max_bytes, self.max_chunk, self.task_chars, self.capacity = max_bytes, max_chunk, task_chars, capacity

    def _access(self, actor_id, room_id, permission):
        access = self.resolve_access(actor_id, room_id)
        require(isinstance(access, FileAccess) and access.actor_id==actor_id and access.room_id==room_id,
                'file_not_found')
        require(access.expires_at > self.clock() and permission in access.permissions, 'file_access_denied')
        return access

    def _version(self, ref, room_id):
        require(isinstance(ref, FileRef), 'file_not_found')
        version = self.store.versions.get(ref.version_id)
        require(version is not None and version.ref==ref and version.room_id==room_id, 'file_not_found')
        require(version.state != 'deleted', 'file_not_found')
        return version

    def _visible(self, actor_id, room_id, ref, ready=True):
        self._access(actor_id,room_id,'file:read')
        version = self._version(ref,room_id)
        require(actor_id in version.readers, 'file_not_found')
        if ready:require(version.state=='fixture_ready' and version.extraction_state=='ready', 'file_not_ready')
        return version

    def upload(self, actor_id, room_id, name, content, media_type, readers, key, provider_agents=(), fence=None):
        """Bytes are synthetic inputs, not read from a host file path."""
        identifier(key)
        require(isinstance(name,str) and 1<=len(name)<=120 and not name.startswith('.')
                and not any(c in name for c in '/\\:\x00') and not any(ord(c)<32 for c in name), 'invalid_filename')
        require(name.lower().endswith(tuple(self.extensions)) and name.count('.')==1, 'unsupported_file_type')
        require(media_type=='text/plain', 'media_type_mismatch')
        require(type(content) is bytes and 0<len(content)<=self.max_bytes, 'file_size_limit')
        try:text=content.decode('utf-8',errors='strict')
        except UnicodeDecodeError:require(False,'invalid_utf8')
        require(not any(ord(c)<32 and c not in '\n\r\t' for c in text), 'binary_content_denied')
        reader_set=frozenset(readers)|{actor_id};provider_set=frozenset(provider_agents)
        require(len(reader_set)<=16 and provider_set<=reader_set, 'invalid_file_sharing')
        for value in reader_set:identifier(value)
        fingerprint=digest(canonical({'name':name,'sha256':hashlib.sha256(content).hexdigest(),
            'media_type':media_type,'readers':sorted(reader_set),'provider_agents':sorted(provider_set)}))
        with self.store.lock:
            access=self._access(actor_id,room_id,'file:upload')
            if access.kind=='agent':
                require(fence is not None and fence.agent_id==actor_id and fence.room_id==room_id,'task_required')
                check_fence(fence,self.resolve_authority,self.clock())
                require(fence.membership_revision==access.membership_revision,'file_access_changed')
            if provider_set:
                consent=self._access(actor_id,room_id,'file:manage')
                require(consent.kind=='human','human_transfer_consent_required')
            for reader in reader_set:self._access(reader,room_id,'file:read')
            lookup=(actor_id,room_id,key)
            if lookup in self.store.upload_keys:
                prior_hash, ref=self.store.upload_keys[lookup]
                require(prior_hash==fingerprint,'idempotency_conflict')
                self._version(ref,room_id)
                return ref
            require(len(self.store.versions)<self.capacity,'file_capacity')
            ref=FileRef(uuid.uuid4().hex,uuid.uuid4().hex,hashlib.sha256(content).hexdigest())
            version=FileVersion(ref,room_id,actor_id,name,media_type,len(content),readers=reader_set,provider_agents=provider_set)
            self.store.versions[ref.version_id]=version;self.store.blobs[ref.version_id]=content
            self.store.upload_keys[lookup]=(fingerprint,ref)
            return ref

    def inspect_fixture(self, ref, inspector):
        """Trusted test-host operation. Never exposed as an agent or public API."""
        require(type(inspector) is FakeFileInspector,'real_inspectors_disabled')
        with self.store.lock:
            version=self.store.versions.get(ref.version_id)
            require(version is not None and version.ref==ref and version.state=='quarantine','invalid_transition')
            actual,verdict,extraction=inspector.inspect(self.store.blobs[ref.version_id])
            require(actual==ref.sha256,'file_hash_mismatch')
            state='fixture_ready' if verdict=='passed_fixture' else verdict
            extraction_state=extraction if verdict=='passed_fixture' else 'not_run'
            if state=='fixture_ready' and extraction_state=='ready':
                self.store.texts[ref.version_id]=self.store.blobs[ref.version_id].decode('utf-8')
            self.store.versions[ref.version_id]=replace(version,state=state,extraction_state=extraction_state,revision=version.revision+1)
            return self.store.versions[ref.version_id]

    def metadata(self, actor_id, room_id, ref):
        with self.store.lock:
            version=self._visible(actor_id,room_id,ref,ready=False)
            return version

    def share(self, actor_id, room_id, ref, readers, provider_agents, expected_revision):
        with self.store.lock:
            manager=self._access(actor_id,room_id,'file:manage')
            version=self._version(ref,room_id)
            require((version.uploader_id==actor_id or manager.kind=='human') and version.revision==expected_revision,'file_revision_conflict')
            reader_set=frozenset(readers)|{actor_id};provider_set=frozenset(provider_agents)
            require(len(reader_set)<=16 and provider_set<=reader_set,'invalid_file_sharing')
            require(not provider_set or manager.kind=='human','human_transfer_consent_required')
            for reader in reader_set:self._access(reader,room_id,'file:read')
            updated=replace(version,readers=reader_set,provider_agents=provider_set,revision=version.revision+1)
            self.store.versions[ref.version_id]=updated
            return updated

    def delete(self, actor_id, room_id, ref, expected_revision):
        with self.store.lock:
            manager=self._access(actor_id,room_id,'file:manage')
            version=self._version(ref,room_id)
            require((version.uploader_id==actor_id or manager.kind=='human') and version.revision==expected_revision,'file_revision_conflict')
            self.store.versions[ref.version_id]=replace(version,state='deleted',revision=version.revision+1)
            self.store.blobs.pop(ref.version_id,None);self.store.texts.pop(ref.version_id,None)

    def attach_message(self, actor_id, room_id, message_id, refs):
        refs=tuple(refs)
        require(0<len(refs)<=4 and len(set(refs))==len(refs),'attachment_count_limit')
        with self.store.lock:
            self._access(actor_id,room_id,'file:attach')
            message=self.resolve_message(message_id)
            require(isinstance(message,MessageSnapshot) and message.room_id==room_id and message.author_id==actor_id
                    and message.state=='draft' and actor_id in message.readers,'message_not_found')
            for ref in refs:self._visible(actor_id,room_id,ref)
            binding=(room_id,message.revision,refs)
            previous=self.store.messages.get(message_id)
            require(previous is None or previous==binding,'message_attachment_conflict')
            require(previous is not None or len(self.store.messages)<self.capacity,'message_capacity')
            self.store.messages[message_id]=binding
            return refs

    def _task_access(self, fence):
        check_fence(fence,self.resolve_authority,self.clock())
        access=self._access(fence.agent_id,fence.room_id,'file:retrieve')
        require(access.kind=='agent' and access.membership_revision==fence.membership_revision,'file_access_changed')
        return access

    def task_context(self, fence):
        with self.store.lock:
            self._task_access(fence)
            message_ids=tuple(self.resolve_task_messages(fence))
            require(len(message_ids)<=8,'task_attachment_limit')
            versions={}
            for message_id in message_ids:
                message=self.resolve_message(message_id)
                require(isinstance(message,MessageSnapshot) and message.room_id==fence.room_id
                        and fence.agent_id in message.readers and message.state=='published','message_not_found')
                binding=self.store.messages.get(message_id)
                if binding is None:continue
                require(binding[:2]==(message.room_id,message.revision),'message_attachment_changed')
                for ref in binding[2]:
                    version=self.store.versions.get(ref.version_id)
                    if version is None or version.state=='deleted' or fence.agent_id not in version.readers:continue
                    self._visible(fence.agent_id,fence.room_id,ref,ready=False)
                    versions[ref.version_id]=version
            require(len(versions)<=8,'task_attachment_limit')
            self._task_access(fence)
            return tuple(versions.values())

    def _task_file(self, fence, ref):
        require(any(v.ref==ref for v in self.task_context(fence)),'task_file_not_found')
        return self._visible(fence.agent_id,fence.room_id,ref)

    def retrieve(self, fence, ref, start, end, representation='text'):
        require(representation=='text','unsupported_representation')
        with self.store.lock:
            self._task_file(fence,ref)
            require('text' in self.resolve_capabilities(fence.agent_id),'runtime_text_unsupported')
            text=self.store.texts[ref.version_id]
            require(type(start) is int and type(end) is int and 0<=start<end<=len(text),'invalid_text_range')
            budget=fence.identity()
            require(budget in self.store.budgets or len(self.store.budgets)<self.capacity,'retrieval_budget_capacity')
            used=self.store.budgets.get(budget,0)
            remaining=self.task_chars-used
            require(remaining>0,'retrieval_budget_exhausted')
            actual_end=min(end,start+self.max_chunk,start+remaining)
            chunk=TextChunk(ref,text[start:actual_end],start,actual_end,text.count('\n',0,start)+1,
                            text.count('\n',0,actual_end-1)+1,actual_end<end)
            self._task_file(fence,ref)
            self.store.budgets[budget]=used+len(chunk.text)
            return chunk

    def download(self, actor_id, room_id, ref, fence=None):
        with self.store.lock:
            access=self._access(actor_id,room_id,'file:read_original')
            self._visible(actor_id,room_id,ref)
            if access.kind=='agent':
                require(fence is not None and fence.agent_id==actor_id and fence.room_id==room_id,'task_required')
                self._task_file(fence,ref)
                require('file' in self.resolve_capabilities(actor_id),'runtime_file_unsupported')
            content=self.store.blobs[ref.version_id]
            if access.kind=='agent':self._task_file(fence,ref)
            self._visible(actor_id,room_id,ref)
            self._access(actor_id,room_id,'file:read_original')
            return content

    def search(self, fence, query, limit=4):
        """Bounded literal search only across the task's currently visible files."""
        require(isinstance(query,str) and 1<=len(query)<=100,'invalid_search')
        positive(limit,4)
        with self.store.lock:
            require('text' in self.resolve_capabilities(fence.agent_id),'runtime_text_unsupported')
            self._task_access(fence)
            require(self.store.budgets.get(fence.identity(),0)<self.task_chars,'retrieval_budget_exhausted')
            hits=[]
            for version in self.task_context(fence):
                if self.store.budgets.get(fence.identity(),0)>=self.task_chars:break
                if version.state!='fixture_ready' or version.extraction_state!='ready':continue
                text=self.store.texts[version.ref.version_id]
                start=text.find(query)
                if start>=0:
                    hits.append(self.retrieve(fence,version.ref,start,len(text)))
                if len(hits)>=limit:break
            self._task_access(fence)
            return tuple(hits)

    def preview(self, actor_id, room_id, ref, fence=None):
        # Exact same original/readiness/room ACL gate. Return inert text, never HTML.
        return self.download(actor_id,room_id,ref,fence).decode('utf-8')[:self.max_chunk]

    def prepare_provider_input(self, fence, ref, start, end, provider_target):
        identifier(provider_target)
        with self.store.lock:
            access=self._task_access(fence)
            require('file:send_provider' in access.permissions and provider_target in access.provider_targets,'provider_transfer_denied')
            version=self._task_file(fence,ref)
            require(fence.agent_id in version.provider_agents,'provider_transfer_not_consented')
            require(len(self.store.transfers)<self.capacity,'transfer_capacity')
            chunk=self.retrieve(fence,ref,start,end)
            current=self._task_access(fence)
            version=self._task_file(fence,ref)
            require('file:send_provider' in current.permissions and provider_target in current.provider_targets
                    and fence.agent_id in version.provider_agents,'provider_transfer_denied')
            self.store.transfers.append((fence,ref,provider_target,chunk.start,chunk.end,'prepared_not_sent'))
            return chunk
