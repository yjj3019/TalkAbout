// Synthetic DOM/fetch/clipboard only. No provider, operating account or credential.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ViewState,labels} from '../talkabout/web/state.mjs';
const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
const html=readFileSync(new URL('../talkabout/web/index.html',import.meta.url),'utf8');
const permissions=['room:read','room:speak','room:contribute','task:claim','task:heartbeat','task:complete','task:skip'];
const scopes=[...permissions,'agent:self_read','agent:capabilities_write'];
const agent={id:'synthetic-agent',display_name:'Synthetic agent',handle:'fixture-agent'};
const key=(patch={})=>({credential_id:'synthetic-key',room_id:'room',scopes,expires_at:Date.now()/1000+3600,revoked:false,...patch});
const issued=(patch={})=>({...key(),token:'synthetic-room-secret',...patch});
const tick=()=>new Promise(setImmediate);
async function harness({language='ko',origin='http://127.0.0.1:19999',consent=true,manage=true}={}){
 class Node{
  constructor(){this.value='';this.children=[];this.text='';this.type='password';this.hidden=false;this.open=false;this.disabled=false;this.listeners={};this.checked=false;}
  set textContent(v){this.text=v??'';this.children=[];}get textContent(){return this.text+this.children.map(n=>n.textContent??n).join('');}
  append(...v){this.children.push(...v);}replaceChildren(...v){this.text='';this.children=v;}get options(){return this.children;}
  close(){this.open=false;}showModal(){this.open=true;}addEventListener(k,v){this.listeners[k]=v;}setAttribute(){}scrollIntoView(){}querySelector(){return new Node();}click(){this.clicked=true;}
 }
 const nodes=new Map(),calls=[],handlers=new Map(),copies=[],downloads=[],timers=[],confirms=[];
 const node=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
 let clipboardFails=false;
 const context=vm.createContext({ViewState,labels,crypto:globalThis.crypto,AbortController,URLSearchParams,Blob,
  document:{getElementById:node,createElement:()=>new Node(),querySelectorAll:()=>[],documentElement:{}},
  window:{setInterval:()=>1,clearInterval(){},setTimeout:f=>{timers.push(f);return timers.length;},clearTimeout(){},addEventListener(){}},
  location:{origin,search:''},history:{replaceState(){},pushState(){}},confirm:message=>{confirms.push(message);return consent;},
  navigator:{clipboard:{writeText:async text=>{if(clipboardFails)throw new Error('synthetic_clipboard_denied');copies.push(text);}}},
  URL:{createObjectURL:blob=>{downloads.push(blob);return 'blob:synthetic';},revokeObjectURL(){}},
  fetch:async(url,options={})=>{const path=url.replace('/api/v1/',''),method=options.method||'GET';calls.push({path,method,options});const handler=handlers.get(method+' '+path);if(handler)return handler(options);return {ok:true,json:async()=>path==='setup/status'?{setup_required:true}:[]};}});
 const run=code=>vm.runInContext(code,context);run(source);await tick();
 const room={id:'room',topic:'Synthetic private room',state:'preparing',mode:'co_creation',generation:1,room_revision:1,
  limits:{rounds:1},effective_permissions:['room:manage_members'],members:{[agent.id]:{kind:'agent',status:'pending',revision:1,permissions}},messages:[],tasks:[],artifacts:[]};
 run(`user={id:'human',scopes:['agent:manage',${manage?"'credential:manage'":''}]};agents=[${JSON.stringify(agent)}];view.language='${language}';view.select('room');view.room=${JSON.stringify(room)};`);
 run("refresh=async()=>{view.room=await api('rooms/room');renderParticipants(view.room);};");
 const respond=(method,path,data)=>handlers.set(method+' '+path,async()=>({ok:true,json:async()=>data}));
 respond('GET','rooms/room',room);respond('GET','agents/'+agent.id+'/credentials',[]);
 const open=async()=>{run('showOnboarding(view.room,agents[0]);');await tick();};
 return {node,run,room,calls,handlers,copies,downloads,timers,confirms,respond,open,failClipboard:()=>{clipboardFails=true;}};
}

for(const language of ['ko','en'])test('one room screen continues an existing invitation without writes '+language,async()=>{
 const h=await harness({language});h.run('renderParticipants(view.room);');assert.equal(h.node('invite-agent-id').value,agent.id);
 assert.equal(h.node('invite-submit').textContent,labels[language].inviteContinue);
 await h.node('invite-agent').onsubmit({preventDefault(){}});await tick();
 assert.equal(h.node('onboarding-dialog').open,true);assert.equal(h.node('invite-status').textContent,labels[language].inviteAcceptedPending);
 assert.equal(h.calls.filter(c=>c.method==='POST').length,0);assert.equal(h.node('invite-next').textContent,labels[language].inviteIssue);
});

test('inline registration invites once despite duplicate click and keeps the selected client',async()=>{
 const h=await harness();h.run('agents=[];view.room.members={};');h.node('invite-agent-id').value='__new';h.node('invite-new-name').value='New synthetic';h.node('invite-new-client').value='claude';
 let resolve;h.handlers.set('POST agents',()=>new Promise(done=>{resolve=done;}));
 h.handlers.set('POST rooms/room/memberships',async()=>{h.room.members[agent.id]={kind:'agent',status:'pending',revision:1,permissions};return {ok:true,json:async()=>h.room.members[agent.id]};});
 const first=h.node('invite-agent').onsubmit({preventDefault(){}});await tick();await h.node('invite-agent').onsubmit({preventDefault(){}});
 assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path==='agents').length,1);
 resolve({ok:true,json:async()=>agent});await first;await tick();
 assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path.endsWith('/memberships')).length,1);
 assert.equal(h.node('invite-client').value,'claude');assert.match(h.node('onboarding-commands').textContent,/--runtime claude/);
 assert.ok(!h.calls.some(c=>c.method==='POST'&&c.path.endsWith('/credentials')));
 const body=JSON.parse(h.calls.find(c=>c.path==='agents').options.body);assert.match(body.handle,/^agent-[a-f0-9]{8}$/);
});

test('partial registration failure retains identity and retries only the same invitation',async()=>{
 const h=await harness();h.run('agents=[];view.room.members={};');h.node('invite-agent-id').value='__new';h.node('invite-new-name').value='New synthetic';
 h.respond('POST','agents',agent);let attempt=0;
 h.handlers.set('POST rooms/room/memberships',async()=>{if(++attempt===1)return {ok:false,status:503};h.room.members[agent.id]={kind:'agent',status:'pending',revision:1,permissions};return {ok:true,json:async()=>h.room.members[agent.id]};});
 await h.node('invite-agent').onsubmit({preventDefault(){}});assert.equal(h.node('invite-form-error').textContent,labels.ko.invitePartialFailure);
 await h.node('invite-agent').onsubmit({preventDefault(){}});await tick();
 assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path==='agents').length,1);
 const attempts=h.calls.filter(c=>c.method==='POST'&&c.path.endsWith('/memberships'));assert.equal(attempts.length,2);assert.equal(attempts[0].options.headers['Idempotency-Key'],attempts[1].options.headers['Idempotency-Key']);
});

test('navigation during registration prevents a late invitation to the old room',async()=>{
 const h=await harness();h.run('agents=[];view.room.members={};');h.node('invite-agent-id').value='__new';h.node('invite-new-name').value='New synthetic';let resolve;
 h.handlers.set('POST agents',()=>new Promise(done=>{resolve=done;}));const pending=h.node('invite-agent').onsubmit({preventDefault(){}});await tick();h.run('clearDetail();');resolve({ok:true,json:async()=>agent});await pending;
 assert.ok(!h.calls.some(c=>c.method==='POST'&&c.path.endsWith('/memberships')));assert.equal(h.node('onboarding-dialog').open,false);
});

test('keys for another or closed room, workspace, expiry, revocation and missing scopes are excluded',async()=>{
 const h=await harness();const entries=[key({room_id:'closed-room'}),key({room_id:null}),key({expires_at:1}),key({revoked:true}),key({scopes:['room:read']}),key()];
 h.respond('GET','agents/'+agent.id+'/credentials',entries);await h.open();assert.equal(h.run("validInviteKeys(onboardingContext.credentials,'room').length"),1);assert.equal(h.node('invite-next').textContent,labels.ko.invitePrepare);
 h.run("onboardingContext.room.state='closed';renderInviteWizard();");assert.equal(h.node('invite-key-state').textContent,labels.ko.inviteKeyUnavailable);await h.run('issueInviteCredential(true)');assert.ok(!h.calls.some(c=>c.method==='POST'));
});

for(const manage of [true,false])test('key issuance needs explicit confirmation and current permission '+manage,async()=>{
 const h=await harness({consent:false,manage});await h.open();await h.run('issueInviteCredential()');assert.ok(!h.calls.some(c=>c.method==='POST'));
});

test('explicit issuance binds room, minimal scopes and one hour; secret never enters ordinary output',async()=>{
 const h=await harness(),response=issued();h.respond('POST','agents/'+agent.id+'/credentials',response);await h.open();await h.node('invite-next').onclick();
 const request=h.calls.find(c=>c.method==='POST'),body=JSON.parse(request.options.body);
 assert.equal(body.room_id,'room');assert.equal(body.expires_in,3600);assert.deepEqual(body.scopes,scopes);assert.ok(!body.scopes.includes('rooms:create'));
 assert.ok(h.confirms[0].includes(h.room.topic)&&h.confirms[0].includes(agent.handle)&&h.confirms[0].includes(labels.ko.inviteOneHour));
 assert.equal(h.node('invite-secret-value').value,'synthetic-room-secret');assert.equal(h.node('invite-secret-value').type,'password');assert.equal(response.token,'');
 for(const id of ['onboarding-config','onboarding-prompt','onboarding-commands','invite-command','invite-summary'])assert.ok(!(h.node(id).value||h.node(id).textContent).includes('synthetic-room-secret'));
 await h.node('invite-next').onclick();assert.equal(h.downloads.length,1);const config=JSON.parse(await h.downloads[0].text());assert.equal(Object.hasOwn(config,'token'),false);
 assert.equal(h.copies.length,1);assert.ok(!h.copies[0].includes('synthetic-room-secret'));assert.ok(!h.copies[0].includes('cli check'));
 assert.match(h.copies[0],/^python -m talkabout\.cli connect .*--room room --acknowledge-private$/);assert.ok(!h.copies[0].includes('cli join'));assert.ok(!h.copies[0].includes('--retry-check'));
 assert.equal(h.node('invite-status').textContent,labels.ko.inviteAcceptedPending);h.timers.at(-1)();assert.equal(h.node('invite-secret-value').value,'');
});

for(const patch of [{room_id:'closed-room'},{scopes:['room:read']},{expires_at:1},{expires_at:Date.now()/1000+7200},{token_not_recoverable:true,token:undefined}])test('unconfirmed issuance is never displayed or automatically repeated '+JSON.stringify(patch),async()=>{
 const h=await harness(),response=issued(patch);h.respond('POST','agents/'+agent.id+'/credentials',response);await h.open();await h.node('invite-next').onclick();
 assert.equal(h.node('invite-secret-value').value,'');assert.equal(response.token,'');assert.equal(h.node('invite-next').textContent,labels.ko.inviteCheckKey);
 await h.node('invite-next').onclick();assert.equal(h.calls.filter(c=>c.method==='POST').length,1);
});

for(const boundary of ['clearOnboarding()','signedOut()','clearDetail()'])test('late issuance is discarded after '+boundary,async()=>{
 const h=await harness();let resolve;h.handlers.set('POST agents/'+agent.id+'/credentials',()=>new Promise(done=>{resolve=done;}));await h.open();const pending=h.run('issueInviteCredential()');await tick();h.run(boundary);const response=issued();resolve({ok:true,json:async()=>response});await pending;
 assert.equal(response.token,'');assert.equal(h.node('invite-secret-value').value,'');
});

test('pause or membership revision change during issuance prevents secret display',async()=>{
 for(const change of ["onboardingContext.room.state='paused'","onboardingContext.room.members[agents[0].id].revision++"]){
  const h=await harness();let resolve;h.handlers.set('POST agents/'+agent.id+'/credentials',()=>new Promise(done=>{resolve=done;}));await h.open();const pending=h.run('issueInviteCredential()');await tick();h.run(change);const response=issued();resolve({ok:true,json:async()=>response});await pending;assert.equal(h.node('invite-secret-value').value,'');assert.equal(response.token,'');
 }
});

test('issuance network failure and duplicate click do not create another attempt',async()=>{
 const h=await harness();let reject;h.handlers.set('POST agents/'+agent.id+'/credentials',()=>new Promise((_,fail)=>{reject=fail;}));await h.open();const pending=h.run('issueInviteCredential()');await tick();await h.run('issueInviteCredential()');reject(new Error('synthetic_network_failure'));await pending;
 assert.equal(h.calls.filter(c=>c.method==='POST').length,1);assert.equal(h.node('invite-next').textContent,labels.ko.inviteCheckKey);assert.equal(h.node('invite-secret-value').value,'');
});

test('closing and reopening resumes metadata without issuing or recovering a secret',async()=>{
 const h=await harness(),response=issued();h.respond('POST','agents/'+agent.id+'/credentials',response);await h.open();await h.run('issueInviteCredential()');h.run('clearOnboarding()');
 h.respond('GET','agents/'+agent.id+'/credentials',[key()]);await h.open();assert.equal(h.node('invite-next').textContent,labels.ko.invitePrepare);assert.equal(h.node('invite-secret-value').value,'');assert.equal(h.calls.filter(c=>c.method==='POST').length,1);
});

test('clipboard failure offers manual commands without claiming connection',async()=>{
 const h=await harness();h.respond('GET','agents/'+agent.id+'/credentials',[key()]);await h.open();h.failClipboard();await h.node('invite-next').onclick();
 assert.equal(h.downloads.length,1);assert.equal(h.node('invite-error').textContent,labels.ko.inviteClipboardFailure);assert.equal(h.node('invite-command-preview').open,true);assert.equal(h.node('invite-status').textContent,labels.ko.inviteAcceptedPending);
});

test('secret clipboard failure is separate from ordinary command copying',async()=>{
 const h=await harness();h.respond('POST','agents/'+agent.id+'/credentials',issued());await h.open();await h.run('issueInviteCredential()');h.failClipboard();await h.node('invite-secret-copy').onclick();assert.equal(h.node('invite-error').textContent,labels.ko.inviteSecretCopyFailure);assert.equal(h.node('invite-secret-value').type,'password');
});

for(const runtime of ['grok'])test('unsupported client cannot create keys or generate a fake launch '+runtime,async()=>{
 const h=await harness();await h.open();h.node('invite-client').value=runtime;h.node('invite-client').onchange();await h.run('issueInviteCredential(true)');await h.run('prepareInviteSetup()');assert.equal(h.node('invite-next').disabled,true);assert.equal(h.downloads.length,0);assert.ok(!h.calls.some(c=>c.method==='POST'));
});

test('localhost use from another computer blocks setup and issuance',async()=>{
 const h=await harness();await h.open();h.node('invite-machine').value='other';h.node('invite-machine').onchange();await h.run('issueInviteCredential(true)');await h.run('prepareInviteSetup()');assert.equal(h.node('invite-next').disabled,true);assert.equal(h.node('invite-host-warning').textContent,labels.ko.inviteOtherBlocked);assert.equal(h.downloads.length,0);assert.ok(!h.calls.some(c=>c.method==='POST'));
});

test('acceptance or heartbeat alone is incomplete; fresh matching native test evidence is required',async()=>{
 const h=await harness();h.respond('GET','agents/'+agent.id+'/credentials',[key()]);await h.open();h.run("onboardingContext.room.members[agents[0].id].status='active';renderInviteWizard();");assert.equal(h.node('invite-status').textContent,labels.ko.inviteAwaitConnection);
 const now=Date.now()/1000,c={runtime:'codex',state:'idle',room_id:'room',last_seen_at:now},r={runtime:'codex',status:'passed',verification:'worker_reported_test',expires_at:now+300};
 h.run(`onboardingContext.room.members[agents[0].id].connection=${JSON.stringify(c)};renderInviteWizard();`);assert.notEqual(h.node('invite-status').textContent,labels.ko.inviteConnected);
 for(const patch of [{runtime:'claude'},{status:'pending'},{expires_at:1},{verification:'provider_verified'}]){h.run(`onboardingContext.room.members[agents[0].id].runtime_check=${JSON.stringify({...r,...patch})};renderInviteWizard();`);assert.notEqual(h.node('invite-status').textContent,labels.ko.inviteConnected);}
 h.run(`onboardingContext.room.members[agents[0].id].runtime_check=${JSON.stringify(r)};renderInviteWizard();`);assert.equal(h.node('invite-status').textContent,labels.ko.inviteConnected);assert.equal(h.node('invite-next').textContent,labels.ko.inviteDone);
 h.run("onboardingContext.room.members[agents[0].id].connection.room_id='other-room';renderInviteWizard();");assert.notEqual(h.node('invite-status').textContent,labels.ko.inviteConnected);
});

test('accepted participant needs one connect command rather than another acceptance',async()=>{
 const h=await harness();h.room.members[agent.id].status='active';h.respond('GET','rooms/room',h.room);h.run("view.room.members[agents[0].id].status='active';");h.respond('GET','agents/'+agent.id+'/credentials',[key()]);await h.open();assert.match(h.node('invite-command').textContent,/^python .*cli connect/);assert.ok(!h.node('invite-command').textContent.includes('cli join'));
});

test('pending start preview has one credential prompt; join-only remains separate',async()=>{
 for(const language of ['ko','en']){
  const h=await harness({language});await h.open();const preview=h.node('onboarding-commands').textContent;
  assert.match(preview,/cli connect .*--room room --acknowledge-private/);assert.ok(!/cli (?:join|check) /.test(preview));
  assert.equal((h.node('invite-command').textContent.match(/--prompt-token/g)||[]).length,1);
  assert.match(labels[language].inviteExternalStep,language==='en'?/room-scoped credential.*once/:/방 전용 자격증명.*한 번/);
  h.node('onboarding-action').value='join_only';h.node('onboarding-action').onchange();
  assert.match(h.node('invite-command').textContent,/^python .*cli join .*--room room --acknowledge-private --prompt-token$/);assert.ok(!h.node('invite-command').textContent.includes('cli connect'));
 }
});

test('a known other-room worker is not silently replaced when room metadata hides its connection',async()=>{
 const h=await harness();h.run("agents[0].connection={runtime:'codex',state:'idle',room_id:'other-room',last_seen_at:Date.now()/1000};");await h.open();assert.equal(h.node('onboarding-action').value,'');assert.equal(h.node('invite-command').textContent,'');assert.equal(h.node('invite-next').textContent,labels.ko.inviteRecovery);assert.ok(!h.calls.some(c=>c.method==='POST'));
});

test('a queued close event cannot erase a newly reopened invitation',async()=>{
 const h=await harness();await h.open();h.run('clearOnboarding();');await h.open();
 h.node('onboarding-dialog').listeners.close();
 assert.equal(h.node('onboarding-dialog').open,true);assert.equal(h.run('onboardingContext.agent.id'),agent.id);
 h.node('onboarding-dialog').open=false;h.node('onboarding-dialog').listeners.close();
 assert.equal(h.run('onboardingContext'),null);assert.equal(h.node('invite-secret-value').value,'');
});

test('guarded preview hides commands for blocked runtime, host and room states',async()=>{
 const h=await harness();await h.open();
 for(const change of ["el('invite-client').value='grok'", "el('invite-machine').value='other'", "onboardingContext.room.state='paused'", "onboardingContext.room.state='closed'", "onboardingContext.room.dispatch_held=true"]){
  h.run("el('invite-client').value='codex';el('invite-machine').value='same';onboardingContext.room.state='preparing';onboardingContext.room.dispatch_held=false;el('onboarding-action').value='start';"+change);
  assert.equal(h.run('basicInviteCommands(onboardingContext)'),'',change);
 }
});

test('room invitation exposes prerequisites and steps while blocking unsupported choices',()=>{
 const dialog=html.slice(html.indexOf('<dialog id="onboarding-dialog"'),html.indexOf('<dialog id="credential-dialog"'));
 const advanced=dialog.indexOf('id="invite-advanced"');
 for(const key of ['clientSetupHelp','runtimePlatformHelp','inviteSimpleSteps'])assert.ok(dialog.indexOf(`data-i18n="${key}"`)<advanced,key);
 assert.ok(dialog.indexOf('id="onboarding-commands"')>advanced);
 assert.ok(dialog.indexOf('id="onboarding-action"')<advanced);
 assert.match(dialog,/<details id="invite-command-preview">/);
 assert.match(dialog,/<option value="grok" disabled/);
 assert.match(dialog,/<optgroup label="OpenAI-compatible API" disabled>/);
 for(const language of ['ko','en'])for(const key of ['inviteRuntimeAvailability','inviteSimpleSteps','inviteManualSteps'])assert.ok(labels[language][key]);
});

test('all new HTML localization keys exist; advanced prompt and JSON are collapsed',()=>{
 const dialog=html.slice(html.indexOf('<dialog id="onboarding-dialog"'),html.indexOf('<dialog id="credential-dialog"'));
 for(const [,name] of dialog.matchAll(/data-i18n="([^"]+)"/g))for(const language of ['ko','en'])assert.equal(typeof labels[language][name],'string',name);
 assert.ok(dialog.indexOf('id="invite-advanced"')<dialog.indexOf('id="onboarding-prompt"'));assert.match(dialog,/<details id="invite-advanced">/);assert.match(dialog,/id="invite-secret-value" type="password"/);assert.match(dialog,/id="invite-error" role="alert"/);
});

test('connection helper downloads one launcher and copies the same invocation without a secret',async()=>{
 const h=await harness();await h.open();
 assert.equal(h.node('invite-helper').hidden,false);
 h.node('invite-helper-download').onclick();
 assert.equal(h.downloads.length,1);
 const batch=await h.downloads[0].text();
 assert.ok(batch.startsWith('@echo off\r\n'));
 assert.ok(batch.includes('\r\n'));
 assert.match(batch,/-Server 'http:\/\/127\.0\.0\.1:19999'/);
 assert.match(batch,/-Room 'room'/);
 assert.match(batch,/-Agent 'synthetic-agent'/);
 assert.match(batch,/-Runtime 'codex'/);
 assert.match(batch,/-AuthMode 'native'/);
 assert.match(batch,/-MaxModelCalls 4/);
 assert.ok(batch.includes('/web/connect-agent.ps1'));
 assert.ok(batch.includes('powershell -NoProfile -ExecutionPolicy Bypass -Command "'));
 assert.ok(!batch.includes('--prompt-token'));
 assert.ok(!batch.includes('synthetic-room-secret'));
 await h.node('invite-helper-copy').onclick();
 assert.equal(h.copies.length,1);
 assert.ok(h.copies[0].startsWith('& ([scriptblock]::Create((Invoke-RestMethod -UseBasicParsing'));
 assert.ok(h.copies[0].includes("-Room 'room'"));
 assert.ok(!h.copies[0].includes('synthetic-room-secret'));
 assert.equal(h.calls.filter(c=>c.method==='POST').length,0);
});

test('connection helper stays hidden for unsupported runtime, remote host, closed state and free mode without consent',async()=>{
 const h=await harness();await h.open();
 for(const [change,expected] of [
  ["el('invite-client').value='grok'",true],
  ["el('invite-client').value='codex';el('invite-machine').value='other'",true],
  ["el('invite-machine').value='same';onboardingContext.room.state='paused'",true],
  ["onboardingContext.room.state='preparing';onboardingContext.room.mode='free_discussion';el('onboarding-turn-consent').checked=false",true],
  ["onboardingContext.room.mode='free_discussion';el('onboarding-turn-consent').checked=true",false],
 ]){
  h.run("el('invite-client').value='codex';el('invite-machine').value='same';onboardingContext.room.state='preparing';onboardingContext.room.mode='co_creation';el('onboarding-turn-consent').checked=false;"+change+";renderInviteWizard();");
  assert.equal(h.node('invite-helper').hidden,expected,change);
 }
 h.run("el('invite-client').value='codex';el('invite-machine').value='same';onboardingContext.room.state='preparing';onboardingContext.room.mode='free_discussion';el('onboarding-turn-consent').checked=true;renderInviteWizard();");
 h.node('invite-helper-download').onclick();
 const batch=await h.downloads[h.downloads.length-1].text();
 assert.ok(batch.includes('-RequestTurn'));
 assert.match(batch,/-MaxModelCalls 3/);
 const noManage=await harness({manage:false});await noManage.open();
 assert.equal(noManage.node('invite-helper').hidden,true);
});
