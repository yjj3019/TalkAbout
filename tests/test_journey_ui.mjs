// Synthetic DOM only: never launches a provider, browser or server.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ViewState,labels} from '../talkabout/web/state.mjs';
const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
const html=readFileSync(new URL('../talkabout/web/index.html',import.meta.url),'utf8');
const agent={id:'synthetic-agent',display_name:'Synthetic agent',handle:'research-bot'};
async function harness({consent=true,scopes=['agent:manage','credential:manage']}={}){
 class Node{constructor(){this.value='';this.children=[];this.ownText='';this.type='password';this.hidden=false;this.open=false;this.disabled=false;this.listeners={};}set textContent(v){this.ownText=v;this.children=[];}get textContent(){return this.ownText+this.children.map(n=>n.textContent??n).join('');}append(...v){this.children.push(...v);}replaceChildren(...v){this.ownText='';this.children=v;}close(){this.open=false;}showModal(){this.open=true;}addEventListener(k,v){this.listeners[k]=v;}querySelector(){return new Node();}setAttribute(){}scrollIntoView(){}click(){this.clicked=true;}}
 const nodes=new Map(),calls=[],handlers=new Map(),timeouts=[],listeners={},copied=[],downloads=[];
 const node=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
 const context=vm.createContext({ViewState,labels,crypto:globalThis.crypto,AbortController,URLSearchParams,Blob,
 document:{getElementById:node,createElement:()=>new Node(),querySelectorAll:()=>[],documentElement:{}},
 window:{setInterval:()=>1,clearInterval(){},setTimeout:f=>{timeouts.push(f);return timeouts.length;},clearTimeout(){},addEventListener:(k,v)=>{listeners[k]=v;}},
 location:{origin:'https://synthetic.invalid',search:''},history:{pushState(){},replaceState(){}},confirm:()=>consent,navigator:{clipboard:{writeText:async v=>copied.push(v)}},URL:{createObjectURL:blob=>{downloads.push(blob);return 'blob:synthetic';},revokeObjectURL(){}},
 fetch:async(url,options={})=>{const path=url.replace('/api/v1/','');calls.push({path,options});if(handlers.has(path))return handlers.get(path)(options);const value=path==='setup/status'?{setup_required:true}:path==='setup/csrf'?{csrf_token:'synthetic'}:[];return {ok:true,json:async()=>value};}});
 const run=code=>vm.runInContext(code,context);run(source);await new Promise(setImmediate);
 run(`user={id:'human',scopes:${JSON.stringify(scopes)}};agents=[${JSON.stringify(agent)}];refresh=async()=>{};`);
 const respond=(path,data)=>handlers.set(path,async()=>({ok:true,json:async()=>data}));
 return {node,run,calls,handlers,timeouts,listeners,copied,downloads,respond};
}

const now=2000000000;
const live={...agent,connection:{runtime:'codex',state:'idle',last_seen_at:now},runtime_check:{id:'receipt',runtime:'codex',status:'passed',verification:'worker_reported_test',expires_at:now+100}};
const room={id:'room',topic:'Synthetic room',state:'active',members:{'synthetic-agent':{status:'active',response_evidence:{worker_reported:0}}}};
for(const [name,patch,target,expected]of [
 ['registered only',{},null,'journeyUnconnected'],
 ['checking',{runtime_check:{status:'pending',expires_at:now+100}},null,'journeyChecking'],
 ['failed',{connection:{state:'failed',error_code:'runtime_failed'}},null,'journeyFailed'],
 ['offline',{...live,connection:{...live.connection,last_seen_at:now-100}},null,'journeyOffline'],
 ['synthetic',{connection:{runtime:'synthetic'}},null,'journeySynthetic'],
 ['valid receipt is not room participation',live,null,'journeyRegistered'],
 ['accepted without response',live,room,'journeyAwaitResponse'],
 ['invited without contact',{}, {...room,members:{'synthetic-agent':{status:'pending'}}},'journeyPending'],
 ['not invited',{}, {...room,members:{}},'journeyRegistered'],
 ['old total count cannot establish completion',live,{...room,members:{'synthetic-agent':{status:'active',published_contributions:20}}},'journeyAwaitResponse'],
 ['read-only member cannot claim participation readiness',live,{...room,members:{'synthetic-agent':{status:'active',permissions:['room:read'],response_evidence:{worker_reported:1}}}},'journeyFailed'],
 ['current worker report',live,{...room,members:{'synthetic-agent':{status:'active',response_evidence:{worker_reported:1}}}},'journeyParticipating'],
 ['paused overrides response',live,{...room,state:'paused',members:{'synthetic-agent':{status:'active',response_evidence:{worker_reported:1}}}},'journeyPaused'],
 ['closed room never offers stale invitation acceptance',{}, {...room,state:'closed',members:{'synthetic-agent':{status:'pending'}}},'journeyClosed'],
 ['room mismatch', {...live,connection:{...live.connection,room_id:'other'}},room,'journeyFailed']
])test('journey evidence: '+name,async()=>{
 const h=await harness();h.run(`fixtureAgent=${JSON.stringify({...agent,...patch})};fixtureRoom=${JSON.stringify(target)};`);
 h.run("for(const member of Object.values(fixtureRoom?.members||{}))if(!Object.hasOwn(member,'permissions'))member.permissions=[...participantPermissions];");
 assert.equal(h.run(`journeyStatus(fixtureAgent,fixtureRoom,${now}).state`),expected);
});
test('journey resume URL contains identity metadata only and close forgets it',async()=>{
 const h=await harness();h.run('showConnection(agents[0]);');
 assert.ok(h.node('journey-status').textContent.includes(labels.ko.journeyUnconnected));
 assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
 h.node('connection-close').onclick();assert.equal(h.node('connection-wizard').hidden,true);
});
test('journey translations cover every state and the runtime execution boundary',()=>{
 for(const lang of ['ko','en'])for(const key of ['journeyMachine','journeyBoundary','journeyRegistered','journeyPending','journeyAwaitResponse','journeyParticipating','journeySynthetic','journeyRecover','journeyNever'])assert.equal(typeof labels[lang][key],'string');
 for(const lang of ['ko','en'])assert.ok(labels[lang].journeyMachine.includes('Windows')&&labels[lang].journeyMachine.includes('POSIX')&&labels[lang].journeyMachine.includes('Grok'));
});

test('a new registration keeps the selected runtime without issuing a credential',async()=>{
 const h=await harness();const created={...agent,id:'new-agent',handle:'new-bot'};h.respond('agents',created);
 h.node('agent-runtime').value='claude';h.node('agent-name').value='New synthetic agent';h.node('agent-handle').value='new-bot';
 h.node('register-agent').onsubmit({preventDefault(){}});await new Promise(setImmediate);
 assert.equal(h.node('connection-runtime').value,'claude');
 assert.equal(h.calls.filter(c=>c.options.method==='POST').length,1);
 assert.ok(!h.calls.some(c=>c.path.includes('/credentials')));
});
