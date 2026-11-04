// Synthetic DOM/fetch integration for the real work-plan module and app.mjs.
// No browser, provider, operating account, database or model call.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ViewState,labels} from '../talkabout/web/state.mjs';
import {createWorkPlanView,WORK_PLAN_LABELS} from '../talkabout/web/work-plan.mjs';
import {createCollaborationBoard} from '../talkabout/web/collaboration-board.mjs';

const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
const html=readFileSync(new URL('../talkabout/web/index.html',import.meta.url),'utf8');
const tick=()=>new Promise(setImmediate);
const ok=data=>({ok:true,status:200,json:async()=>data});
const failure=(status,code='synthetic')=>({ok:false,status,json:async()=>({code})});
const deferred=()=>{let resolve;const promise=new Promise(settle=>{resolve=settle;});return {promise,resolve};};
const appDispatchRun=(extra={})=>({id:'run-1',state:'running',plan_revision:2,generation:1,started_at:1700000000,finished_at:null,abort_reason:null,nodes:[{id:'node1',assignee:'worker',write_scope:'analysis',state:'queued',task_id:'task-1',failure:null}],write_scope_enforced:false,automatic_provider_call:false,plan_text_visible_to_assignees:true,evidence:'worker_reported_unverified',...extra});
const dispatchCalls=(h,method)=>h.calls.filter(call=>(!method||call.method===method)&&call.path.endsWith('/work-plan/dispatch'));

class Node{
 constructor(tag='div',id=''){this.tagName=tag;this.id=id;this.value='';this.children=[];this.ownText='';this.type='text';this.hidden=false;this.open=false;this.disabled=false;this.checked=false;this.readOnly=false;this.rows=0;this.title='';this.className='';this.dataset={};this.attributes={};this.listeners={};this.onclick=null;this.oninput=null;this.onchange=null;}
 set textContent(v){this.ownText=v==null?'':String(v);this.children=[];}get textContent(){return this.ownText+this.children.map(child=>child&&typeof child==='object'&&'textContent' in child?child.textContent:String(child)).join('');}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.ownText='';this.children=items;}
 get options(){return this.children;}get selectedOptions(){return this.children.filter(child=>child.selected===true);}
 close(){this.open=false;}showModal(){this.open=true;}addEventListener(name,fn){this.listeners[name]=fn;}
 setAttribute(name,value){this.attributes[name]=value;}getAttribute(name){return this.attributes[name];}removeAttribute(name){delete this.attributes[name];}
 scrollIntoView(){}querySelector(){return new Node();}querySelectorAll(){return [];}click(){this.onclick?.();}focus(){this.focused=true;}remove(){this.removed=true;}replaceWith(){this.replaced=true;}
}

function makeRoom(id='room',{revision=1,permissions=['room:read','room:control']}={}){
 return {id,topic:'Synthetic plan room '+id,state:'active',mode:'bounded_rounds',mode_status:'running',generation:1,room_revision:1,limits:{rounds:1},membership_revision:revision,dispatch_held:false,mode_progress:{},effective_permissions:permissions,messages:[],tasks:[],artifacts:[],
  members:{human:{kind:'human',status:'active',revision:1,epoch:0,permissions:['room:read','room:control']},worker:{kind:'agent',status:'active',revision:1,epoch:0,permissions:['room:read']}}};
}
const planNode=(title='Draft one')=>({id:'node1',title,instruction:'Inspect only; never publish',assignee:'worker',write_scope:'analysis',depends_on:[]});

async function harness({factory=true}={}){
 const nodes=new Map(),calls=[],handlers=new Map(),timers=[],streams=[],confirmations=[],copied=[];
 const node=id=>{if(!nodes.has(id))nodes.set(id,new Node('div',id));return nodes.get(id);};
 class EventSource{constructor(url){this.url=url;this.listeners={};this.closed=false;streams.push(this);}addEventListener(name,fn){this.listeners[name]=fn;}close(){this.closed=true;}emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}}
 const context=vm.createContext({ViewState,labels,createWorkPlanView,WORK_PLAN_LABELS,createCollaborationBoard,crypto:globalThis.crypto,AbortController,URLSearchParams,Blob,Date,
  document:{getElementById:node,createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag),querySelectorAll:()=>[],addEventListener(){},documentElement:{},hidden:false},
  window:{setInterval:()=>1,clearInterval(){},setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},addEventListener(){},matchMedia:undefined},
  location:{origin:'http://127.0.0.1:19999',search:''},history:{replaceState(){},pushState(){}},confirm:message=>{confirmations.push(message);return true;},
  navigator:{clipboard:{writeText:async text=>{copied.push(text);}}},EventSource,
  fetch:async(url,options={})=>{const path=url.replace('/api/v1/',''),method=options.method||'GET';calls.push({path,method,options,body:options.body?JSON.parse(options.body):null});const handler=handlers.get(method+' '+path);if(handler)return handler(options);if(path==='capabilities')return ok({text_attachments:{enabled:false},agent_invitations:{enabled:false}});if(path==='setup/status')return ok({setup_required:false});return ok([]);}});
 const run=code=>vm.runInContext(code,context);
 if(factory===false){context.createWorkPlanView=undefined;context.WORK_PLAN_LABELS=undefined;}
 run(source);await tick();
 run("user={id:'human',kind:'human',display_name:'Human',handle:'human',scopes:['room:read','room:control']};agents=[];view.language='ko';");
 const respond=(method,path,data,status=200,code='synthetic')=>handlers.set(method+' '+path,async()=>status<400?ok(data):failure(status,code));
 const respondGet=(path,data,status=200,code='synthetic')=>respond('GET',path,data,status,code);
 const respondPost=(path,data,status=200,code='synthetic')=>respond('POST',path,data,status,code);
 const deferredGet=path=>{const gate=deferred();handlers.set('GET '+path,()=>gate.promise.then(()=>ok({})));return gate;};
 const flushTimers=async()=>{while(timers.length){const fn=timers.shift();await fn();}await tick();await tick();};
 const planRoot=()=>node('work-plan-container').children.find(child=>child&&child.id==='work-plan-module');
 const find=id=>{const root=planRoot();if(!root)return undefined;const queue=[root];while(queue.length){const current=queue.shift();if(!current||typeof current!=='object')continue;if(current.id===id)return current;if(Array.isArray(current.children))queue.push(...current.children);}return undefined;};
 const planText=()=>node('work-plan-container').textContent;
 const workPlanCalls=method=>calls.filter(call=>(!method||call.method===method)&&call.path.endsWith('/work-plan'));
 const stream=()=>streams.filter(source=>!source.closed).at(-1);
 return {node,run,calls,handlers,respond,respondGet,respondPost,deferredGet,flushTimers,planRoot,find,planText,workPlanCalls,stream,streams,confirmations,copied};
}

async function opened(h=null,roomData=makeRoom()){
 const app=h||await harness();
 app.respondGet('rooms',[roomData]);
 app.respondGet('rooms/'+roomData.id,roomData);
 app.respondGet('rooms/'+roomData.id+'/interventions',[]);
 await app.run(`openRoom(${JSON.stringify(roomData.id)},false,false)`);
 await tick();await tick();
 return app;
}

async function loaded(app,{title='Draft one'}={}){
 app.respondGet('rooms/room/work-plan',{revision:2,nodes:[planNode(title)]});
 // use the module's explicit load control, never an automatic fetch
 const load=app.find('wp-load');
 assert.ok(load,'explicit load button exists');
 await load.onclick();
 await tick();await tick();
 return app;
}

function startEdit(app,value){
 const edit=app.find('wp-edit');assert.ok(edit);edit.onclick();
 const title=app.find('wp-title-0');assert.ok(title);
 title.value=value;title.oninput();
 return {title};
}

function ackDispatch(app){
 for(const name of ['queue','disclosure','unenforced']){
  const input=app.find('wp-dispatch-ack-'+name);assert.ok(input,'missing dispatch ack '+name);
  input.checked=true;input.onchange();
 }
}

test('the real module is mounted in the management container with no automatic plan fetch',async()=>{
 const h=await opened();
 assert.equal(h.run('agentInviteEnabled'),false,'the private plan is independent of the invitation flag');
 assert.ok(h.run('workPlanView!==null'),'app created the real module instance');
 assert.equal(h.planRoot()?.id,'work-plan-module');
 assert.ok(h.planRoot().children.some(child=>child.id==='wp-load'),'unloaded panel has the manual load action');
 assert.equal(h.workPlanCalls().length,0,'opening a room never loads the private plan');
 assert.equal(h.find('wp-load').type,'button');
 const status=h.planRoot().children.find(child=>child.className==='wp-status');
 assert.equal(status.getAttribute('role'),'status');
 assert.equal(status.getAttribute('aria-live'),'polite');
 assert.ok(html.includes('data-work-plan="required"'),'production container is marked as required');
});

test('manual load uses the exact plan endpoint and no mutation is automatic',async()=>{
 const h=await opened();
 await loaded(h);
 const calls=h.workPlanCalls();
 assert.equal(calls.length,1);
 assert.equal(calls[0].method,'GET');
 assert.equal(calls[0].path,'rooms/room/work-plan');
 assert.equal(h.planText().includes('Draft one'),true);
 assert.equal(h.planText().includes('Inspect only; never publish'),true);
 assert.equal(h.calls.some(call=>call.path.endsWith('/messages')||call.path.endsWith('/tasks')),false,'loading a draft never publishes or creates tasks');
});

test('same-room SSE refresh keeps the stable plan ticket, auth ticket and dirty draft',async()=>{
 const h=await opened();
 await loaded(h);
 startEdit(h,'Changed but private');
 h.run("watchRoom('room');");
 const before={auth:h.run('authEpoch'),ticket:h.run('workPlanTicket'),epoch:h.run('view.epoch'),gets:h.workPlanCalls().length};
 const source=h.stream();assert.ok(source,'room stream is open');
 source.emit('room_update',{events:[{type:'message',id:'m-2'}]});
 await h.flushTimers();
 assert.equal(h.run('authEpoch'),before.auth,'same-session SSE must not change authEpoch');
 assert.equal(h.run('workPlanTicket'),before.ticket,'same-room SSE must not change the stable plan ticket');
 assert.ok(h.run('view.epoch')>before.epoch,'app view.epoch does rise on SSE refresh');
 assert.equal(h.find('wp-title-0')?.value,'Changed but private','dirty draft survives the SSE refresh');
 assert.equal(h.workPlanCalls().length,before.gets,'SSE refresh does not automatically reload the plan');
});

test('real room switch clears the plan ticket and an old dirty draft',async()=>{
 const h=await opened();
 await loaded(h);startEdit(h,'Room A secret');
 const before=h.run('workPlanTicket');
 const b=makeRoom('room-b');
 h.respondGet('rooms/room-b',b);h.respondGet('rooms/room-b/interventions',[]);h.respondGet('rooms',[b]);
 await h.run("openRoom('room-b',false,false)");
 await tick();await tick();
 assert.ok(h.run('workPlanTicket')>before,'real room switch advances the stable plan ticket');
 assert.equal(h.planText().includes('Room A secret'),false,'old private draft is removed');
 assert.ok(h.find('wp-load'),'new room starts in the explicit unloaded state');
 assert.equal(h.workPlanCalls().length,1,'switching rooms never auto-loads the new plan');
});

test('a late load response from a previous room cannot cross into the current room',async()=>{
 const h=await opened();
 const gate=deferred();h.handlers.set('GET rooms/room/work-plan',()=>gate.promise.then(()=>ok({revision:2,nodes:[planNode('Old room draft')]})));
 const load=h.find('wp-load');const pending=load.onclick();await tick();
 const b=makeRoom('room-b');h.respondGet('rooms/room-b',b);h.respondGet('rooms/room-b/interventions',[]);h.respondGet('rooms',[b]);
 await h.run("openRoom('room-b',false,false)");await tick();
 gate.resolve();await pending;await tick();await tick();
 assert.equal(h.planText().includes('Old room draft'),false,'late previous-room response is discarded');
 assert.ok(h.find('wp-load'),'current room remains unloaded');
});

for(const [status,key] of [[403,'workPlanForbidden'],[404,'workPlanUnavailable']])test('a '+status+' reload removes the loaded draft immediately',async()=>{
 const h=await opened();
 await loaded(h);
 h.respondGet('rooms/room/work-plan',{},status);
 const reload=h.find('wp-reload');assert.ok(reload);await reload.onclick();await tick();await tick();
 assert.equal(h.planText().includes('Draft one'),false,'failed reload leaves no stale draft text');
 assert.ok(h.planText().includes(WORK_PLAN_LABELS.ko[key]),'status explains the failed access');
 assert.ok(h.find('wp-retry'),'explicit retry remains available without an automatic request');
 assert.equal(h.workPlanCalls().length,2,'one explicit reload only; no automatic retry loop');
});

test('permission loss immediately hides and purges the private plan',async()=>{
 const h=await opened();
 await loaded(h);startEdit(h,'Permission secret');
 h.run("view.room.effective_permissions=['room:read'];renderRoom(view.room);");
 assert.equal(h.planText().includes('Permission secret'),false,'draft text is removed immediately');
 assert.equal(h.planText(),'','panel is hidden/empty without room:control');
 assert.equal(h.workPlanCalls().length,1,'a permission change does not auto-fetch');
});

test('membership revision and epoch changes immediately purge the rendered draft',async()=>{
 for(const change of ['revision','epoch']){
  const h=await opened();
  await loaded(h);startEdit(h,'Membership secret');
  if(change==='revision')h.run('view.room.membership_revision++;');
  else h.run('view.room.members.human.epoch++;');
  h.run('renderRoom(view.room);');
  assert.equal(h.planText().includes('Membership secret'),false,change+' change removes the draft');
  assert.ok(h.planText().includes(WORK_PLAN_LABELS.ko.workPlanStale),change+' change leaves a stale explanation');
  assert.equal(h.workPlanCalls().length,1,change+' change does not auto-refetch');
 }
});

test('logout clears the mounted plan and advances the stable ticket',async()=>{
 const h=await opened();
 await loaded(h);startEdit(h,'Logout secret');
 const before=h.run('workPlanTicket');
 h.run('signedOut();');
 assert.ok(h.run('workPlanTicket')>before);
 assert.equal(h.run('user'),null);
 assert.equal(h.planText().includes('Logout secret'),false);
 assert.equal(h.planRoot(),undefined,'module was cleared from the container');
});

test('save is a single explicit plan mutation and selected copy makes no API call',async()=>{
 const h=await opened();
 await loaded(h);startEdit(h,'Save me');
 h.respondPost('rooms/room/work-plan',{revision:3,nodes:[{...planNode('Save me')}]});
 const save=h.find('wp-save');assert.ok(save);await save.onclick();await tick();await tick();
 const posts=h.workPlanCalls('POST');
 assert.equal(posts.length,1);
 assert.deepEqual(posts[0].body,{expected_revision:2,nodes:[{id:'node1',title:'Save me',instruction:'Inspect only; never publish',assignee:'worker',write_scope:'analysis',depends_on:[]}]});
 assert.ok(posts[0].options.headers['Idempotency-Key']);
 assert.equal(h.calls.some(call=>call.path.endsWith('/messages')||call.path.endsWith('/tasks')),false,'saving never publishes or dispatches');
 const select=h.find('wp-select-node1');assert.ok(select);select.checked=true;select.onchange();
 const preview=h.find('wp-preview');assert.ok(preview);preview.onclick();
 const copy=h.find('wp-copy');assert.ok(copy);await copy.onclick();await tick();
 assert.equal(h.copied.length,1);
 assert.ok(h.copied[0].includes('Save me'));
 assert.equal(h.workPlanCalls('POST').length,1,'copying makes no plan mutation');
});

test('language switching relabels the mounted module without losing dirty text or refetching',async()=>{
 const h=await opened();
 await loaded(h);startEdit(h,'Language-safe draft');
 const before=h.workPlanCalls().length;
 h.run("view.language='en';language();");
 assert.equal(h.find('wp-title-0')?.value,'Language-safe draft');
 assert.ok(h.planText().includes(WORK_PLAN_LABELS.en.workPlanTitle));
 assert.equal(h.workPlanCalls().length,before,'language switching never refetches');
});

test('hostile plan text stays inert in the real app container',async()=>{
 const hostile='<img src=x onerror=alert(1)>';
 const h=await opened();
 await loaded(h,{title:hostile});
 assert.ok(h.planText().includes(hostile),'hostile title is shown as text');
 assert.equal(walkTags(h.planRoot()).includes('img'),false,'no markup element is created from draft text');
});

test('same-room SSE refresh preserves the explicitly loaded dispatch result',async()=>{
 const h=await opened();
 await loaded(h);
 h.respondGet('rooms/room/work-plan/dispatch',{run:appDispatchRun()});
 await h.find('wp-dispatch-refresh').onclick();await tick();await tick();
 assert.ok(h.planText().includes('task-1'),'the explicitly refreshed run is rendered');
 h.run("watchRoom('room');");
 const before={auth:h.run('authEpoch'),ticket:h.run('workPlanTicket'),gets:dispatchCalls(h,'GET').length};
 const source=h.stream();assert.ok(source);
 source.emit('room_update',{events:[{type:'message',id:'m-2'}]});
 await h.flushTimers();
 assert.equal(h.run('authEpoch'),before.auth,'same-session SSE does not change authEpoch');
 assert.equal(h.run('workPlanTicket'),before.ticket,'same-room SSE keeps the stable plan ticket');
 assert.ok(h.planText().includes('task-1'),'same-room SSE keeps the dispatch result');
 assert.ok(h.planText().includes('실행 중'),'same-room SSE keeps the run state');
 assert.equal(dispatchCalls(h,'GET').length,before.gets,'SSE never auto-refreshes dispatch');
});

test('a real room switch purges the previous dispatch result',async()=>{
 const h=await opened();
 await loaded(h);
 h.respondGet('rooms/room/work-plan/dispatch',{run:appDispatchRun()});
 await h.find('wp-dispatch-refresh').onclick();await tick();await tick();
 assert.ok(h.planText().includes('task-1'));
 const before={ticket:h.run('workPlanTicket'),gets:dispatchCalls(h,'GET').length};
 const b=makeRoom('room-b');
 h.respondGet('rooms/room-b',b);h.respondGet('rooms/room-b/interventions',[]);h.respondGet('rooms',[b]);
 await h.run("openRoom('room-b',false,false)");await tick();await tick();
 assert.ok(h.run('workPlanTicket')>before.ticket,'switch advances the stable plan ticket');
 assert.equal(h.planText().includes('task-1'),false,'the previous room dispatch result is purged');
 assert.equal(h.planText().includes('실행 중'),false);
 assert.ok(h.find('wp-load'),'the new room starts unloaded');
 assert.equal(dispatchCalls(h,'GET').length,before.gets,'switching rooms never auto-refreshes dispatch');
});

test('logout purges the dispatch result and remounts a private-free container',async()=>{
 const h=await opened();
 await loaded(h);
 h.respondGet('rooms/room/work-plan/dispatch',{run:appDispatchRun()});
 await h.find('wp-dispatch-refresh').onclick();await tick();await tick();
 assert.ok(h.planText().includes('task-1'));
 const before={ticket:h.run('workPlanTicket'),gets:dispatchCalls(h,'GET').length};
 h.run('signedOut();');
 assert.ok(h.run('workPlanTicket')>before.ticket);
 assert.equal(h.planRoot(),undefined,'logout clears the module');
 assert.equal(h.planText(),'');
 assert.equal(dispatchCalls(h,'GET').length,before.gets,'logout never fetches dispatch');
});

test('permission loss and membership changes purge the dispatch result immediately',async()=>{
 for(const change of ['permission','revision']){
  const h=await opened();
  await loaded(h);
  h.respondGet('rooms/room/work-plan/dispatch',{run:appDispatchRun()});
  await h.find('wp-dispatch-refresh').onclick();await tick();await tick();
  assert.ok(h.planText().includes('task-1'));
  const gets=dispatchCalls(h,'GET').length;
  if(change==='permission')h.run("view.room.effective_permissions=['room:read'];renderRoom(view.room);");
  else{h.run('view.room.membership_revision++;renderRoom(view.room);');}
  assert.equal(h.planText().includes('task-1'),false,change+' change purges the dispatch result');
  assert.equal(h.planText().includes('실행 중'),false);
  assert.equal(dispatchCalls(h,'GET').length,gets,change+' change never auto-refreshes dispatch');
 }
});

test('the real app preserves backend error codes and reports zero queued tasks',async()=>{
 const errorCase=await opened();
 await loaded(errorCase);
 errorCase.respondPost('rooms/room/work-plan/dispatch',{},409,'room_busy');
 ackDispatch(errorCase);
 await errorCase.find('wp-dispatch-start').onclick();await tick();await tick();
 assert.ok(errorCase.planText().includes(WORK_PLAN_LABELS.ko.workPlanDispatchRoomBusy),'the app api preserves error.code');
 assert.equal(dispatchCalls(errorCase,'POST').length,1);
 assert.equal(dispatchCalls(errorCase,'GET').length,0,'a coded failure never auto-refreshes');
 await tick();
 assert.equal(dispatchCalls(errorCase,'POST').length,1,'a coded failure never auto-retries');

 const zeroCase=await opened();
 await loaded(zeroCase);
 zeroCase.respondPost('rooms/room/work-plan/dispatch',{run:appDispatchRun(),tasks_queued:0});
 ackDispatch(zeroCase);
 await zeroCase.find('wp-dispatch-start').onclick();await tick();await tick();
 assert.ok(zeroCase.planText().includes(WORK_PLAN_LABELS.ko.workPlanDispatchQueuedZero));
 assert.equal(dispatchCalls(zeroCase,'POST').length,1);
 assert.equal(dispatchCalls(zeroCase,'GET').length,0);
});

test('a missing real module factory is fatal for the marked production container, not silently hidden',async()=>{
 const h=await harness({factory:false});
 h.node('work-plan-container').dataset.workPlan='required';
 const room=makeRoom();
 assert.throws(()=>h.run(`view.select('room');view.room=${JSON.stringify(room)};renderRoom(view.room);`),/work-plan: module unavailable/);
});

function walkTags(root){const tags=[];const queue=[root];while(queue.length){const current=queue.shift();if(!current||typeof current!=='object')continue;if(current.tagName)tags.push(current.tagName);if(Array.isArray(current.children))queue.push(...current.children);}return tags;}
