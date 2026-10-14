// UI logic harness; this does not replace browser DOM verification.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ViewState,labels} from '../talkabout/web/state.mjs';
const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
async function harness(locked=true,submitOK=true){
 const nodes=new Map(),calls=[];
 function node(id){if(!nodes.has(id))nodes.set(id,{value:'',hidden:['setup','dashboard','detail','intervention-panel'].includes(id),textContent:'',disabled:false,replaceChildren(){},querySelector(){return node(id+'-button');}});return nodes.get(id);}
 const context={ViewState,labels,document:{getElementById:node,documentElement:{},querySelectorAll:()=>[]},window:{},location:{search:''},history:{},URLSearchParams,AbortController,crypto:globalThis.crypto,
 fetch:async(url,options={})=>{calls.push({url,options});if(url.endsWith('setup/status'))return {ok:true,json:async()=>({setup_required:locked})};if(url.endsWith('setup/csrf'))return {ok:true,json:async()=>({csrf_token:'synthetic-csrf'})};if(url.endsWith('setup/complete'))return {ok:submitOK,status:submitOK?200:403,json:async()=>({status:'complete',login_required:true})};return {ok:false,status:401};}};
 vm.runInNewContext(source,context);
 await new Promise(setImmediate);
 return {node,calls};
}
test('first setup stays locked, clears secrets and returns to existing login',async()=>{
 const {node,calls}=await harness();
 assert.equal(node('setup').hidden,false);assert.equal(node('login').hidden,true);
 for(const [id,value] of Object.entries({'setup-code':'synthetic-code','setup-workspace':'fixture','setup-name':'Owner','setup-username':'owner','setup-password':'synthetic-password-only','setup-confirmation':'synthetic-password-only'}))node(id).value=value;
 await node('setup').onsubmit({preventDefault(){}});
 assert.equal(node('setup').hidden,true);assert.equal(node('login').hidden,false);assert.equal(node('dashboard').hidden,true);
 for(const id of ['setup-code','setup-password','setup-confirmation'])assert.equal(node(id).value,'');
 assert.equal(node('workspace').value,'fixture');
 const request=calls.at(-1);assert.equal(request.url,'/api/v1/setup/complete');assert.equal(request.options.headers['X-CSRF-Token'],'synthetic-csrf');
 assert.equal(JSON.parse(request.options.body).code,'synthetic-code');
 assert.equal(calls.some(c=>c.url.includes('synthetic-code')||c.url.endsWith('session')),false);
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
