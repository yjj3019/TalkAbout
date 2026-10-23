import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ViewState,labels} from '../talkabout/web/state.mjs';
const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
async function harness(){
 const nodes=new Map(),calls=[],timeouts=[],listeners={};let resolve;
 const held=new Promise(done=>{resolve=done;});
 function node(id){if(!nodes.has(id))nodes.set(id,{value:'',type:'password',textContent:'',hidden:false,open:false,append(){},replaceChildren(){},close(){this.open=false;},showModal(){this.open=true;},querySelector(){return node(id+'-button');}});return nodes.get(id);}
 const context=vm.createContext({ViewState,labels,crypto:globalThis.crypto,AbortController,URLSearchParams,confirm:()=>true,
  document:{getElementById:node,createElement:()=>node('synthetic-created-'+nodes.size),documentElement:{},querySelectorAll:()=>[]},window:{setInterval:()=>1,clearInterval(){},clearTimeout(){},setTimeout:fn=>{timeouts.push(fn);return timeouts.length;},addEventListener:(name,fn)=>{listeners[name]=fn;}},
  location:{search:'',origin:'http://127.0.0.1:18081'},history:{pushState(){},replaceState(){}},
  fetch:async(url,options)=>{calls.push({url,options});if(url.endsWith('setup/status'))return{ok:true,json:async()=>({setup_required:true})};if(url.endsWith('setup/csrf'))return{ok:true,json:async()=>({csrf_token:'synthetic-csrf'})};if(url.endsWith('/credentials'))return held;return{ok:false,status:401};}});
 vm.runInContext(source,context);await new Promise(setImmediate);
 vm.runInContext("refresh=async()=>{};user={id:'human',scopes:['agent:manage','credential:manage']};view.select('room');",context);
 return {node,calls,timeouts,listeners,run:code=>vm.runInContext(code,context),resolve:value=>resolve({ok:true,json:async()=>value})};
}
for(const boundary of ['logout','room navigation'])test('late credential response is discarded after '+boundary,async()=>{
 const h=await harness();h.run("issueCredential({id:'room'},{id:'agent'});");await new Promise(setImmediate);
 if(boundary==='logout')h.run('signedOut();');else h.run("clearDetail();view.select('other');");
 const response={token:'synthetic-private-token',expires_at:2000000000};h.resolve(response);await new Promise(setImmediate);
 assert.equal(h.node('credential-value').value,'');assert.equal(h.node('credential-dialog').open,false);assert.equal(response.token,'');
 assert.equal(h.calls.filter(c=>c.options?.method==='POST').length,1);assert.ok(h.calls.every(c=>!c.url.includes('synthetic-private-token')));
});
test('replayed credential metadata gives recovery instructions without displaying a secret',async()=>{
 const h=await harness();h.run("issueCredential({id:'room'},{id:'agent'});");await new Promise(setImmediate);
 h.resolve({token_not_recoverable:true});await new Promise(setImmediate);
 assert.equal(h.node('credential-value').value,'');assert.equal(h.node('notice').textContent,labels.ko.credentialUnavailable);
});
test('explicit close clears private input and onboarding fields on navigation',async()=>{
 const h=await harness();h.node('credential-value').value='synthetic-private-token';h.node('onboarding-prompt').value='synthetic-private-topic';
 h.run('clearDetail();');assert.equal(h.node('credential-value').value,'');assert.equal(h.node('onboarding-prompt').value,'');
});
test('one-time credential clears on its timeout and on page exit',async()=>{
 const h=await harness();h.run("issueCredential({id:'room'},{id:'agent'});");await new Promise(setImmediate);
 h.resolve({token:'synthetic-private-token',expires_at:2000000000});await new Promise(setImmediate);
 assert.equal(h.node('credential-dialog').open,true);assert.equal(h.node('credential-value').value,'synthetic-private-token');
 h.timeouts[0]();assert.equal(h.node('credential-dialog').open,false);assert.equal(h.node('credential-value').value,'');
 h.node('credential-value').value='synthetic-second-token';h.listeners.pagehide();assert.equal(h.node('credential-value').value,'');
});
test('browser back to the room list clears the credential before any fetch',async()=>{
 const h=await harness();h.node('credential-value').value='synthetic-private-token';
 await h.run('openRoom(null);');assert.equal(h.node('credential-value').value,'');assert.equal(h.node('room-browser').hidden,false);
});

for(const lang of ['ko','en'])test('participation guide starts the worker before browser discussion start: '+lang,async()=>{
 const h=await harness();h.run(`view.language='${lang}';showOnboarding({id:'room',topic:'Synthetic',mode:'free_discussion'},{id:'agent',display_name:'Synthetic',handle:'synthetic'});`);
 h.run("el('onboarding-turn-consent').checked=true;renderOnboardingCommands();");const commands=h.node('onboarding-commands').textContent;
 assert.ok(commands.indexOf('cli check')<commands.indexOf('cli join'));
 assert.ok(commands.indexOf('cli join')<commands.indexOf('cli connect'));
 assert.ok(commands.indexOf(labels[lang].workerBeforeStart)<commands.indexOf('cli connect'));
 assert.ok(commands.indexOf('cli connect')<commands.indexOf(labels[lang].startAfterWorker));
 assert.ok(commands.includes('--request-turn'));
});

test('preparing workflow explains why Start is disabled',async()=>{
 const h=await harness();h.run("room={effective_permissions:['room:control','room:approve'],members:{}};");
 assert.equal(h.run('startReadiness(room)'),'startNeedInvite');
 h.run("room.members.a={kind:'agent',status:'pending'};");assert.equal(h.run('startReadiness(room)'),'startNeedAccept');
 h.run("room.members.a.status='active';room.members.a.permissions=[...participantPermissions];room.members.a.connection={runtime:'unknown',state:'idle',last_seen_at:Date.now()/1000};");assert.equal(h.run('startReadiness(room)'),'startNeedWorker');
 h.run("room.members.a.connection.runtime='codex';");assert.equal(h.run('startReadiness(room)'),'startNeedWorker');
 h.run("room.members.a.runtime_check={runtime:'codex',status:'passed',verification:'worker_reported_test',expires_at:Date.now()/1000+300};");assert.equal(h.run('startReadiness(room)'),'startReady');
 h.run("room.members.a.connection.state='stopped';");assert.equal(h.run('startReadiness(room)'),'startNeedWorker');
 h.run("room.effective_permissions=[];");assert.equal(h.run('startReadiness(room)'),'startNeedPermission');
});
