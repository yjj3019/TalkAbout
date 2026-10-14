// UI logic harness; this does not replace browser DOM verification.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ViewState,labels} from '../talkabout/web/state.mjs';
const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
async function harness(locked=true,submitOK=true){
 const nodes=new Map(),calls=[];
 let csrfCookie=null,nonce=0,raceOnce=false,completeOnce=false;
 function node(id){if(!nodes.has(id))nodes.set(id,{value:'',hidden:['setup','dashboard','detail','intervention-panel'].includes(id),textContent:'',disabled:false,replaceChildren(){},querySelector(){return node(id+'-button');}});return nodes.get(id);}
 const context={ViewState,labels,document:{getElementById:node,documentElement:{},querySelectorAll:()=>[]},window:{},location:{search:''},history:{},URLSearchParams,AbortController,crypto:globalThis.crypto,
 fetch:async(url,options={})=>{calls.push({url,options});if(url.endsWith('setup/status'))return {ok:true,json:async()=>({setup_required:locked})};if(url.endsWith('setup/csrf')){csrfCookie='synthetic-csrf-'+(++nonce);const token=csrfCookie;return {ok:true,json:async()=>({csrf_token:token})};}if(url.endsWith('setup/complete')){if(raceOnce){csrfCookie='synthetic-other-tab';raceOnce=false;}if(completeOnce){locked=false;completeOnce=false;}const ok=submitOK&&locked&&csrfCookie===options.headers['X-CSRF-Token'];return {ok,status:ok?200:locked?403:409,json:async()=>({status:'complete',login_required:true})};}return {ok:false,status:401};}};
 vm.runInNewContext(source,context);
 await new Promise(setImmediate);
 return {node,calls,expireCookie:()=>{csrfCookie=null;},replaceCookie:()=>{csrfCookie='synthetic-other-tab';},setLocked:value=>{locked=value;},raceOnSubmit:()=>{raceOnce=true;},completeOnSubmit:()=>{completeOnce=true;}};
}
test('first setup stays locked, clears secrets and returns to existing login',async()=>{
 const {node,calls}=await harness();
 assert.equal(node('setup').hidden,false);assert.equal(node('login').hidden,true);
 for(const [id,value] of Object.entries({'setup-code':'synthetic-code','setup-workspace':'fixture','setup-name':'Owner','setup-username':'owner','setup-password':'synthetic-password-only','setup-confirmation':'synthetic-password-only'}))node(id).value=value;
 await node('setup').onsubmit({preventDefault(){}});
 assert.equal(node('setup').hidden,true);assert.equal(node('login').hidden,false);assert.equal(node('dashboard').hidden,true);
 for(const id of ['setup-code','setup-password','setup-confirmation'])assert.equal(node(id).value,'');
 assert.equal(node('workspace').value,'fixture');
 const request=calls.at(-1);assert.equal(request.url,'/api/v1/setup/complete');assert.equal(request.options.headers['X-CSRF-Token'],'synthetic-csrf-2');
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
