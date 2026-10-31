// Synthetic DOM/fetch only: no provider, browser or server process.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ViewState,labels} from '../talkabout/web/state.mjs';

const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');

function harness(){
 class Node{
  constructor(){this.value='';this.children=[];this.ownText='';this.type='password';this.hidden=false;this.open=false;this.disabled=false;this.listeners={};this.classList={add(){},remove(){}};this.dataset={};this.attributes={};}
  set textContent(v){this.ownText=v;this.children=[];}get textContent(){return this.ownText+this.children.map(n=>n.textContent??n).join('');}
  append(...v){this.children.push(...v);}replaceChildren(...v){this.ownText='';this.children=v;}get options(){return this.children;}
  close(){this.open=false;}showModal(){this.open=true;}addEventListener(k,v){this.listeners[k]=v;}querySelector(){return new Node();}
  setAttribute(k,v){this.attributes[k]=v;}getAttribute(k){return this.attributes[k];}removeAttribute(){}scrollIntoView(){}click(){this.clicked=true;}focus(){}replaceWith(){}remove(){}
 }
 const nodes=new Map(),calls=[],handlers=new Map();
 const node=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
 const context=vm.createContext({ViewState,labels,crypto:globalThis.crypto,AbortController,URLSearchParams,Blob,
  document:{getElementById:node,createElement:()=>new Node(),querySelectorAll:()=>[],documentElement:{}},
  window:{setInterval:()=>1,clearInterval(){},setTimeout:f=>{f();return 1;},clearTimeout(){},addEventListener(){}},
  location:{origin:'https://synthetic.invalid',search:''},history:{pushState(){},replaceState(){}},confirm:()=>true,
  navigator:{clipboard:{writeText:async()=>{}}},
  URL:{createObjectURL:()=>'blob:synthetic',revokeObjectURL(){}},
  EventSource:class{addEventListener(){}close(){}},
  fetch:async(url,options={})=>{const path=url.replace('/api/v1/','');calls.push({path,options});const handler=handlers.get(path);
   if(handler)return handler(options);
   const value=path==='setup/status'?{setup_required:true}:path==='setup/csrf'?{csrf_token:'synthetic'}:[];return {ok:true,json:async()=>value};}});
 const run=code=>vm.runInContext(code,context);run(source);run("user={id:'human',scopes:['room:read']};agents=[];view.language='ko';");
 const respond=(path,data)=>handlers.set(path,async()=>({ok:true,json:async()=>data}));
 const pending=(path,data)=>{let resolve;handlers.set(path,()=>new Promise(done=>{resolve=()=>done({ok:true,json:async()=>data});}));return ()=>resolve();};
 return {node,run,calls,handlers,respond,pending};
}

const task=(id)=>({id,state:'queued',assignee:'synthetic-agent',purpose:'proposal',attempt:0,generation:1});
const room=(id,taskId)=>({
 id,topic:'Synthetic room',mode:'bounded_rounds',state:'active',generation:1,room_revision:1,limits:{rounds:1},
 members:{'synthetic-agent':{kind:'agent',status:'active',display_name:'Synthetic agent',handle:'synthetic-bot',permissions:['room:read']}},
 messages:[],tasks:[task(taskId)],artifacts:[],membership_revision:1,mode_status:'running',mode_progress:{},dispatch_held:false,effective_permissions:[]
});

const tick=()=>new Promise(setImmediate);

test('observed waiting reasons render only after an explicit read-only request',async()=>{
 const h=harness();
 const current=room('room','task-1');
 h.respond('rooms/room',current);
 h.respond('rooms/room/interventions',[]);
 h.respond('rooms/room/diagnostics',{room_id:'room',room_state:'active',dispatch_held:false,last_seq:0,membership_revision:1,
  tasks:[{task_id:'task-1',state:'queued',assignee:'synthetic-agent',attempt:0,waiting_reasons:['awaiting_worker_claim']}]});
 await h.run("openRoom('room',false,false)");
 assert.ok(!h.calls.some(call=>call.path==='rooms/room/diagnostics'));
 h.node('tasks-diagnostics').onclick();
 await tick();await tick();
 const text=h.node('tasks').textContent;
 assert.ok(text.includes(labels.ko.waitingReasonLabel));
 assert.ok(text.includes(labels.ko.waiting_awaiting_worker_claim));
 assert.equal(h.run("view.room.tasks[0].state"),'queued');
});

test('a late diagnostics response for the previous room never renders in the new room',async()=>{
 const h=harness();
 h.respond('rooms/room-a',room('room-a','task-a'));
 h.respond('rooms/room-a/interventions',[]);
 const releaseA=h.pending('rooms/room-a/diagnostics',{room_id:'room-a',tasks:[{task_id:'task-a',waiting_reasons:['dispatch_held']}]});
 h.respond('rooms/room-b',room('room-b','task-b'));
 h.respond('rooms/room-b/interventions',[]);
 h.respond('rooms/room-b/diagnostics',{room_id:'room-b',tasks:[]});
 await h.run("openRoom('room-a',false,false)");
 h.node('tasks-diagnostics').onclick();
 await h.run("openRoom('room-b',false,false)");
 h.node('tasks-diagnostics').onclick();
 releaseA();await tick();await tick();
 const text=h.node('tasks').textContent;
 assert.ok(!text.includes(labels.ko.waitingReasonLabel));
 assert.ok(!text.includes(labels.ko.waiting_dispatch_held));
});

test('a refused diagnostics request leaves tasks visible without reasons',async()=>{
 const h=harness();
 h.respond('rooms/room',room('room','task-1'));
 h.respond('rooms/room/interventions',[]);
 h.handlers.set('rooms/room/diagnostics',async()=>({ok:false,status:403,json:async()=>({code:'human_required'})}));
 await h.run("openRoom('room',false,false)");
 h.node('tasks-diagnostics').onclick();
 await tick();await tick();
 const text=h.node('tasks').textContent;
 assert.ok(text.includes('Synthetic agent'));
 assert.ok(!text.includes(labels.ko.waitingReasonLabel));
});
