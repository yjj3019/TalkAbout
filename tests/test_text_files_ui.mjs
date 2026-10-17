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

test('all attachment labels exist in both languages',()=>{
 const panel=html.slice(html.indexOf('id="text-files-panel"'),html.indexOf('<h3 data-i18n="tasks"'));
 for(const [,key] of panel.matchAll(/data-i18n="([^"]+)"/g))for(const lang of ['ko','en'])assert.equal(typeof labels[lang][key],'string',lang+':'+key);
});
test('late attachment 401 cannot log out a replacement session',async()=>{
 const h=await harness();let finish;h.handlers.set('rooms/old/files',()=>new Promise(resolve=>finish=resolve));
 const pending=h.run("textFileRequest('rooms/old/files').catch(error=>error.status)");
 h.run("authEpoch++;user={id:'replacement',scopes:[]};");finish({ok:false,status:401,json:async()=>({code:'unauthorized'})});
 assert.equal(await pending,401);assert.equal(h.run('user.id'),'replacement');
});
test('unsupported attachment format never starts an HTTP upload',async()=>{
 const h=await harness();h.run("view.selected='room';view.room={id:'room'};textAttachmentSettings={enabled:true,max_bytes:65536};");
 h.node('text-file-input').files=[{name:'source.pdf',size:10}];
 await h.node('text-file-upload').onsubmit({preventDefault(){}});
 assert.equal(h.node('file-upload-status').textContent,labels.ko.fileUnsupported);assert.equal(h.calls.filter(c=>c.path.includes('/uploads')).length,0);
});
for(const leave of [false,true])test('upload completion respects route epoch while tolerating same-room refresh '+leave,async()=>{
 const h=await harness();h.run("view.selected='room';view.room={id:'room',members:{}};textAttachmentSettings={enabled:true,max_bytes:65536};");
 h.node('text-file-input').files=[{name:'source.txt',size:3,type:'text/plain',arrayBuffer:async()=>new TextEncoder().encode('abc').buffer}];
 h.node('file-unscanned-ack').checked=true;h.node('file-agent-consent').checked=false;
 h.handlers.set('rooms/room/uploads',async()=>{h.run(leave?'textRoomEpoch++;view.epoch++;':'view.epoch++;');return {ok:true,json:async()=>({file_id:'file'})};});
 h.respond('rooms/room/uploads/file/content',{revision:2});
 h.respond('rooms/room/uploads/file/complete',{file_id:'file',version_id:'version',sha256:'hash',name:'source.txt'});h.respond('rooms/room/files',[]);
 await h.node('text-file-upload').onsubmit({preventDefault(){}});
 assert.equal(h.calls.filter(c=>c.path.includes('/uploads')).length,3);
 assert.equal(h.run("(pendingTextFiles.get('room')||[]).length"),leave?0:1);
});
