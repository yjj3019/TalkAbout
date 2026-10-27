// Synthetic DOM harness; actual browser coverage is maintained separately.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ViewState,labels} from '../talkabout/web/state.mjs';
const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
const html=readFileSync(new URL('../talkabout/web/index.html',import.meta.url),'utf8');
const room={id:'room',topic:'Synthetic room',state:'preparing',mode:'co_creation',mode_status:'running',effective_permissions:['room:read','room:manage_members','room:control','room:approve'],members:{human:{kind:'human',status:'active'}},tasks:[],messages:[],artifacts:[]};
async function harness(scopes=['room:read','room:manage_members']){
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
 const context=vm.createContext({ViewState,labels,crypto:globalThis.crypto,AbortController,URLSearchParams,EventSource,
  document:{getElementById:node,createElement:tag=>new Node(tag),querySelectorAll:selectors,documentElement:{}},
  window:{addEventListener(){},setInterval:fn=>{timers.push(fn);return timers.length;},clearInterval(){},setTimeout(){},clearTimeout(){}},
  location:{search:'',origin:'https://fixture.invalid'},history:{pushState(){},replaceState(){}},confirm:text=>{confirmations.push(text);return true;},
  fetch:async(url,options={})=>{const path=url.replace('/api/v1/','');calls.push({path,options});if(path==='setup/status')return {ok:true,json:async()=>({setup_required:true})};if(path==='setup/csrf')return {ok:true,json:async()=>({csrf_token:'synthetic'})};if(handlers.has(path))return handlers.get(path)(options);return {ok:false,status:403};}
 });
 vm.runInContext(source,context);await new Promise(setImmediate);
 const run=code=>vm.runInContext(code,context);
 run(`user={id:'human',scopes:${JSON.stringify(scopes)}};`);
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

test('detaching from a room and stream lifecycle send no mutation',async()=>{
 const h=await harness();h.show();await h.run('openRoom("room")');
 h.streams[0].events.access_changed();await new Promise(setImmediate);assert.equal(h.node('detail').hidden,true);
 await h.run('openRoom("room")');h.streams[1].events.stream_end();await new Promise(setImmediate);
 await h.run('openRoom(null)');assert.equal(h.node('detail').hidden,true);
 assert.ok(h.calls.length>2);
 assert.ok(h.calls.every(c=>!c.options||c.options.method==='GET'));
});

for(const status of [401,403,404])test('confirmed room access rejection clears private details: '+status,async()=>{
 const h=await harness();h.show();h.respond('rooms/room',{},status);await h.run("openRoom('room',false,false)");
 assert.equal(h.node('detail').hidden,true);assert.equal(h.node('room-title').textContent,'');assert.equal(h.run('view.room'),null);
});

test('stream access change closes the live source instead of reconnecting from a stale cursor',async()=>{
 const h=await harness();h.show();h.run('watchRoom("room")');
 h.streams[0].events.access_changed();await new Promise(setImmediate);
 assert.equal(h.streams[0].closed,true);assert.equal(h.run('liveSource'),null);assert.equal(h.node('detail').hidden,true);
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

test('room stage renders a spatial scene and the room list renders a plan', async () => {
  const h = await harness();
  const spatial = {...room, mode: 'free_discussion', members: {
    human: {kind: 'human', status: 'active', display_name: 'Owner', permissions: ['room:read']},
    a: {kind: 'agent', status: 'active', display_name: 'Agent A', permissions: ['room:read']}
  }};
  h.run(`view.language='ko';view.select('room');view.room=${JSON.stringify(spatial)};renderRoom(view.room);`);
  const stage = h.node('room-stage').innerHTML;
  assert.match(stage, /<svg/);
  assert.match(stage, /class="actors"/);
  assert.match(stage, /data-set="free_discussion"/);
  assert.ok(h.node('stage-status').textContent.includes(labels.ko.free_discussion));
  h.run(`renderRooms([${JSON.stringify(spatial)}])`);
  const card = h.node('rooms').children[0];
  const plan = card.children[0].innerHTML;
  assert.match(plan, /<svg/);
  assert.equal((plan.match(/<circle/g) || []).length >= 2, true);
});

test('room cards show the deliverable and messages show input-reference evidence',async()=>{
 const h=await harness();
 const withDeliverable={...room,deliverable:'change_proposal',members:{human:{kind:'human'}},messages:[]};
 h.run(`view.language='ko';renderRooms([${JSON.stringify(withDeliverable)}]);`);
 assert.ok(h.node('rooms').textContent.includes(labels.ko.deliverableChange));
 h.run(`renderRooms([${JSON.stringify({...room,members:{human:{kind:'human'}},messages:[]})}]);`);
 assert.ok(!h.node('rooms').textContent.includes(labels.ko.deliverableChange));
 const verified={id:'m1',actor_id:'agent-a',actor_kind:'agent',display_name:'Atlas',kind:'contribution',text:'verified input',seq:1,created_at:0,input_evidence:'worker_reported_server_validated_refs_not_model_read_receipt'};
 const legacy={id:'m2',actor_id:'agent-a',actor_kind:'agent',display_name:'Atlas',kind:'contribution',text:'legacy input',seq:2,created_at:0,input_evidence:'legacy_input_unverified'};
 h.run(`view.language='ko';view.select('room');view.room=${JSON.stringify({...room,state:'active',messages:[verified,legacy]})};renderRoom(view.room);`);
 const text=h.node('messages').textContent;
 assert.ok(text.includes(labels.ko.inputVerified));
 assert.ok(text.includes(labels.ko.inputLegacy));
});
