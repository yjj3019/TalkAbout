// UI logic harness; this does not replace browser DOM verification.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ViewState,labels} from '../talkabout/web/state.mjs';
const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
async function harness(locked=true,submitOK=true,errorCode=null,container='talkabout-fixture-app'){
 const nodes=new Map(),calls=[],listeners={},timers=[];
 let csrfCookie=null,nonce=0,raceOnce=false,completeOnce=false,onPost=null;const delayedStatuses=[];
 function node(id){if(!nodes.has(id))nodes.set(id,{value:'',hidden:['setup','dashboard','detail','intervention-panel'].includes(id),textContent:'',disabled:false,replaceChildren(){},querySelector(){return node(id+'-button');}});return nodes.get(id);}
 const context={ViewState,labels,document:{getElementById:node,documentElement:{},querySelectorAll:()=>[],addEventListener:(name,fn)=>{listeners[name]=fn;}},window:{addEventListener:(name,fn)=>{listeners[name]=fn;},setInterval:fn=>{timers.push(fn);return timers.length;},clearInterval:id=>{timers[id-1]=null;}},location:{search:''},history:{},URLSearchParams,AbortController,crypto:globalThis.crypto,
 fetch:async(url,options={})=>{calls.push({url,options});if(url.endsWith('setup/status')&&delayedStatuses.length)return delayedStatuses.shift();if(url.endsWith('setup/status'))return {ok:true,json:async()=>({setup_required:locked,setup_container:container})};if(url.endsWith('setup/csrf')){csrfCookie='synthetic-csrf-'+(++nonce);const token=csrfCookie;return {ok:true,json:async()=>({csrf_token:token})};}if(url.endsWith('setup/complete')){onPost?.();if(raceOnce){csrfCookie='synthetic-other-tab';raceOnce=false;}if(completeOnce){locked=false;completeOnce=false;}const ok=submitOK&&locked&&csrfCookie===options.headers['X-CSRF-Token'];if(ok)locked=false;return {ok,status:ok?200:locked?403:409,json:async()=>({code:errorCode,status:'complete',login_required:true})};}return {ok:false,status:401};}};
 vm.runInNewContext(source,context);
 await new Promise(setImmediate);
 return {node,calls,listeners,timers,onPost:fn=>{onPost=fn;},deferStatus:()=>{let resolve,reject;delayedStatuses.push(new Promise((yes,no)=>{resolve=yes;reject=no;}));return {resolve:value=>resolve({ok:true,json:async()=>({setup_required:value,setup_container:container})}),reject:()=>reject(new Error('synthetic-network-failure'))};},expireCookie:()=>{csrfCookie=null;},replaceCookie:()=>{csrfCookie='synthetic-other-tab';},setLocked:value=>{locked=value;},raceOnSubmit:()=>{raceOnce=true;},completeOnSubmit:()=>{completeOnce=true;}};
}
test('first setup stays locked, clears secrets and returns to existing login',async()=>{
 const {node,calls}=await harness();
 assert.equal(node('setup').hidden,false);assert.equal(node('login').hidden,true);
 for(const [id,value] of Object.entries({'setup-code':'synthetic-code','setup-workspace':'fixture','setup-name':'Owner','setup-username':'owner','setup-password':'synthetic-password-only','setup-confirmation':'synthetic-password-only'}))node(id).value=value;
 await node('setup').onsubmit({preventDefault(){}});
 assert.equal(node('setup').hidden,true);assert.equal(node('login').hidden,false);assert.equal(node('dashboard').hidden,true);
 for(const id of ['setup-code','setup-password','setup-confirmation'])assert.equal(node(id).value,'');
 assert.equal(node('workspace').value,'fixture');
 const request=calls.find(c=>c.url.endsWith('setup/complete'));assert.equal(request.options.headers['X-CSRF-Token'],'synthetic-csrf-2');
 assert.equal(JSON.parse(request.options.body).code,'synthetic-code');
 assert.equal(calls.some(c=>c.url.includes('synthetic-code')||c.url.endsWith('session')),false);
});
for(const scenario of ['expired cookie after operator reissue','another setup tab replaced cookie'])test(scenario,async()=>{
 const h=await harness();
 if(scenario.startsWith('expired'))h.expireCookie();else h.replaceCookie();
 h.node('setup-code').value='synthetic-reissued';h.node('setup-password').value='synthetic-password-only';h.node('setup-confirmation').value='synthetic-password-only';
 await h.node('setup').onsubmit({preventDefault(){}});
 assert.equal(h.node('setup').hidden,true);assert.equal(h.node('login').hidden,false);
 assert.equal(h.calls.filter(c=>c.url.endsWith('setup/complete')).length,1);
});
test('cookie race refreshes state without automatic POST retry or secret retention',async()=>{
 const h=await harness();h.raceOnSubmit();h.node('setup-code').value='synthetic-reissued';h.node('setup-password').value='synthetic-password-only';h.node('setup-confirmation').value='synthetic-password-only';
 await h.node('setup').onsubmit({preventDefault(){}});
 assert.equal(h.node('setup').hidden,false);assert.equal(h.node('login').hidden,true);
 assert.equal(h.node('setup-code').value,'');assert.equal(h.node('setup-password').value,'');
 assert.equal(h.calls.filter(c=>c.url.endsWith('setup/complete')).length,1);
 assert.equal(h.calls.at(-2).url,'/api/v1/setup/status');assert.equal(h.calls.at(-1).url,'/api/v1/setup/csrf');
 assert.match(h.node('notice').textContent,/자동 재전송하지/);
});
test('completion in another tab before submit closes form without POST',async()=>{
 const h=await harness();h.setLocked(false);h.node('setup-code').value='synthetic-unused';h.node('setup-password').value='synthetic-password-only';
 await h.node('setup').onsubmit({preventDefault(){}});
 assert.equal(h.node('setup').hidden,true);assert.equal(h.node('login').hidden,false);assert.equal(h.node('setup-password').value,'');
 assert.equal(h.calls.some(c=>c.url.endsWith('setup/complete')),false);
});
test('completion during POST refreshes status and closes setup without retry',async()=>{
 const h=await harness();h.completeOnSubmit();
 await h.node('setup').onsubmit({preventDefault(){}});
 assert.equal(h.node('setup').hidden,true);assert.equal(h.node('login').hidden,false);
 assert.equal(h.calls.filter(c=>c.url.endsWith('setup/complete')).length,1);
 assert.equal(h.calls.at(-1).url,'/api/v1/setup/status');
});
test('rejected setup keeps normal login/dashboard locked and clears inputs',async()=>{
 const {node}=await harness(true,false);
 node('setup-code').value='synthetic-wrong';node('setup-password').value='synthetic-password-only';node('setup-confirmation').value='synthetic-password-only';
 await node('setup').onsubmit({preventDefault(){}});
 assert.equal(node('setup').hidden,false);assert.equal(node('login').hidden,true);assert.equal(node('dashboard').hidden,true);
 assert.equal(node('setup-code').value,'');assert.equal(node('setup-password').value,'');assert.equal(node('setup-button').disabled,false);
});
test('existing installation uses normal session flow without issuing setup CSRF',async()=>{
 const {node,calls}=await harness(false);
 assert.equal(node('setup').hidden,true);assert.equal(node('login').hidden,false);
 assert.equal(calls.some(c=>c.url.endsWith('setup/csrf')),false);
 assert.equal(calls.some(c=>c.url.endsWith('session')),true);
});

test('guide command uses only validated public container metadata and translates',async()=>{
 const h=await harness();assert.equal(h.node('setup-command').textContent,'podman exec -it talkabout-fixture-app python -m talkabout.container setup-code');
 assert.equal(h.node('setup-container-note').textContent,labels.ko.guideContainerKnown);
 h.node('language').onclick();assert.equal(h.node('setup-container-note').textContent,labels.en.guideContainerKnown);
 for(const value of [null,'bad; command','../private']){const other=await harness(true,true,null,value);assert.match(other.node('setup-command').textContent,/APP_CONTAINER/);assert.equal(other.node('setup-container-note').textContent,labels.ko.guideContainerUnknown);}
});
for(const event of ['focus','online','visibilitychange','poll'])test('server state closes an existing setup tab on '+event,async()=>{
 const h=await harness();const csrfCalls=h.calls.filter(c=>c.url.endsWith('setup/csrf')).length;
 h.node('setup-code').value='synthetic-unused';h.setLocked(false);
 if(event==='poll')await h.timers[0]();else h.listeners[event]();await new Promise(setImmediate);
 assert.equal(h.node('setup').hidden,true);assert.equal(h.node('login').hidden,false);assert.equal(h.node('setup-code').value,'');assert.equal(h.timers[0],null);
 assert.equal(h.calls.filter(c=>c.url.endsWith('setup/csrf')).length,csrfCalls);
 assert.equal(h.calls.some(c=>c.url.endsWith('setup/complete')),false);
});
test('focus and polling keep pending form visible without rotating CSRF',async()=>{
 const h=await harness();h.listeners.focus();await new Promise(setImmediate);await h.timers[0]();await new Promise(setImmediate);
 assert.equal(h.node('setup').hidden,false);assert.equal(h.calls.filter(c=>c.url.endsWith('setup/csrf')).length,1);
});

const settle=()=>new Promise(setImmediate);
const statusCount=h=>h.calls.filter(c=>c.url.endsWith('setup/status')).length;
test('a response delayed across repeated five-second ticks still closes setup',async()=>{
 const h=await harness(),before=statusCount(h),held=h.deferStatus(),poll=h.timers[0];
 h.node('setup-code').value='synthetic-unused';h.node('setup-password').value='synthetic-unused';h.node('setup-confirmation').value='synthetic-unused';
 // Deterministic timer ticks model a response slower than multiple polling periods.
 for(let tick=0;tick<8;tick++){poll();h.listeners.focus();h.listeners.online();await settle();}
 assert.equal(statusCount(h),before+1);assert.equal(h.node('setup').hidden,false);
 h.setLocked(false);held.resolve(false);await settle();
 assert.equal(h.node('setup').hidden,true);assert.equal(h.node('login').hidden,false);assert.equal(h.timers[0],null);
 for(const id of ['setup-code','setup-password','setup-confirmation'])assert.equal(h.node(id).value,'');
 assert.equal(h.calls.filter(c=>c.url.endsWith('setup/csrf')).length,1);
 assert.equal(h.calls.some(c=>c.url.endsWith('setup/complete')),false);
});
test('successive slow responses settle without overlapping requests or starvation',async()=>{
 const h=await harness(),before=statusCount(h);
 for(let cycle=0;cycle<3;cycle++){
  const held=h.deferStatus();for(let tick=0;tick<4;tick++){h.timers[0]();await settle();}
  assert.equal(statusCount(h),before+cycle+1);held.resolve(cycle<2);await settle();
  assert.equal(h.node('setup').hidden,cycle===2);
 }
 assert.equal(h.timers[0],null);
});
test('a failed shared poll releases the flight for the next status check',async()=>{
 const h=await harness(),before=statusCount(h),held=h.deferStatus();h.timers[0]();h.listeners.focus();await settle();
 assert.equal(statusCount(h),before+1);held.reject();await settle();
 h.setLocked(false);h.timers[0]();await settle();assert.equal(statusCount(h),before+2);
 assert.equal(h.node('setup').hidden,true);assert.equal(h.timers[0],null);
});
test('submission waits for a slow poll then rechecks completion before any POST',async()=>{
 const h=await harness(),before=statusCount(h),held=h.deferStatus();h.timers[0]();await settle();
 const submit=h.node('setup').onsubmit({preventDefault(){}});await settle();
 assert.equal(statusCount(h),before+1);assert.equal(h.calls.some(c=>c.url.endsWith('setup/complete')),false);
 h.setLocked(false);held.resolve(true);await submit;
 assert.equal(statusCount(h),before+2);assert.equal(h.node('setup').hidden,true);assert.equal(h.timers[0],null);
 assert.equal(h.calls.some(c=>c.url.endsWith('setup/complete')),false);
});
test('post-success check waits for an older poll and fetches state after mutation',async()=>{
 const h=await harness();let held;
 h.onPost(()=>{held=h.deferStatus();h.timers[0]();});
 const submit=h.node('setup').onsubmit({preventDefault(){}});await settle();
 assert.ok(held);assert.equal(h.node('setup').hidden,false);const before=statusCount(h);
 h.listeners.focus();await settle();assert.equal(statusCount(h),before);
 held.resolve(true);await submit;
 assert.equal(statusCount(h),before+1);assert.equal(h.node('setup').hidden,true);assert.equal(h.timers[0],null);
 assert.equal(h.calls.filter(c=>c.url.endsWith('setup/complete')).length,1);
 assert.equal(h.node('notice').textContent,labels.ko.setupDone);
});
for(const [code,key] of Object.entries({setup_unavailable:'setupCodeUnavailable',setup_invalid:'setupCodeInvalid',setup_attempt_limit:'setupCodeLimited',reserved_name:'setupReserved',invalid_handle:'setupHandleInvalid',invalid_name:'setupNameInvalid',invalid_workspace:'setupWorkspaceInvalid',invalid_password:'setupPasswordInvalid',password_confirmation_mismatch:'setupConfirmationInvalid',origin_forbidden:'setupOriginInvalid',csrf_invalid:'setupCsrfInvalid',unknown_private_detail:'setupRetry',toString:'setupRetry'}))test('fixed localized error mapping: '+code,async()=>{
 const h=await harness(true,false,code);h.node('setup-code').value='synthetic-code';h.node('setup-password').value='synthetic-password-only';
 await h.node('setup').onsubmit({preventDefault(){}});assert.equal(h.node('notice').textContent,labels.ko[key]);assert.equal(h.node('setup-code').value,'');assert.equal(h.node('setup-password').value,'');assert.equal(h.calls.filter(c=>c.url.endsWith('setup/complete')).length,1);
});
