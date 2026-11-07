// Synthetic DOM/fetch only: no provider, browser or server process.
// Manual memory-preview contract: one explicit read-only POST, no store/approve/model call,
// inert text rendering, and content removal on close / room change / sign-out.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ViewState,labels} from '../talkabout/web/state.mjs';
import {createCollaborationBoard} from '../talkabout/web/collaboration-board.mjs';

const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
const html=readFileSync(new URL('../talkabout/web/index.html',import.meta.url),'utf8');
const stateSource=readFileSync(new URL('../talkabout/web/state.mjs',import.meta.url),'utf8');

function harness(){
 class Node{
  constructor(tag='div'){this.tagName=tag;this.value='';this.children=[];this.ownText='';this.type='password';this.hidden=false;this.open=false;this.disabled=false;this.listeners={};this.classList={add(){},remove(){}};this.dataset={};this.attributes={};}
  set textContent(v){this.ownText=v;this.children=[];}get textContent(){return this.ownText+this.children.map(n=>n.textContent??n).join('');}
  set innerHTML(v){this.html=v;}append(...v){this.children.push(...v);}replaceChildren(...v){this.ownText='';this.children=v;}get options(){return this.children;}get selectedOptions(){return this.children.filter(n=>n.value===this.value).slice(0,1);}
  close(){this.open=false;}showModal(){this.open=true;}addEventListener(k,v){this.listeners[k]=v;}querySelector(){return new Node();}
  setAttribute(k,v){this.attributes[k]=v;}getAttribute(k){return this.attributes[k];}removeAttribute(){}scrollIntoView(){}click(){this.clicked=true;}focus(){this.focused=true;}replaceWith(){}remove(){}
 }
 const nodes=new Map(),calls=[],handlers=new Map(),created=[];
 const node=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
 const context=vm.createContext({ViewState,labels,createCollaborationBoard,crypto:globalThis.crypto,AbortController,URLSearchParams,Blob,
  document:{getElementById:node,createElement:tag=>{const n=new Node(tag);created.push(tag);return n;},querySelectorAll:()=>[],documentElement:{}},
  window:{setInterval:()=>1,clearInterval(){},setTimeout:f=>{f();return 1;},clearTimeout(){},addEventListener(){}},
  location:{origin:'https://synthetic.invalid',search:''},history:{pushState(){},replaceState(){}},confirm:()=>true,
  navigator:{clipboard:{writeText:async()=>{}}},
  URL:{createObjectURL:()=>'blob:synthetic',revokeObjectURL(){}},
  EventSource:class{addEventListener(){}close(){}},
  fetch:async(url,options={})=>{const path=url.replace('/api/v1/','');calls.push({path,options});const handler=handlers.get(path);
   if(handler)return handler(options);
   const value=path==='setup/status'?{setup_required:true}:path==='setup/csrf'?{csrf_token:'synthetic'}:[];return {ok:true,status:200,json:async()=>value};}});
 const run=code=>vm.runInContext(code,context);run(source);run("user={id:'human',scopes:['room:read']};agents=[];view.language='ko';");
 const respond=(path,data,status=200)=>handlers.set(path,async()=>({ok:status<400,status,json:async()=>data}));
 const pending=(path,data)=>{let resolve;handlers.set(path,()=>new Promise(done=>{resolve=()=>done({ok:true,status:200,json:async()=>data});}));return ()=>resolve();};
 const previewCalls=()=>calls.filter(call=>call.path==='rooms/room/memories/preview');
 return {node,run,calls,handlers,respond,pending,previewCalls,created};
}

const room=(id='room',permissions=['room:read','room:control'],revision=1)=>({id,topic:'Synthetic room',state:'active',mode:'bounded_rounds',mode_status:'running',generation:1,room_revision:1,limits:{rounds:1},members:{},messages:[],tasks:[],artifacts:[],membership_revision:revision,dispatch_held:false,mode_progress:{},effective_permissions:permissions});
const candidate=(text='Candidate memory text',extra={})=>({original_text:text,truncated:false,source:{message_id:'m-1',seq:12,actor_id:'human',actor_kind:'human'},repetition:{count:1,sources:[]},...extra});
const src=(seq=12,messageId='m-1',actorId='a-1',actorKind='human')=>({message_id:messageId,seq,actor_id:actorId,actor_kind:actorKind});
const items=h=>h.node('memory-preview-list').children;
const detailsOf=item=>item.children.find(child=>child.tagName==='details');
const preview=(candidates=[candidate()],counts={returned:1,omitted_groups:0},extra={excluded_attachment_messages:0,excluded_agent_messages:0})=>({mode:'preview_only',candidates,counts,...extra});
const tick=()=>new Promise(setImmediate);

async function opened(){
 const h=harness();
 h.respond('rooms/room',room());
 h.respond('rooms/room/interventions',[]);
 await h.run("openRoom('room',false,false)");
 return h;
}

test('preview is not loaded automatically but is available as an explicit read-only action',async()=>{
 const h=await opened();
 assert.equal(h.node('memory-preview-panel').hidden,false);
 assert.equal(h.previewCalls().length,0);
 assert.equal(h.node('memory-preview-list').textContent,'');
 assert.equal(h.node('memory-preview-load').disabled,false);
 assert.equal(h.node('memory-preview-clear').hidden,true);
 await h.run("loadMemoryPreview()");
 const calls=h.previewCalls();
 assert.equal(calls.length,1);
 assert.equal(calls[0].options.method,'POST');
 assert.equal(calls[0].options.body,'{}');
 assert.equal(calls[0].options.headers['X-CSRF-Token'],undefined);
});

test('viewers need both room read and room control to see or request the preview',async()=>{
 for(const permissions of [['room:read'],['room:control'],[]]){
  const h=harness();
  h.respond('rooms/room',room('room',permissions));
  h.respond('rooms/room/interventions',[]);
  await h.run("openRoom('room',false,false)");
  assert.equal(h.node('memory-preview-panel').hidden,true,JSON.stringify(permissions)+' panel hidden');
  await h.run("loadMemoryPreview()");
  assert.equal(h.previewCalls().length,0,JSON.stringify(permissions)+' sends no request');
 }
});

test('losing room read or control clears already rendered preview content',async()=>{
 for(const remaining of [['room:read'],['room:control']]){
  const h=await opened();
  h.respond('rooms/room/memories/preview',preview([candidate('With sources',{repetition:{count:2,sources:[src(51,'m-51','a-51'),src(52,'m-52','a-52')]}})]));
  h.node('memory-preview-load').onclick();await tick();await tick();
  assert.notEqual(h.node('memory-preview-list').textContent,'');
  assert.ok(detailsOf(items(h)[0]),'collapsed source DOM exists before permission loss');
  h.run("view.room.effective_permissions="+JSON.stringify(remaining)+";renderRoom(view.room);");
  assert.equal(h.node('memory-preview-panel').hidden,true,JSON.stringify(remaining)+' panel hidden');
  assert.equal(h.node('memory-preview-list').textContent,'');
  assert.equal(h.node('memory-preview-list').children.length,0,JSON.stringify(remaining)+' collapsed source DOM removed');
  assert.equal(h.node('memory-preview-counts').textContent,'');
 }
});

test('explicit preview renders candidates as inert text with provenance and counts',async()=>{
 const h=await opened();
 const text='<img src=x onerror="alert(1)"><b>not markup</b>';
 h.respond('rooms/room/memories/preview',preview([candidate(text)]));
 h.node('memory-preview-load').onclick();
 await tick();await tick();
 const list=h.node('memory-preview-list');
 assert.equal(list.textContent.includes('<img src=x onerror="alert(1)">'),true);
 assert.equal(list.textContent.includes('<b>not markup</b>'),true);
 assert.equal(list.innerHTML,undefined,'candidate text must never be assigned as HTML');
 assert.equal(h.created.some(tag=>['img','b','script'].includes(tag)),false,'candidate text must not create elements');
 assert.ok(list.textContent.includes('#12'));
 assert.ok(list.textContent.includes(labels.ko.humanBadge));
 assert.ok(list.textContent.includes(labels.ko.memoryPreviewPreviewOnly));
 const counts=h.node('memory-preview-counts').textContent;
 assert.ok(counts.includes(labels.ko.memoryPreviewReturned+': 1'));
 assert.ok(counts.includes(labels.ko.memoryPreviewExcluded+': 0'));
 assert.ok(counts.includes(labels.ko.memoryPreviewExcludedAgent+': 0'));
 assert.equal(h.node('memory-preview-status').textContent,'');
 assert.equal(h.node('memory-preview-clear').hidden,false);
 assert.equal(h.calls.filter(call=>call.options.method==='POST').length,1,'no store/approve request may be sent');
});

test('the screen names preview-only scope and offers no save or approve control',async()=>{
 assert.match(html,/data-i18n="memoryPreviewScope"/);
 assert.match(html,/data-i18n="memoryPreviewProductBoundary"/);
 assert.match(html,/data-i18n="memoryPreviewUntrusted"/);
 assert.match(html,/data-i18n="memoryPreviewPrivacy"/);
 assert.match(html,/id="memory-preview-load" type="button"/);
 assert.match(html,/id="memory-preview-clear" type="button" data-i18n="memoryPreviewClear" hidden/);
 assert.match(html,/id="memory-preview-status" class="field-help" role="status" aria-live="polite"/);
 assert.match(html,/aria-labelledby="memory-preview-title"/);
 assert.doesNotMatch(html,/memory-preview-(?:save|approve|apply|store)/);
 for(const language of ['ko','en'])for(const key of ['memoryPreviewScope','memoryPreviewProductBoundary','memoryPreviewUntrusted','memoryPreviewPrivacy','memoryPreviewPreviewOnly','memoryPreviewExcluded','memoryPreviewExcludedAgent'])assert.ok(labels[language][key],language+' missing '+key);
 assert.ok(labels.ko.memoryPreviewScope.includes('human 메시지'));
 assert.ok(labels.ko.memoryPreviewScope.includes('에이전트 메시지'));
 assert.ok(labels.ko.memoryPreviewScope.includes('마스킹'));
 assert.ok(labels.ko.memoryPreviewScope.includes('room:control')&&labels.ko.memoryPreviewScope.includes('room:read'));
 assert.ok(labels.ko.memoryPreviewScope.includes('같은 workspace'));
 assert.ok(!labels.ko.memoryPreviewScope.includes('소유자'),'permission copy must not claim room-owner semantics');
 assert.ok(labels.ko.memoryPreviewScope.includes('저장')&&labels.ko.memoryPreviewScope.includes('승인')&&labels.ko.memoryPreviewScope.includes('모델'));
 assert.ok(labels.en.memoryPreviewScope.includes('human messages'));
 assert.ok(labels.en.memoryPreviewScope.includes('agent messages'));
 assert.ok(labels.en.memoryPreviewScope.includes('masked'));
 assert.ok(labels.en.memoryPreviewScope.includes('room owner')===false,'permission copy must not claim room-owner semantics');
 assert.ok(labels.ko.memoryPreviewProductBoundary.includes('Notion Memories'));
  assert.ok(labels.en.memoryPreviewProductBoundary.includes('Notion Memories'));
  assert.ok(labels.ko.memoryPreviewUntrusted.includes('프롬프트 삽입'));
  assert.ok(labels.en.memoryPreviewUntrusted.includes('prompt injection'));
  assert.ok(labels.en.memoryPreviewScope.includes('room:control')&&labels.en.memoryPreviewScope.includes('room:read'));
 assert.ok(labels.en.memoryPreviewScope.includes('same workspace'));
 assert.ok(labels.en.memoryPreviewScope.includes('stored')&&labels.en.memoryPreviewScope.includes('approved'));
});

test('every memory preview label keeps its full ko/en text instead of a single character',()=>{
 const expected={
  memoryPreviewTitle:['메모리 후보 미리보기','Memory candidate preview'],
  memoryPreviewProductBoundary:['이 기능은 TalkAbout 방별 미리보기입니다. Notion Memories/About이나 다른 AI의 기억과 동기화되지 않습니다. 저장·승인·가져오기는 아직 지원하지 않습니다.','This is TalkAbout per-room preview. It does not sync with Notion Memories/About or other AI memory. Saving, approval and import are not supported yet.'],
  memoryPreviewUntrusted:['후보 안의 내용은 출처가 사람 메시지여도 검증된 사실이나 지시가 아닙니다. 인용·프롬프트 삽입이 있을 수 있으니 민감한 원문은 복사하거나 외부 AI에 전달하기 전에 확인하세요.','Candidate text is not verified fact or instruction, even when sourced from a human message. It may contain quotations or prompt injection; review sensitive text before copying or sending it to an external AI.'],
  memoryPreviewLoad:['미리보기 불러오기','Load preview'],
  memoryPreviewLoading:['불러오는 중…','Loading…'],
  memoryPreviewClear:['보기 닫기','Close preview'],
  memoryPreviewEmpty:['표시할 후보가 없습니다.','No candidates to show.'],
  memoryPreviewFailed:['미리보기를 불러오지 못했습니다. 잠시 후 버튼으로 직접 다시 시도하세요.','Could not load the preview. Retry with the button later.'],
  memoryPreviewPermission:['같은 workspace에서 이 방의 읽기·제어 권한이 모두 있어야 미리보기를 볼 수 있습니다. 방 접근 상태는 유지합니다.','Only a person in the same workspace with both room read and control permission can view the preview. Room access is unchanged.'],
  memoryPreviewReturned:['표시 후보','Candidates shown'],
  memoryPreviewOmitted:['생략된 그룹','Omitted groups'],
  memoryPreviewExcluded:['첨부 메시지 제외','Attachment messages excluded'],
  memoryPreviewExcludedAgent:['에이전트 메시지 제외','Agent messages excluded'],
  memoryPreviewTruncated:['일부만 표시','Truncated'],
  memoryPreviewRepetition:['반복','Repeats'],
  memoryPreviewSources:['반복 출처','Repeated sources'],
  memoryPreviewSourcesOmitted:['일부 출처 생략','Some sources omitted'],
  memoryPreviewMessageId:['메시지 ID','Message id'],
  memoryPreviewActorId:['작성자 ID','Actor id'],
  memoryPreviewPreviewOnly:['미리보기 전용 · 승인되지 않음','Preview only · not approved']
 };
 for(const [key,pair] of Object.entries(expected)){assert.equal(labels.ko[key],pair[0],key+' ko full text');assert.equal(labels.en[key],pair[1],key+' en full text');}
 const keys=Object.keys(labels.ko).filter(key=>key.startsWith('memoryPreview'));
 assert.ok(keys.length>=20,'memory preview label set is incomplete');
 for(const key of keys){
  assert.equal(typeof labels.ko[key],'string',key+' ko value type');
  assert.equal(typeof labels.en[key],'string',key+' en value type');
  assert.ok(labels.ko[key].length>1,key+' ko must not be truncated to one character');
  assert.ok(labels.en[key].length>1,key+' en must not be truncated to one character');
  assert.notEqual(labels.ko[key],labels.en[key],key+' must define separate ko/en text');
  assert.match(stateSource,new RegExp(key+':\\s*\\['),key+' must be declared as a [ko,en] pair array');
 }
});

test('truncated repetition sources and untrusted source ids are shown as inert bounded text',async()=>{
 const h=await opened();
 const hostileId='<img src=x onerror=alert(1)>'+'x'.repeat(200);
 h.respond('rooms/room/memories/preview',preview([
  candidate('First',{source:{message_id:hostileId,seq:7,actor_id:'actor-9',actor_kind:'human'},repetition:{count:4,sources_truncated:true,sources:['m-1']}}),
  candidate('Second',{source:{message_id:'',seq:8,actor_id:null,actor_kind:'human'},repetition:{count:1,sources_truncated:false,sources:[]}})
 ]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 const list=h.node('memory-preview-list'),first=list.children[0].textContent,second=list.children[1].textContent;
 assert.ok(first.includes(labels.ko.memoryPreviewSourcesOmitted),'truncated sources are labeled in Korean');
 assert.ok(first.includes(labels.ko.memoryPreviewMessageId),'message id is labeled in Korean');
 assert.ok(first.includes(labels.ko.memoryPreviewActorId)&&first.includes('actor-9'),'actor id is shown in Korean');
 assert.ok(!first.includes(hostileId),'hostile id is capped before rendering');
 assert.ok(first.length<420,'bounded metadata keeps the row small');
 assert.ok(!second.includes(labels.ko.memoryPreviewSourcesOmitted),'complete provenance is not labeled as omitted');
 assert.ok(!second.includes(labels.ko.memoryPreviewMessageId)&&!second.includes(labels.ko.memoryPreviewActorId),'absent ids keep only seq metadata');
 h.run("view.language='en';renderRoom(view.room);");
 const en=h.node('memory-preview-list').textContent;
 assert.ok(en.includes(labels.en.memoryPreviewSourcesOmitted),'truncated sources are labeled in English');
 assert.ok(en.includes(labels.en.memoryPreviewMessageId)&&en.includes(labels.en.memoryPreviewActorId),'ids are labeled in English');
 assert.equal(h.created.some(tag=>['img','b','script'].includes(tag)),false,'untrusted ids must not create elements');
});

test('repeated sources render as a collapsed native disclosure in both languages',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',preview([candidate('Repeated',{repetition:{count:3,sources:[src(31,'m-31','a-31'),src(32,'m-32','a-32'),src(33,'m-33','a-33')]}})]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 const details=detailsOf(items(h)[0]);
 assert.ok(details,'sources disclosure exists');
 assert.equal(details.open,false,'disclosure is collapsed by default');
 assert.equal(details.children[0].tagName,'summary');
 assert.equal(details.children[0].textContent,labels.ko.memoryPreviewSources+' (3)');
 const rows=details.children[1];
 assert.equal(rows.tagName,'ul');
 assert.equal(rows.children.length,3);
 assert.ok(rows.children[0].textContent.includes('#31'));
 assert.ok(rows.children[0].textContent.includes(labels.ko.memoryPreviewMessageId+' m-31'));
 assert.ok(rows.children[2].textContent.includes(labels.ko.memoryPreviewActorId+' a-33'));
 h.run("view.language='en';renderRoom(view.room);");
 const enDetails=detailsOf(items(h)[0]);
 assert.equal(enDetails.children[0].textContent,labels.en.memoryPreviewSources+' (3)');
 assert.ok(enDetails.textContent.includes(labels.en.memoryPreviewMessageId+' m-31'));
 assert.ok(enDetails.textContent.includes(labels.en.memoryPreviewActorId+' a-31'));
});

test('a single-source repetition keeps only representative metadata unless the count is higher',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',preview([
  candidate('Only one',{repetition:{count:1,sources:[src(41,'m-41','a-41')]}}),
  candidate('Repeated but truncated',{repetition:{count:4,sources_truncated:true,sources:[src(42,'m-42','a-42')]}}),
  candidate('Two sources',{repetition:{count:2,sources:[src(43,'m-43','a-43'),src(44,'m-44','a-44')]}})
 ]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 const rows=items(h);
 assert.equal(detailsOf(rows[0]),undefined,'count 1 with one source does not duplicate the representative row');
 assert.ok(detailsOf(rows[1]),'count>1 with one visible source is still disclosed');
 assert.equal(detailsOf(rows[1]).children[1].children.length,1);
 assert.ok(rows[1].textContent.includes(labels.ko.memoryPreviewSourcesOmitted),'truncation warning stays visible');
 assert.equal(detailsOf(rows[2]).children[1].children.length,2);
});

test('repeated sources are capped at 20 entries and excess is labeled',async()=>{
 const h=await opened();
 const many=Array.from({length:25},(_,i)=>src(100+i,'m-'+i,'a-'+i));
 h.respond('rooms/room/memories/preview',preview([candidate('Many repeats',{repetition:{count:25,sources_truncated:false,sources:many}})]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 const details=detailsOf(items(h)[0]),rows=details.children[1];
 assert.equal(rows.children.length,20,'only 20 source rows are rendered');
 assert.ok(rows.children[19].textContent.includes('#119'));
 assert.equal(rows.textContent.includes('m-20'),false,'entries beyond the client cap are not rendered');
 assert.ok(items(h)[0].textContent.includes(labels.ko.memoryPreviewSourcesOmitted),'client-side cap is labeled as omission');
});

test('malformed traversal is also capped at 20 entries and the omission is labeled',async()=>{
 const h=await opened();
 const sources=[null,'bad',...Array.from({length:18},(_,i)=>src(200+i,'m-'+i,'a-'+i)),...Array.from({length:5},(_,i)=>src(300+i,'m-'+(20+i),'a-'+(20+i)))];
 h.respond('rooms/room/memories/preview',preview([candidate('Mixed entries',{repetition:{count:25,sources_truncated:false,sources}})]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 const details=detailsOf(items(h)[0]),rows=details.children[1];
 assert.equal(sources.length,25,'the synthetic payload is deliberately oversized');
 assert.equal(rows.children.length,18,'only entries within the first 20 are visited, malformed ones are skipped');
 assert.equal(rows.textContent.includes('m-20'),false,'entries after the 20-entry traversal cap are not rendered');
 assert.ok(items(h)[0].textContent.includes(labels.ko.memoryPreviewSourcesOmitted),'the out-of-cap entries are labeled as omitted');
});

test('source rows stay inert, bounded and skip malformed entries',async()=>{
 const h=await opened();
 const hostile='<img src=x onerror=alert(1)>'+'y'.repeat(200);
 h.respond('rooms/room/memories/preview',preview([
  candidate('Hostile ids',{repetition:{count:5,sources_truncated:true,sources:[{message_id:hostile,seq:5,actor_id:hostile,actor_kind:'agent'},null,'not-an-object',{seq:'bad',actor_kind:'other'}]}}),
  candidate('No array',{repetition:{count:3,sources:'not-an-array'}})
 ]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 const rows=items(h),details=detailsOf(rows[0]);
 assert.ok(details);
 const sources=details.children[1];
 assert.equal(sources.children.length,1,'entries without a safe seq or message id are skipped');
 const first=sources.children[0].textContent;
 assert.equal(first.includes(hostile),false,'hostile id is capped before rendering');
 assert.ok(first.includes(labels.ko.memoryPreviewMessageId));
 assert.ok(first.includes(labels.ko.agentBadge));
 assert.equal(sources.textContent.includes('#?'),false,'skipped malformed entries add no placeholder row');
 assert.equal(detailsOf(rows[1]),undefined,'non-array sources create no disclosure');
 assert.equal(h.created.some(tag=>['img','b','script'].includes(tag)),false,'untrusted ids never create elements');
});

test('sources without a safe seq or non-empty message id are skipped and flagged',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',preview([candidate('Empty objects',{repetition:{count:5,sources:[{},{seq:Number.MAX_VALUE,actor_id:'a-1'},[],src(80,'m-80','a-80'),{message_id:'m-81'}]}})]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 const item=items(h)[0],rows=detailsOf(item).children[1];
 assert.equal(rows.children.length,2,'empty objects, unsafe seq and arrays are treated as malformed');
 assert.equal(rows.textContent.includes('#?'),false,'an empty object never renders as a placeholder source row');
 assert.ok(rows.textContent.includes('#80'));
 assert.ok(rows.textContent.includes('m-81'),'a message-id-only source stays visible');
 assert.ok(item.textContent.includes(labels.ko.memoryPreviewSourcesOmitted),'skipped entries raise the omission flag');
});

test('same-revision refresh reuses the rendered preview so an open disclosure stays open',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',preview([candidate('With sources',{repetition:{count:2,sources:[src(61,'m-61','a-61'),src(62,'m-62','a-62')]}})]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 const before=items(h)[0],detailsBefore=detailsOf(before);
 detailsBefore.open=true;
 detailsBefore.children[0].focus();
 h.respond('rooms',[room()]);
 h.respond('rooms/room',room());
 await h.run('refresh()');await tick();
 const after=items(h)[0],detailsAfter=detailsOf(after);
 assert.equal(after,before,'candidate li node is reused on an unchanged same-revision refresh');
 assert.equal(detailsAfter,detailsBefore,'disclosure node is reused instead of rebuilt');
 assert.equal(detailsAfter.open,true,'open state survives the refresh');
 assert.equal(detailsAfter.children[0].focused,true,'summary focus is not lost');
 h.run("view.language='en';renderRoom(view.room);");
 assert.notEqual(detailsOf(items(h)[0]),detailsBefore,'language change rebuilds the localized DOM');
 assert.equal(detailsOf(items(h)[0]).children[0].textContent,labels.en.memoryPreviewSources+' (2)');
 h.respond('rooms/room/memories/preview',preview([candidate('Reloaded')]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 assert.equal(detailsOf(items(h)[0]),undefined,'a new preview response replaces the previous disclosure DOM');
});

test('skipped malformed source entries are labeled as omitted',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',preview([
  candidate('Partially malformed',{repetition:{count:6,sources:[src(70,'m-70','a-70'),[],src(71,'m-71','a-71'),null,'bad',src(72,'m-72','a-72')]}}),
  candidate('Complete sources',{repetition:{count:2,sources:[src(73,'m-73','a-73'),src(74,'m-74','a-74')]}})
 ]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 const rows=items(h),details=detailsOf(rows[0]),list=details.children[1];
 assert.equal(list.children.length,3,'array and scalar entries are skipped');
 assert.equal(list.textContent.includes('#?'),false,'skipped arrays create no placeholder row');
 assert.ok(list.textContent.includes('m-70')&&list.textContent.includes('m-72'));
 assert.ok(rows[0].textContent.includes(labels.ko.memoryPreviewSourcesOmitted),'skipped entries are labeled as omitted');
 assert.equal(rows[1].textContent.includes(labels.ko.memoryPreviewSourcesOmitted),false,'a fully valid source array keeps the complete label off');
});

test('stored preview state keeps only bounded detached fields',async()=>{
 const h=await opened();
 const candidates=Array.from({length:60},(_,i)=>({original_text:'text-'+i,truncated:false,source:{message_id:'i'.repeat(200),seq:i,actor_id:'a-'+i,actor_kind:'human',padding:'p'.repeat(2000)},repetition:{count:2,distinct_texts:9,sources:[{message_id:'s-'+i,seq:i,actor_id:'a-'+i,actor_kind:'human',extra:'e'.repeat(2000)}]},extra:'x'.repeat(2000)}));
 candidates[0].original_text='<b>'+'t'.repeat(9000);
 candidates[1].original_text=['not','text'];
 candidates[2].original_text={big:'object'};
 candidates[3].original_text=42;
 candidates[4].repetition={count:25,sources_truncated:false,sources:[null,...Array.from({length:25},(_,i)=>src(300+i,'m-'+i,'a-'+i))]};
 Object.defineProperty(candidates,55,{get(){throw new Error('candidate beyond 50 was inspected');}});
 h.respond('rooms/room/memories/preview',preview(candidates,{returned:60,omitted_groups:1,excluded_attachment_messages:2,excluded_agent_messages:3,bulk:'b'.repeat(2000)},{excluded_attachment_messages:2,excluded_agent_messages:3}));
 h.node('memory-preview-load').onclick();await tick();await tick();
 const stored=h.run('memoryPreview');
 assert.ok(stored,'bounded preview state is stored');
 assert.equal(stored.candidates.length,47,'only the first 50 raw entries are inspected and malformed text is dropped');
 assert.equal(stored.candidates[0].original_text.length,8001,'long text is capped in the cache');
 assert.equal(stored.candidates[0].truncated,true,'capped text keeps its truncation flag');
 assert.equal(stored.candidates[0].source.message_id.length,81,'long source ids are capped in the cache');
 assert.equal(stored.candidates[0].source.padding,undefined,'extra source fields are dropped');
 assert.equal(stored.candidates[0].repetition.distinct_texts,undefined,'extra repetition fields are dropped');
 assert.equal(stored.candidates[0].repetition.sources[0].extra,undefined,'extra source-entry fields are dropped');
 assert.equal(stored.candidates[0].extra,undefined,'extra candidate fields are dropped');
 assert.equal(JSON.stringify(stored).includes('xxxxxxxxxx'),false,'large padding fields are not retained');
 assert.equal(stored.candidates[1].repetition.sources.length,19,'only the first 20 source entries are kept');
 assert.equal(stored.candidates[1].repetition.sources_truncated,true,'source cap/omission is flagged');
 const counts=JSON.parse(JSON.stringify(stored.counts));
 assert.deepEqual(Object.keys(counts).sort(),['excluded_agent_messages','excluded_attachment_messages','omitted_groups','returned']);
 assert.deepEqual(counts,{returned:60,omitted_groups:1,excluded_attachment_messages:2,excluded_agent_messages:3});
});

test('counts, truncation and repetition metadata are rendered from the response',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',preview([candidate('Long candidate',{truncated:true,repetition:{count:3,sources:['m-1','m-2','m-3']}})],{returned:1,omitted_groups:2},{excluded_attachment_messages:4,excluded_agent_messages:5}));
 h.node('memory-preview-load').onclick();await tick();await tick();
 const list=h.node('memory-preview-list').textContent;
 assert.ok(list.includes(labels.ko.memoryPreviewTruncated));
 assert.ok(list.includes(labels.ko.memoryPreviewRepetition+' 3'));
 const counts=h.node('memory-preview-counts').textContent;
 assert.ok(counts.includes(labels.ko.memoryPreviewOmitted+': 2'));
 assert.ok(counts.includes(labels.ko.memoryPreviewExcluded+': 4'));
 assert.ok(counts.includes(labels.ko.memoryPreviewExcludedAgent+': 5'));
});

test('client-side truncation is labeled at the 8001-character boundary',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',preview([candidate('x'.repeat(8001))]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 const list=h.node('memory-preview-list').textContent;
 assert.ok(list.includes(labels.ko.memoryPreviewTruncated));
 assert.ok(!list.includes('x'.repeat(8001)));
});

test('a refused preview keeps the room visible and shows no candidate content',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',{code:'forbidden'},403);
 h.node('memory-preview-load').onclick();await tick();await tick();
 assert.equal(h.node('memory-preview-status').textContent,labels.ko.memoryPreviewPermission);
 assert.equal(h.node('memory-preview-list').textContent,'');
 assert.equal(h.node('detail').hidden,false);
 assert.equal(h.node('room-title').textContent,room().topic);
});

test('a malformed response is refused instead of rendering candidates',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',{mode:'stored',candidates:[candidate('should not render')],counts:{}});
 h.node('memory-preview-load').onclick();await tick();await tick();
 assert.equal(h.node('memory-preview-list').textContent,'');
 assert.equal(h.node('memory-preview-status').textContent,labels.ko.memoryPreviewFailed);
});

test('a late preview response for the previous room never renders in the new room',async()=>{
 const h=harness();
 h.respond('rooms/room-a',room('room-a'));
 h.respond('rooms/room-a/interventions',[]);
 const release=h.pending('rooms/room-a/memories/preview',preview([candidate('room-a secret')]));
 h.respond('rooms/room-b',room('room-b'));
 h.respond('rooms/room-b/interventions',[]);
 await h.run("openRoom('room-a',false,false)");
 h.node('memory-preview-load').onclick();
 await h.run("openRoom('room-b',false,false)");
 release();await tick();await tick();
 assert.equal(h.node('memory-preview-list').textContent,'');
 assert.ok(!h.node('memory-preview-counts').textContent.includes('room-a'));
});

test('closing the view clears content but keeps the room open',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',preview([candidate('With sources',{repetition:{count:2,sources:[src(51,'m-51','a-51'),src(52,'m-52','a-52')]}})]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 assert.ok(h.node('memory-preview-list').textContent.includes('With sources'));
 assert.ok(detailsOf(items(h)[0]),'collapsed source DOM exists before close');
 h.node('memory-preview-clear').onclick();
 assert.equal(h.node('memory-preview-list').textContent,'');
 assert.equal(h.node('memory-preview-list').children.length,0,'collapsed source DOM removed');
 assert.equal(h.node('memory-preview-counts').textContent,'');
 assert.equal(h.node('memory-preview-status').textContent,'');
 assert.equal(h.node('memory-preview-clear').hidden,true);
 assert.equal(h.node('detail').hidden,false);
});

test('room change and sign-out clear the rendered preview content',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',preview([candidate('With sources',{repetition:{count:2,sources:[src(51,'m-51','a-51'),src(52,'m-52','a-52')]}})]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 assert.notEqual(h.node('memory-preview-list').textContent,'');
 assert.ok(detailsOf(items(h)[0]),'collapsed source DOM exists before navigation');
 await h.run("openRoom(null)");
 assert.equal(h.node('memory-preview-list').textContent,'');
 assert.equal(h.node('memory-preview-list').children.length,0,'collapsed source DOM removed on room change');
 await h.run("openRoom('room',false,false)");
 h.node('memory-preview-load').onclick();await tick();await tick();
 assert.notEqual(h.node('memory-preview-list').textContent,'');
 h.run('signedOut()');
 assert.equal(h.node('memory-preview-list').textContent,'');
 assert.equal(h.node('memory-preview-list').children.length,0,'collapsed source DOM removed on sign-out');
 assert.equal(h.run('user'),null);
});

test('a membership revision change clears displayed candidates on the next render',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',preview([candidate('With sources',{repetition:{count:2,sources:[src(53,'m-53','a-53'),src(54,'m-54','a-54')]}})]));
 h.node('memory-preview-load').onclick();await tick();await tick();
 assert.notEqual(h.node('memory-preview-list').textContent,'');
 assert.ok(detailsOf(items(h)[0]),'collapsed source DOM exists before revision change');
 h.respond('rooms',[room('room',['room:read','room:control'],2)]);
 h.respond('rooms/room',room('room',['room:read','room:control'],2));
 await h.run('refresh()');await tick();
 assert.equal(h.node('memory-preview-list').textContent,'');
 assert.equal(h.node('memory-preview-list').children.length,0,'collapsed source DOM removed on revision change');
 assert.equal(h.node('memory-preview-counts').textContent,'');
 assert.equal(h.node('memory-preview-status').textContent,'');
 assert.equal(h.node('memory-preview-panel').hidden,false);
 assert.equal(h.run('memoryPreview'),null);
});

test('a pending preview response is discarded when membership revision changes before it returns',async()=>{
 const h=await opened();
 const release=h.pending('rooms/room/memories/preview',preview([candidate('stale rejoin text')]));
 h.node('memory-preview-load').onclick();await tick();
 h.respond('rooms',[room('room',['room:read','room:control'],3)]);
 h.respond('rooms/room',room('room',['room:read','room:control'],3));
 await h.run('refresh()');await tick();
 release();await tick();await tick();
 assert.equal(h.node('memory-preview-list').textContent,'');
 assert.equal(h.run('memoryPreview'),null);
 assert.equal(h.run('memoryPreviewBusy'),false);
});

test('losing room control while a preview request is pending invalidates the ticket',async()=>{
 const h=await opened();
 const release=h.pending('rooms/room/memories/preview',preview([candidate('pending secret text')]));
 h.node('memory-preview-load').onclick();await tick();
 h.respond('rooms',[room('room',['room:read'],1)]);
 h.respond('rooms/room',room('room',['room:read'],1));
 await h.run('refresh()');await tick();
 release();await tick();await tick();
 assert.equal(h.node('memory-preview-panel').hidden,true);
 assert.equal(h.node('memory-preview-list').textContent,'');
 assert.equal(h.run('memoryPreview'),null);
 assert.equal(h.run('memoryPreviewBusy'),false);
});

test('same-membership-revision refresh preserves preview content and never reloads it automatically',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',preview());
 h.node('memory-preview-load').onclick();await tick();await tick();
 const before=h.previewCalls().length;
 h.respond('rooms',[room()]);
 await h.run('refresh()');await tick();
 assert.equal(h.previewCalls().length,before);
 assert.ok(h.node('memory-preview-list').textContent.includes('Candidate memory text'));
});

test('language switches re-render the same preview data in the other locale',async()=>{
 const h=await opened();
 h.respond('rooms/room/memories/preview',preview());
 h.node('memory-preview-load').onclick();await tick();await tick();
 h.run("view.language='en';renderRoom(view.room);");
 const list=h.node('memory-preview-list').textContent;
 assert.ok(list.includes(labels.en.humanBadge));
 assert.ok(list.includes(labels.en.memoryPreviewPreviewOnly));
 assert.ok(h.node('memory-preview-counts').textContent.includes(labels.en.memoryPreviewReturned));
});
