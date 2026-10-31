// Synthetic DOM coverage for artifact review; no browser or server is required.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ViewState, labels} from '../talkabout/web/state.mjs';

const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
const room={id:'room',topic:'Review room',state:'active',mode:'co_creation',mode_status:'awaiting_adoption',generation:4,effective_permissions:['room:read','room:adopt','room:question'],members:{human:{kind:'human',status:'active',revision:7,display_name:'Reviewer'}},tasks:[],messages:[],artifacts:[]};
const tick=()=>new Promise(setImmediate);
async function harness(){
 class Node{
  constructor(tag='div'){this.tagName=tag;this.hidden=false;this.children=[];this.dataset={};this.attributes={};this.value='';this.ownText='';this.className='';this.htmlWrites=[];}
  set textContent(v){this.ownText=String(v);this.children=[];}get textContent(){return this.ownText+this.children.map(n=>n.textContent??n).join('');}
  set innerHTML(v){this.htmlWrites.push(v);this.children=[];this.ownText='';}get innerHTML(){return this.htmlWrites.at(-1)||'';}
  append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.ownText='';this.children=nodes;}
  setAttribute(k,v){this.attributes[k]=v;}getAttribute(k){return this.attributes[k];}
  get options(){return this.children;}get selectedOptions(){return this.children.filter(n=>n.value===this.value).slice(0,1);}
  querySelector(selector){return this.children.find(n=>selector==='.'+n.className)||null;}
  close(){this.open=false;}showModal(){this.open=true;}focus(){this.focused=true;}remove(){}
 }
 const nodes=new Map(),calls=[],handlers=new Map();
 const node=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
 const tabs=['discussion','participants','artifacts','management'].map(name=>{const n=node('tab-control-'+name);n.dataset.tab=name;return n;});
 const actions=['start','pause','resume','close','hold_dispatch','release_dispatch'].map(name=>{const n=new Node('button');n.dataset.action=name;return n;});
 const context=vm.createContext({ViewState,labels,crypto:globalThis.crypto,AbortController,URLSearchParams,EventSource:class{addEventListener(){} close(){}},
  document:{getElementById:node,createElement:tag=>new Node(tag),querySelectorAll:s=>s==='[data-tab]'?tabs:s==='[data-action]'?actions:[],documentElement:{}},
  window:{addEventListener(){},setInterval(){},clearInterval(){},setTimeout(){},clearTimeout(){}},location:{search:'',origin:'https://fixture.invalid'},history:{pushState(){},replaceState(){}},confirm:()=>true,
  fetch:async(url,options={})=>{const path=url.replace('/api/v1/','');calls.push({path,options});if(path==='setup/status')return {ok:true,json:async()=>({setup_required:true})};if(path==='setup/csrf')return {ok:true,json:async()=>({csrf_token:'synthetic'})};if(handlers.has(path))return handlers.get(path)(options);return {ok:false,status:403};}});
 vm.runInContext(source,context);await tick();
 const run=code=>vm.runInContext(code,context);
 run("user={id:'human',scopes:['room:read','room:adopt']}");
 const respond=(path,value)=>handlers.set(path,async()=>({ok:true,json:async()=>value}));
 respond('rooms',[room]);respond('rooms/room',room);respond('rooms/room/interventions',[]);
 return {node,run,calls,respond,show(versions,lang='en'){const displayed={...room,artifacts:[{id:'artifact-1',revision:12,versions}]};run(`view.language=${JSON.stringify(lang)};view.select('room');view.room=${JSON.stringify(displayed)};renderRoom(view.room);`);}};
}
const version=(id,text,extra={})=>({id,actor_id:'human',text,...extra});
const descendants=n=>[n,...(n.children||[]).flatMap(c=>typeof c==='object'?descendants(c):[])];
const byTag=(n,tag)=>descendants(n).filter(x=>x.tagName===tag);
const byClass=(n,name)=>descendants(n).filter(x=>(x.className||'').split(/\s+/).includes(name));
const button=(n,label)=>byTag(n,'button').find(x=>x.textContent===label);

test('patch is literal per-line text with diff classifications, never HTML',async()=>{
 const h=await harness();const patch='@@ -1,2 +1,2 @@\n-old <img src=x onerror=alert(1)>\n+new <script>alert(1)</script>\n context & "quoted"';
 h.show([version('p',patch,{kind:'patch'})]);const root=h.node('artifacts'),pre=byTag(root,'pre')[0];
 assert.ok(pre);assert.equal(pre.textContent,patch);
 const spans=byTag(pre,'span');assert.equal(spans.length,4);
 assert.equal(byClass(pre,'diff-hunk').length,1);assert.equal(byClass(pre,'diff-remove').length,1);assert.equal(byClass(pre,'diff-add').length,1);
 assert.match(byClass(pre,'diff-remove')[0].textContent,/<img src=x onerror=alert\(1\)>/);
 assert.match(byClass(pre,'diff-add')[0].textContent,/<script>alert\(1\)<\/script>/);
 assert.equal(descendants(pre).some(n=>n.htmlWrites?.length),false);
 assert.equal(byTag(pre,'img').length+byTag(pre,'script').length,0);
});

test('hunk body lines starting with --- or +++ stay additions and removals',async()=>{
 const h=await harness();const patch='--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n--- x\n+++ x\n context';
 h.show([version('p',patch,{kind:'patch'})]);const pre=byTag(h.node('artifacts'),'pre')[0];
 const lines=byClass(pre,'diff-line');
 assert.deepEqual(lines.map(n=>n.className.replace('diff-line ','')),['diff-header','diff-header','diff-hunk','diff-remove','diff-add','diff-context']);
 assert.equal(lines[3].textContent,'--- x\n');assert.equal(lines[4].textContent,'+++ x\n');
 assert.equal(byClass(pre,'diff-header').length,2);
 const headerOnly='--- a/f\n+++ b/f';h.show([version('q',headerOnly,{kind:'patch'})]);
 assert.equal(byClass(byTag(h.node('artifacts'),'pre')[0],'diff-header').length,2);
});

test('version labels stay stable when earlier versions are hidden',async()=>{
 const h=await harness();const first=version('first','old',{version_revision:1}),second=version('second','+new',{kind:'patch',version_revision:3});
 h.show([first,second]);assert.deepEqual(byClass(h.node('artifacts'),'version-tag').map(n=>n.textContent),['v1','v3']);
 h.show([second]);assert.deepEqual(byClass(h.node('artifacts'),'version-tag').map(n=>n.textContent),['v3']);
 h.show([version('legacy-id','legacy')]);assert.equal(byClass(h.node('artifacts'),'version-tag')[0].textContent,'legacy-id');
});

test('ordinary text versions remain plain text even when lines look like a diff',async()=>{
 const h=await harness();const text='+not a patch\n<script>literal</script>\n-old';
 h.show([version('t',text,{kind:'text'})]);const pre=byTag(h.node('artifacts'),'pre')[0];
 assert.equal(pre.textContent,text);assert.equal(byTag(pre,'span').length,0);
 assert.equal(descendants(pre).some(n=>n.htmlWrites?.length),false);
});

for(const language of ['ko','en'])test('patch review explains shape-only and reference evidence in '+language,async()=>{
 const h=await harness();const verified='worker_reported_server_validated_refs_not_model_read_receipt';
 h.show([version('validated','+a',{kind:'patch',input_references:{evidence:verified}}),version('legacy','+b',{kind:'patch',input_references:{evidence:'legacy_input_unverified'}}),version('missing','+c',{kind:'patch'}),version('unknown','+d',{kind:'patch',input_references:{evidence:'unexpected'}})],language);
 const root=h.node('artifacts'),items=byTag(root,'li');assert.equal(items.length,4);
 for(const item of items){assert.ok(item.textContent.includes(labels[language].patchReviewTitle));assert.ok(item.textContent.includes(labels[language].patchReviewCaution));}
 assert.match(labels[language].patchReviewCaution,/---/);assert.match(labels[language].patchReviewCaution,/\+\+\+/);
 assert.ok(items[0].textContent.includes(labels[language].patchInputRefsValidated));
 assert.ok(!items[0].textContent.includes(labels[language].patchInputRefsUnverified));
 for(const item of items.slice(1)){assert.ok(item.textContent.includes(labels[language].patchInputRefsUnverified));assert.ok(!item.textContent.includes(labels[language].patchInputRefsValidated));}
 assert.notEqual(labels[language].patchInputRefsValidated,labels[language].patchInputRefsUnverified);
 // Validated references must not be described as proof the model read them.
 if(language==='en')assert.match(labels.en.patchInputRefsValidated,/not.{0,30}(read|receipt)|does not.{0,30}read/i);
 else assert.match(labels.ko.patchInputRefsValidated,/(읽|열람|확인).{0,30}(아니|않|없|보장|증명)/);
 assert.ok(!items[0].textContent.includes(labels[language].journeyWorkerEvidence));
});

for(const language of ['ko','en'])test('review comment opens discussion and focuses existing form without posting in '+language,async()=>{
 const h=await harness();h.show([version('version-9','+line',{kind:'patch'})],language);
 h.run("roomTab('artifacts')");const root=h.node('artifacts');
 assert.ok(root.textContent.includes(labels[language].patchReviewCommentHelp));
 const comment=button(root,labels[language].patchReviewComment);assert.ok(comment);
 const before=h.calls.length;comment.onclick();await tick();
 assert.equal(h.node('tab-discussion').hidden,false);assert.equal(h.node('tab-artifacts').hidden,true);
 assert.equal(h.node('message-text').focused,true);
 assert.equal(h.calls.slice(before).some(c=>c.options.method==='POST'),false);
});

test('adopt retains the fenced version API and revision fields',async()=>{
 const h=await harness();h.show([version('version-9','+line',{kind:'patch'})]);
 h.respond('rooms/room/artifacts/artifact-1/adoptions',{});
 const adopt=button(h.node('artifacts'),labels.en.adopt);assert.ok(adopt);adopt.onclick();await tick();await tick();
 const call=h.calls.find(c=>c.path==='rooms/room/artifacts/artifact-1/adoptions');assert.ok(call);
 assert.equal(call.options.method,'POST');assert.deepEqual(JSON.parse(call.options.body),{generation:4,membership_revision:7,expected_revision:12,version_id:'version-9'});
 assert.ok(call.options.headers['Idempotency-Key']);
});
