// Synthetic DOM harness; actual browser coverage is maintained separately.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ViewState,labels} from '../talkabout/web/state.mjs';
const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
const html=readFileSync(new URL('../talkabout/web/index.html',import.meta.url),'utf8');
const room={id:'room',topic:'Synthetic room',state:'preparing',mode:'co_creation',mode_status:'running',effective_permissions:['room:read','room:manage_members','room:control','room:approve'],members:{human:{kind:'human',status:'active'}},tasks:[],messages:[],artifacts:[]};
async function harness(scopes=['room:read','room:manage_members'],sessionStorage=undefined){
 class Node{
  constructor(tag='div'){this.tagName=tag;this.hidden=false;this.children=[];this.dataset={};this.attributes={};this.value='';this.ownText='';this.className='';}
  set textContent(value){this.ownText=value;this.children=[];}get textContent(){return this.ownText+this.children.map(n=>n.textContent??n).join('');}
  append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.ownText='';this.children=nodes;}
  setAttribute(key,value){this.attributes[key]=value;}getAttribute(key){return this.attributes[key];}
  get options(){return this.children;}get selectedOptions(){return this.children.filter(n=>n.value===this.value).slice(0,1);}
  querySelector(selector){return this.children.find(n=>selector==='.'+n.className)||null;}
  close(){this.open=false;}showModal(){this.open=true;}focus(){this.focused=true;}remove(){}
 }
 const nodes=new Map(),calls=[],handlers=new Map(),timers=[],confirmations=[];
 const node=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
 const tabs=['discussion','participants','artifacts','management'].map(name=>{const n=node('tab-control-'+name);n.dataset.tab=name;return n;});
 const actions=['start','pause','resume','close','hold_dispatch','release_dispatch'].map(name=>{const n=new Node('button');n.dataset.action=name;return n;});
 const selectors=selector=>selector==='[data-tab]'?tabs:selector==='[data-action]'?actions:[];
 node('detail').hidden=true;node('agent-registry').hidden=true;
 const streams=[];
 class EventSource{constructor(){this.events={};streams.push(this);}addEventListener(name,fn){this.events[name]=fn;}close(){this.closed=true;}}
 const context=vm.createContext({ViewState,labels,crypto:globalThis.crypto,TextEncoder,sessionStorage,AbortController,URLSearchParams,EventSource,
  document:{getElementById:node,createElement:tag=>new Node(tag),querySelectorAll:selectors,documentElement:{}},
  window:{addEventListener(){},setInterval:fn=>{timers.push(fn);return timers.length;},clearInterval(){},setTimeout(){},clearTimeout(){}},
  location:{search:'',origin:'https://fixture.invalid'},history:{pushState(){},replaceState(){}},confirm:text=>{confirmations.push(text);return true;},
  fetch:async(url,options={})=>{const path=url.replace('/api/v1/','');calls.push({path,options});if(path==='setup/status')return {ok:true,json:async()=>({setup_required:true})};if(path==='setup/csrf')return {ok:true,json:async()=>({csrf_token:'synthetic'})};if(handlers.has(path))return handlers.get(path)(options);return {ok:false,status:403};}
 });
 vm.runInContext(source,context);await new Promise(setImmediate);
 const run=code=>vm.runInContext(code,context);
 run(`messageRetryNamespace='${'a'.repeat(64)}';user={id:'human',scopes:${JSON.stringify(scopes)}};`);
 const respond=(path,value,status=200)=>handlers.set(path,async()=>({ok:status<400,status,json:async()=>value}));
 respond('rooms',[structuredClone(room)]);respond('rooms/room',structuredClone(room));respond('rooms/room/interventions',[]);respond('admin/handovers',[]);respond('agents',[]);
 return {node,run,calls,handlers,respond,tabs,actions,timers,streams,confirmations,show:()=>run(`view.select('room');view.room=${JSON.stringify(room)};renderRoom(view.room);`)};
}

test('room manager without registry scope refreshes the room without optional agent calls',async()=>{
 const h=await harness();h.show();await h.run('refresh()');
 assert.equal(h.calls.some(c=>c.path==='agents'),false);
 assert.equal(h.node('detail').hidden,false);assert.equal(h.node('room-title').textContent,room.topic);
 assert.equal(h.node('nav-agents').hidden,true);assert.equal(h.node('invite-agent').hidden,true);
 assert.equal(h.node('members').textContent.includes(labels.ko.issueCredential),false);
 await h.run("showCredentialMetadata(view.room,{id:'agent'},el('members'))");
 assert.equal(h.calls.some(c=>c.path.endsWith('/credentials')),false);
});

test('dispatch hold is distinct from pause and completion in KO/EN',async()=>{
 const h=await harness(['room:read','room:control','room:approve']);
 for(const language of ['ko','en']){
  h.run(`view.language='${language}';view.select('room');view.room=${JSON.stringify({...room,state:'active',dispatch_held:true,pending_controls:1})};renderRoom(view.room);`);
  assert.equal(h.run('workflowState(view.room).title'),'dispatchHeld');
  assert.ok(h.node('workflow').textContent.includes(labels[language].dispatchHeldHelp));
  assert.ok(h.node('room-state').textContent.includes(labels[language].approvalPending));
  assert.equal(h.actions.find(b=>b.dataset.action==='hold_dispatch').disabled,true);
  assert.equal(h.actions.find(b=>b.dataset.action==='release_dispatch').disabled,false);
  for(const state of ['closed','paused'])assert.equal(h.run(`workflowState({state:'${state}',dispatch_held:true}).title`),state);
 }
});

test('management confirmation identifies the exact action, room and revision',async()=>{
 const h=await harness();h.show();h.respond('rooms/room/controls',{id:'control'});h.respond('rooms/room/controls/control/approve',{});
 for(const language of ['ko','en']){
  h.run(`view.language='${language}';view.select('room');view.room=${JSON.stringify({...room,room_revision:7})};`);
  h.actions.find(b=>b.dataset.action==='hold_dispatch').onclick();
  await new Promise(setImmediate);
  const text=h.confirmations.at(-1);
  assert.ok(text.includes(labels[language].hold_dispatch));
  assert.ok(text.includes(room.topic));assert.ok(text.includes(labels[language].controlRevision+': 7'));
 }
});

test('receipt and stored completion labels never imply provider attestation',async()=>{
 const h=await harness();
 const displayed={...room,state:'active',members:{...room.members,a:{kind:'agent',status:'active',display_name:'Synthetic',delivery_evidence:{delivered_seq:12,read_seq:8}}},
  tasks:[{assignee:'a',state:'completed',purpose:'participant',attempt:2,generation:3}],
  messages:[{actor_kind:'agent',display_name:'Synthetic',kind:'contribution',text:'Synthetic content',execution_evidence:'unverified_agent'}]};
 for(const language of ['ko','en']){
  h.run(`view.language='${language}';view.select('room');view.room=${JSON.stringify(displayed)};renderRoom(view.room);`);
  assert.ok(h.node('members').textContent.includes(labels[language].receiptMeaning));
  assert.ok(h.node('members').textContent.includes(labels[language].readThrough+': 8'));
  assert.ok(h.node('messages').textContent.includes(labels[language].storedMessage));
  assert.ok(h.node('messages').textContent.includes(labels[language].journeyUnverified));
  assert.ok(h.node('tasks').textContent.includes(labels[language].storedCompletion));
  assert.ok(h.node('tasks').textContent.includes(labels[language].attemptLabel+': 2'));
 }
});

test('optional registry 403 does not clear accessible rooms or selected room',async()=>{
 const h=await harness(['room:read','room:manage_members','agent:manage']);h.show();h.respond('agents',{},403);
 await h.run('refresh()');assert.equal(h.node('detail').hidden,false);assert.equal(h.node('room-title').textContent,room.topic);
 assert.ok(h.node('rooms').textContent.includes(room.topic));
});

for(const failure of ['503','network'])test('same-room refresh preserves confirmed content on '+failure,async()=>{
 const h=await harness();h.show();if(failure==='503')h.respond('rooms/room',{},503);else h.handlers.set('rooms/room',async()=>{throw new TypeError('synthetic network failure');});
 await h.run("openRoom('room',false,false)");assert.equal(h.node('detail').hidden,false);assert.equal(h.node('room-title').textContent,room.topic);
 assert.equal(h.run('view.room.topic'),room.topic);assert.equal(h.node('notice').textContent,labels.ko.temporaryFailure);
});

test('new-room failure leaves a visible room browser and retry works',async()=>{
 const h=await harness();h.node('agent-registry').hidden=false;h.node('room-browser').hidden=true;h.respond('rooms/room',{},503);
 await h.run("openRoom('room')");assert.equal(h.node('room-browser').hidden,false);assert.equal(h.node('detail').hidden,true);
 h.respond('rooms/room',structuredClone(room));await h.run('refresh()');assert.equal(h.node('detail').hidden,false);
});

test('ten-second polling and SSE network errors preserve the confirmed room',async()=>{
 const h=await harness();h.show();h.respond('rooms/room',{},503);h.run('startCommunityWatch();watchRoom("room");');
 await h.timers.at(-1)();await new Promise(setImmediate);assert.equal(h.node('detail').hidden,false);
 h.streams[0].onerror();await new Promise(setImmediate);assert.equal(h.node('room-title').textContent,room.topic);
});

for(const status of [401,403,404])test('confirmed room access rejection clears private details: '+status,async()=>{
 const h=await harness();h.show();h.respond('rooms/room',{},status);await h.run("openRoom('room',false,false)");
 assert.equal(h.node('detail').hidden,true);assert.equal(h.node('room-title').textContent,'');assert.equal(h.run('view.room'),null);
});

test('paused and adoption workflows point to the correct action in both languages',async()=>{
 const h=await harness();for(const lang of ['ko','en']){h.run(`view.language='${lang}';`);
  const paused=h.run("workflowState({state:'paused',mode_status:'awaiting_adoption'})");assert.equal(paused.title,'paused');assert.equal(paused.tab,'management');
  const adoption=h.run("workflowState({state:'active',mode_status:'awaiting_adoption'})");assert.equal(adoption.title,'awaiting_adoption');assert.equal(adoption.tab,'artifacts');
  assert.ok(labels[lang][paused.help]);assert.ok(labels[lang][adoption.help]);}
});

test('language changes cached room cards immediately without a fetch',async()=>{
 const h=await harness();h.run(`renderRooms([${JSON.stringify(room)}]);`);const count=h.calls.length;
 assert.ok(h.node('rooms').textContent.includes(labels.ko.preparing));h.run("view.languageTo('en');language();");
 assert.ok(h.node('rooms').textContent.includes(labels.en.preparing));assert.equal(h.calls.length,count);
});

test('metadata distinguishes credential IDs and scopes without rendering a raw secret',async()=>{
 const h=await harness(['credential:manage']);h.show();h.respond('agents/a/credentials',[
  {credential_id:'credential-one',scopes:['room:read'],room_id:'room',expires_at:2000000000,revoked:false,token:'must-not-render'},
  {credential_id:'credential-two',scopes:['room:read','room:speak'],room_id:'room',expires_at:2000000000,revoked:false}]);
 await h.run("showCredentialMetadata(view.room,{id:'a'},el('members'))");const text=h.node('members').textContent;
 assert.ok(text.includes('credential-one')&&text.includes('credential-two')&&text.includes('room:speak'));assert.equal(text.includes('must-not-render'),false);
});

test('tabs have connected semantics and support arrow/Home/End focus',async()=>{
 const h=await harness();h.run("roomTab('discussion')");let prevented=false;
 h.tabs[0].onkeydown({key:'ArrowRight',preventDefault(){prevented=true;}});
 assert.equal(prevented,true);assert.equal(h.tabs[1].attributes['aria-selected'],'true');assert.equal(h.tabs[1].tabIndex,0);assert.equal(h.tabs[1].focused,true);
 h.tabs[1].onkeydown({key:'End',preventDefault(){}});assert.equal(h.tabs[3].attributes['aria-selected'],'true');
 h.tabs[3].onkeydown({key:'Home',preventDefault(){}});assert.equal(h.tabs[0].attributes['aria-selected'],'true');
 for(const name of ['discussion','participants','artifacts','management']){
  assert.match(html,new RegExp(`id="tab-control-${name}" role="tab" aria-controls="tab-${name}"`));
  assert.match(html,new RegExp(`role="tabpanel" aria-labelledby="tab-control-${name}" id="tab-${name}"`));}
 for(const name of ['onboarding','credential'])assert.match(html,new RegExp(`id="${name}-dialog" aria-labelledby="${name}-title" aria-describedby="${name}-help"`));
});

test('message drafts stay in their room and are discarded at logout',async()=>{
 const h=await harness();h.show();h.node('message-kind').value='question';h.node('message-text').value='Private draft A';
 h.respond('rooms/other',{...room,id:'other'});h.respond('rooms/other/interventions',[]);
 await h.run("openRoom('other',false,false)");assert.equal(h.node('message-text').value,'');
 h.node('message-text').value='Draft B';await h.run("openRoom('room',false,false)");
 assert.equal(h.node('message-text').value,'Private draft A');assert.equal(h.node('message-kind').value,'question');
 h.run('signedOut()');assert.equal(h.node('message-text').value,'');
 h.run("user={id:'replacement',scopes:[]};");await h.run("openRoom('room',false,false)");
 assert.equal(h.node('message-text').value,'');
});

test('retry after an unconfirmed message retains its client and idempotency identity',async()=>{
 const h=await harness();h.show();h.node('message-kind').value='question';h.node('message-text').value='One logical message';
 let attempts=0;h.handlers.set('rooms/room/messages',async()=>{if(++attempts===1)throw new TypeError('synthetic response lost');return {ok:true,json:async()=>({id:'saved'})};});
 h.node('message').onsubmit({preventDefault(){}});await new Promise(setImmediate);
 h.node('message').onsubmit({preventDefault(){}});await new Promise(setImmediate);
 const sent=h.calls.filter(c=>c.path==='rooms/room/messages');assert.equal(sent.length,2);
 assert.equal(JSON.parse(sent[0].options.body).client_message_id,JSON.parse(sent[1].options.body).client_message_id);
 assert.equal(sent[0].options.headers['Idempotency-Key'],sent[1].options.headers['Idempotency-Key']);
 assert.equal(h.node('message-text').value,'');
 h.node('message-text').value='One logical message';h.node('message').onsubmit({preventDefault(){}});await new Promise(setImmediate);
 const fresh=h.calls.filter(c=>c.path==='rooms/room/messages').at(-1);
 assert.notEqual(JSON.parse(fresh.options.body).client_message_id,JSON.parse(sent[0].options.body).client_message_id);
});

for(const leave of [false,true])test('late message success preserves newer draft; leave='+leave,async()=>{
 const h=await harness();h.show();h.node('message-text').value='Submitted A';let finish;
 h.handlers.set('rooms/room/messages',()=>new Promise(resolve=>{finish=resolve;}));
 h.node('message').onsubmit({preventDefault(){}});await new Promise(setImmediate);
 if(leave){h.respond('rooms/other',{...room,id:'other'});h.respond('rooms/other/interventions',[]);await h.run("openRoom('other',false,false)");}
 h.node('message-text').value='New draft';finish({ok:true,json:async()=>({id:'saved'})});await new Promise(setImmediate);
 assert.equal(h.node('message-text').value,'New draft');
});

for(const edited of [false,true])test('confirmation after leaving and returning clears only the original draft; edited='+edited,async()=>{
 const h=await harness();h.show();h.node('message-kind').value='question';h.node('message-text').value='Submitted A';let finish;
 h.handlers.set('rooms/room/messages',()=>new Promise(resolve=>{finish=resolve;}));h.node('message').onsubmit({preventDefault(){}});await new Promise(setImmediate);
 h.respond('rooms/other',{...room,id:'other'});h.respond('rooms/other/interventions',[]);
 await h.run("openRoom('other',false,false)");await h.run("openRoom('room',false,false)");
 if(edited)h.node('message-text').oninput();
 finish({ok:true,json:async()=>({id:'saved'})});await new Promise(setImmediate);
 assert.equal(h.node('message-text').value,edited?'Submitted A':'');
});

test('confirmed submission removes only its submitted attachment references',async()=>{
 const h=await harness();h.show();h.node('message-text').value='With attachment';
 h.run("textAttachmentSettings={enabled:true};pendingTextFiles.set('room',[{ref:{file_id:'old',version_id:'v1'}}]);");let finish;
 h.handlers.set('rooms/room/messages',()=>new Promise(resolve=>{finish=resolve;}));h.node('message').onsubmit({preventDefault(){}});await new Promise(setImmediate);
 h.run("pendingTextFiles.get('room').push({ref:{file_id:'new',version_id:'v2'}});");
 finish({ok:true,json:async()=>({id:'saved'})});await new Promise(setImmediate);
 assert.equal(h.run("pendingTextFiles.get('room').length"),1);assert.equal(h.run("pendingTextFiles.get('room')[0].ref.file_id"),'new');
});

for(const actor of ['human','replacement'])test('late success after a new login cannot clear its draft or attachments: '+actor,async()=>{
 const h=await harness();h.show();h.node('message-text').value='Previous session';let finish;
 h.handlers.set('rooms/room/messages',()=>new Promise(resolve=>{finish=resolve;}));h.node('message').onsubmit({preventDefault(){}});await new Promise(setImmediate);
 h.run(`signedOut();user={id:'${actor}',scopes:[]};`);h.show();h.node('message-text').value='New session draft';
 h.run("pendingTextFiles.set('room',[{ref:{file_id:'new-session',version_id:'v2'}}]);");
 finish({ok:true,json:async()=>({id:'saved'})});await new Promise(setImmediate);
 assert.equal(h.node('message-text').value,'New session draft');assert.equal(h.run("pendingTextFiles.get('room')[0].ref.file_id"),'new-session');
});

test('an unconfirmed message keeps its retry identity across room navigation',async()=>{
 const h=await harness();h.show();h.node('message-kind').value='question';h.node('message-text').value='Unconfirmed draft';
 h.handlers.set('rooms/room/messages',async()=>{throw new TypeError('synthetic response lost');});
 h.node('message').onsubmit({preventDefault(){}});await new Promise(setImmediate);
 h.respond('rooms/other',{...room,id:'other'});h.respond('rooms/other/interventions',[]);
 await h.run("openRoom('other',false,false)");await h.run("openRoom('room',false,false)");
 h.node('message').onsubmit({preventDefault(){}});await new Promise(setImmediate);
 const sent=h.calls.filter(c=>c.path==='rooms/room/messages');assert.equal(sent.length,2);
 assert.equal(JSON.parse(sent[0].options.body).client_message_id,JSON.parse(sent[1].options.body).client_message_id);
 assert.equal(sent[0].options.headers['Idempotency-Key'],sent[1].options.headers['Idempotency-Key']);
});

for(const lang of ['ko','en'])test('empty creation is a failure rather than successful completion in '+lang,async()=>{
 const h=await harness();h.run(`view.language='${lang}';`);
 const flow=h.run("workflowState({state:'closed',mode_status:'no_artifact'})");
 assert.equal(flow.title,'creationFailed');assert.equal(flow.tab,'artifacts');
 assert.equal(typeof labels[lang][flow.title],'string');assert.equal(typeof labels[lang][flow.help],'string');
});

for(const lang of ['ko','en'])test('a blocked room exposes its failure rather than active or held progress in '+lang,async()=>{
 const h=await harness();h.run(`view.language='${lang}';`);
 const flow=h.run("workflowState({state:'active',dispatch_held:true,scheduler_error:'event_capacity'})");
 assert.equal(flow.title,'dispatchBlocked');assert.equal(flow.tab,'management');
 assert.equal(typeof labels[lang][flow.title],'string');assert.equal(typeof labels[lang][flow.help],'string');
});


test('reload retries reuse only metadata without persisting private message text',async()=>{
 const values=new Map();const storage={get length(){return values.size;},key:i=>[...values.keys()][i],getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
 const first=await harness(undefined,storage);first.show();first.node('message-text').value='Private cobalt phrase';first.node('message-kind').value='question';
 first.handlers.set('rooms/room/messages',async()=>{throw new TypeError('synthetic response loss');});
 await first.node('message').onsubmit({preventDefault(){}});
 const request=first.calls.find(c=>c.path==='rooms/room/messages');assert.ok(request);
 assert.equal(values.size,1);assert.equal(JSON.stringify([...values]).includes('Private cobalt phrase'),false);
 const reloaded=await harness(undefined,storage);reloaded.show();reloaded.node('message-text').value='Private cobalt phrase';reloaded.node('message-kind').value='question';
 reloaded.respond('rooms/room/messages',{id:'confirmed'});
 await reloaded.node('message').onsubmit({preventDefault(){}});
 const retried=reloaded.calls.find(c=>c.path==='rooms/room/messages');assert.ok(retried);
 assert.equal(request.options.headers['Idempotency-Key'],retried.options.headers['Idempotency-Key']);
 assert.equal(JSON.parse(request.options.body).client_message_id,JSON.parse(retried.options.body).client_message_id);
 assert.equal(values.size,0);
 reloaded.node('message-text').value='Private cobalt phrase';await reloaded.node('message').onsubmit({preventDefault(){}});
 assert.notEqual(reloaded.calls.filter(c=>c.path==='rooms/room/messages').at(-1).options.headers['Idempotency-Key'],request.options.headers['Idempotency-Key']);
});

test('duplicate click during asynchronous retry hashing posts one logical message',async()=>{
 const values=new Map();const storage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
 const h=await harness(undefined,storage);h.show();h.node('message-text').value='One submitted intention';
 h.respond('rooms/room/messages',{id:'once'});
 await Promise.all([h.node('message').onsubmit({preventDefault(){}}),h.node('message').onsubmit({preventDefault(){}})]);
 assert.equal(h.calls.filter(c=>c.path==='rooms/room/messages').length,1);
});

for(const logout of [false,true])test('failed retry-marker removal cannot resurrect an old intention: logout='+logout,async()=>{
 const values=new Map();const storage={get length(){return values.size;},key:i=>[...values.keys()][i],getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem(){throw new Error('synthetic removal failure');}};
 const first=await harness(undefined,storage);first.show();first.node('message-text').value='Identical but new intention';
 first.handlers.set('rooms/room/messages',async()=>{if(logout)throw new TypeError('synthetic lost reply');return {ok:true,json:async()=>({id:'confirmed'})};});
 await first.node('message').onsubmit({preventDefault(){}});
 const original=first.calls.find(c=>c.path==='rooms/room/messages');assert.ok(original);
 if(logout)first.run('signedOut()');
 const next=await harness(undefined,storage);next.show();next.node('message-text').value='Identical but new intention';next.respond('rooms/room/messages',{id:'new'});
 await next.node('message').onsubmit({preventDefault(){}});
 const sent=next.calls.find(c=>c.path==='rooms/room/messages');assert.ok(sent);
 assert.notEqual(sent.options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);
 assert.notEqual(JSON.parse(sent.options.body).client_message_id,JSON.parse(original.options.body).client_message_id);
 assert.equal(JSON.stringify([...values]).includes('Identical but new intention'),false);
});

test('new login cannot recover a previous sessions intention after storage cleanup fully fails',async()=>{
 const values=new Map();let broken=false;const storage={get length(){return values.size;},key:i=>[...values.keys()][i],getItem:k=>values.get(k)??null,setItem(k,v){if(broken)throw new Error('synthetic write failure');values.set(k,v);},removeItem(){throw new Error('synthetic removal failure');}};
 const first=await harness(undefined,storage);first.show();first.node('message-text').value='Same text after another login';
 first.handlers.set('rooms/room/messages',async()=>{throw new TypeError('synthetic lost response');});await first.node('message').onsubmit({preventDefault(){}});
 const original=first.calls.find(c=>c.path==='rooms/room/messages');assert.ok(original);
 broken=true;first.run('signedOut()');broken=false;
 const next=await harness(undefined,storage);next.run("messageRetryNamespace='"+'b'.repeat(64)+"';");next.show();next.node('message-text').value='Same text after another login';next.respond('rooms/room/messages',{id:'new'});
 await next.node('message').onsubmit({preventDefault(){}});
 const sent=next.calls.find(c=>c.path==='rooms/room/messages');assert.ok(sent);
 assert.notEqual(sent.options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);
});

for(const namespace of [null,'','not-a-session'])test('missing or invalid retry namespace fails before POST: '+namespace,async()=>{
 const h=await harness();h.show();h.run('messageRetryNamespace='+JSON.stringify(namespace));h.node('message-text').value='Retain this draft';
 await h.node('message').onsubmit({preventDefault(){}});
 assert.equal(h.calls.some(c=>c.path==='rooms/room/messages'),false);
 assert.equal(h.node('message-text').value,'Retain this draft');
 assert.equal(h.node('notice').textContent,labels.ko.messageRetryStorageRequired);
});

test('namespace replacement during hashing cannot submit the previous login intention',async()=>{
 const storage={getItem:()=>null,setItem(){},removeItem(){}};
 const h=await harness(undefined,storage);h.show();h.node('message-text').value='Previous login';
 h.run('let releaseHash;const saved=crypto;crypto={randomUUID:()=>saved.randomUUID(),subtle:{digest:()=>new Promise(done=>{releaseHash=done;})}};');
 const pending=h.node('message').onsubmit({preventDefault(){}});
 h.run("messageRetryNamespace='"+'b'.repeat(64)+"';releaseHash(new Uint8Array(32).buffer);");await pending;
 assert.equal(h.calls.some(c=>c.path==='rooms/room/messages'),false);
});

test('acknowledged message retains its intention if both metadata cleanup operations are unavailable',async()=>{
 const values=new Map();let broken=false;const storage={getItem:k=>values.get(k)??null,setItem(k,v){if(broken)throw new Error('synthetic write failure');values.set(k,v);},removeItem(){throw new Error('synthetic removal failure');}};
 const h=await harness(undefined,storage);h.show();h.node('message-text').value='Stored but cleanup blocked';
 h.handlers.set('rooms/room/messages',async()=>{broken=true;return {ok:true,json:async()=>({id:'stored'})};});
 await h.node('message').onsubmit({preventDefault(){}});
 assert.equal(h.node('message-text').value,'Stored but cleanup blocked');
 assert.equal(h.node('notice').textContent,labels.ko.messageConfirmedStorageBlocked);
 broken=false;h.respond('rooms/room/messages',{id:'stored'});await h.node('message').onsubmit({preventDefault(){}});
 const sent=h.calls.filter(c=>c.path==='rooms/room/messages');assert.equal(sent.length,2);
 assert.equal(sent[0].options.headers['Idempotency-Key'],sent[1].options.headers['Idempotency-Key']);
 assert.equal(h.node('message-text').value,'');
});

for(const broken of ['denied','corrupt'])test('unsafe retry metadata stops before sending: '+broken,async()=>{
 const values=new Map();const storage={getItem:k=>broken==='corrupt'?'{}':null,setItem(){throw new Error('storage unavailable');}};
 const h=await harness(undefined,storage);h.show();h.node('message-text').value='Retained only in composer';
 await h.node('message').onsubmit({preventDefault(){}});
 assert.equal(h.calls.filter(c=>c.path==='rooms/room/messages').length,0);
 assert.equal(h.node('message-text').value,'Retained only in composer');
 assert.equal(h.node('notice').textContent,labels.ko.messageRetryStorageRequired);
});

test('logout during retry hashing cannot restore old metadata or submit under a new login',async()=>{
 const values=new Map();const storage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
 const h=await harness(undefined,storage);h.show();h.node('message-text').value='Previous account';
 h.run('let releaseDigest;const originalCrypto=crypto;crypto={randomUUID:()=>originalCrypto.randomUUID(),subtle:{digest:()=>new Promise(done=>{releaseDigest=done;})}};');
 const pending=h.node('message').onsubmit({preventDefault(){}});
 h.run("signedOut();user={id:'human',scopes:[]};releaseDigest(new Uint8Array(32).buffer);");
 await pending;assert.equal(values.size,0);
 assert.equal(h.calls.filter(c=>c.path==='rooms/room/messages').length,0);
});

test('a different draft cannot overtake a submission while its hash is pending',async()=>{
 const storage={getItem:()=>null,setItem(){},removeItem(){}};
 const h=await harness(undefined,storage);h.show();h.node('message-text').value='First intention';
 h.run('let releaseFirst;const savedCrypto=crypto;crypto={randomUUID:()=>savedCrypto.randomUUID(),subtle:{digest:()=>new Promise(done=>{releaseFirst=done;})}};');
 h.respond('rooms/room/messages',{id:'first'});
 const first=h.node('message').onsubmit({preventDefault(){}});
 h.node('message-text').value='New draft';h.node('message-text').oninput();
 await h.node('message').onsubmit({preventDefault(){}});
 assert.equal(h.node('notice').textContent,labels.ko.messageSendInProgress);
 h.run('releaseFirst(new Uint8Array(32).buffer);');await first;
 const requests=h.calls.filter(c=>c.path==='rooms/room/messages');assert.equal(requests.length,1);
 assert.equal(JSON.parse(requests[0].options.body).text,'First intention');
 assert.equal(h.node('message-text').value,'New draft');
});
