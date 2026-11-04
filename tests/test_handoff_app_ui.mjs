import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {createHandoffSummaryView} from '../talkabout/web/handoff-summary.mjs';

const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'').split('function card(text)')[0];
class Node {
 constructor(){this.children=[];this.hidden=false;this.text='';}
 set textContent(value){this.text=value;this.children=[];}
 get textContent(){return this.text+this.children.map(x=>x.textContent).join('');}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.text='';this.children=items;}
}
const room=id=>({id,can_resolve_uncertain:true,membership_revision:1,effective_permissions:['room:read','room:control'],members:{u:{status:'active',epoch:1}}});
function setup(fetch){
 const nodes=new Map(),node=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
 const context=vm.createContext({ViewState:class{constructor(){this.language='en';this.room=null;}},labels:{},createHandoffSummaryView,document:{getElementById:node,createElement:()=>new Node()},fetch,TextDecoder,Uint8Array});
 const run=code=>vm.runInContext(code,context);run(source);run("user={id:'u',kind:'human'};view.room="+JSON.stringify(room('a')));return {run,node};
}
const response=data=>new Response(JSON.stringify(data));
const tick=()=>new Promise(setImmediate);
test('mounts manually, retains on refresh and clears on changed context',async()=>{
 const calls=[];const h=setup(async(url,options)=>{calls.push([url,options]);return response({run:null});});
 h.run('renderHandoffSummary(view.room)');const container=h.node('handoff-summary-container');
 assert.equal(calls.length,0);assert.match(container.textContent,/Not loaded/);
 container.children[0].children.at(-1).onclick();await tick();await tick();
 assert.equal(calls[0][0],'/api/v1/rooms/a/work-plan/dispatch/handoff');assert.equal(calls[0][1].credentials,'same-origin');assert.match(container.textContent,/No current run/);
 h.run('renderHandoffSummary(view.room)');assert.match(container.textContent,/No current run/);
 h.run('view.room.membership_revision++;renderHandoffSummary(view.room)');assert.doesNotMatch(container.textContent,/No current run/);
 h.run('workPlanTicket++;view.room='+JSON.stringify(room('b'))+';renderHandoffSummary(view.room)');assert.match(container.textContent,/Not loaded/);assert.equal(calls.length,1);
});
test('streaming cap rejects oversized body and cancels reader',async()=>{
 let cancelled=false;const stream=new ReadableStream({pull(controller){controller.enqueue(new Uint8Array(9000));},cancel(){cancelled=true;}});
 const h=setup(async()=>new Response(stream));
 await assert.rejects(h.run("handoffApi('rooms/a/work-plan/dispatch/handoff')"),/handoff_response_too_large/);assert.equal(cancelled,true);
});
test('missing imported factory is safe in stripped VM harness',()=>{
 const context=vm.createContext({ViewState:class{},labels:{},document:{},fetch(){}});assert.doesNotThrow(()=>vm.runInContext(source,context));
});
