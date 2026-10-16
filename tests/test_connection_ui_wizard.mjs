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
const tick=()=>new Promise(setImmediate);
for(const language of ['ko','en'])test('connection wizard gives secret-free actionable instructions in '+language,async()=>{
 const h=await harness();h.run(`view.language='${language}';showConnection(agents[0]);`);
 assert.equal(h.node('connection-wizard').hidden,false);assert.match(h.node('connection-identity').textContent,/@research-bot/);
 const config=JSON.parse(h.node('connection-config').value);assert.deepEqual(config,{base_url:'https://synthetic.invalid',allowed_origin:'https://synthetic.invalid',journal_path:'agent-synthetic-agent.sqlite3',token_env:'TALKABOUT_TOKEN'});
 assert.equal(Object.hasOwn(config,'token'),false);
 for(const runtime of ['codex','claude','grok']){h.node('connection-runtime').value=runtime;h.node('connection-runtime').onchange();if(runtime==='grok'){assert.equal(h.node('connection-command').textContent,'');h.node('connection-auth-mode').value='api_key';h.node('connection-auth-mode').onchange();h.node('connection-api-consent').checked=true;h.node('connection-api-consent').onchange();}const command=h.node('connection-command').textContent;assert.match(command,new RegExp('--runtime '+runtime+' '));assert.match(command,/connect --config agent.local.json/);for(const flag of ['--approve-external','--max-model-calls 3','--steps 1000','--max-seconds 300','--prompt-token'])assert.ok(command.includes(flag));assert.ok(!command.includes('--room'));assert.ok(!command.includes('--acknowledge-private'));}
 await h.node('connection-copy').onclick();h.node('connection-download').onclick();assert.equal(h.copied[0],h.node('connection-config').value);assert.equal(await h.downloads[0].text(),h.node('connection-config').value+'\n');
 assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);assert.ok(labels[language].connectionLimits.includes('300'));assert.ok(labels[language].runtimePlatformHelp.includes('Windows'));assert.ok(labels[language].receiptHelp);
});
test('registering an identity opens connection setup without issuing any credential',async()=>{
 const h=await harness();h.respond('agents',agent);h.node('agent-name').value=agent.display_name;h.node('agent-handle').value=agent.handle;h.node('register-agent').onsubmit({preventDefault(){}});await tick();
 assert.equal(h.node('connection-wizard').hidden,false);assert.equal(h.calls.filter(c=>c.options.method==='POST').length,1);assert.equal(h.calls.at(-1).path,'agents');
});
for(const handle of ['@research-bot','ResearchBot','research_bot','1bot','admin','system','human','administrator',''])test('handle validation explains invalid input '+handle,async()=>{
 const h=await harness();h.node('agent-name').value='Synthetic';h.node('agent-handle').value=handle;h.node('register-agent').onsubmit({preventDefault(){}});await tick();assert.equal(h.node('notice').textContent,labels.ko.agentHandleInvalid);assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
});
for(const config of [{consent:false},{scopes:['agent:manage']}])test('workspace token cannot be issued without consent and scope '+JSON.stringify(config),async()=>{
 const h=await harness(config);h.run('showConnection(agents[0]);issueConnectionCredential();');await tick();assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
});
test('explicit workspace issuance uses short-lived scopes and separate secret display',async()=>{
 const h=await harness(),response={token:'synthetic-secret',expires_at:2000000000};h.respond('agents/synthetic-agent/credentials',response);h.run('showConnection(agents[0]);issueConnectionCredential();');await tick();
 const request=h.calls.find(c=>c.options.method==='POST'),body=JSON.parse(request.options.body);assert.equal(body.expires_in,3600);assert.equal(Object.hasOwn(body,'room_id'),false);assert.ok(body.scopes.includes('agent:capabilities_write'));assert.ok(!body.scopes.includes('rooms:create'));assert.equal(h.node('credential-value').value,'synthetic-secret');assert.equal(response.token,'');assert.ok(!h.node('connection-config').value.includes('synthetic-secret'));assert.ok(!h.node('connection-command').textContent.includes('synthetic-secret'));
 h.timeouts[0]();assert.equal(h.node('credential-value').value,'');
});
for(const boundary of ['clearDetail()','signedOut()','showConnection({id:"other",display_name:"Other",handle:"other"})','clearCredential();clearConnection()'])test('late workspace token is discarded after '+boundary,async()=>{
 const h=await harness();let resolve;h.handlers.set('agents/synthetic-agent/credentials',()=>new Promise(done=>{resolve=done;}));h.run('showConnection(agents[0]);issueConnectionCredential();');await tick();h.run(boundary);const response={token:'synthetic-secret',expires_at:2000000000};resolve({ok:true,json:async()=>response});await tick();assert.equal(h.node('credential-value').value,'');assert.equal(response.token,'');
});
test('runtime readiness cannot come from heartbeat alone or invalid test evidence',async()=>{
 const h=await harness();h.run("agents[0].connection={runtime:'codex',state:'idle',last_seen_at:Date.now()/1000};");assert.equal(h.run('runtimeReady(agents[0])'),false);
 const now=Date.now()/1000;const good={runtime:'codex',status:'passed',verification:'worker_reported_test',last_checked_at:now,expires_at:now+300};
 for(const patch of [{status:'pending'},{status:'failed'},{status:'invalidated'},{expires_at:now-1},{runtime:'claude'},{verification:'provider_verified'}]){h.run(`agents[0].runtime_check=${JSON.stringify({...good,...patch})};`);assert.equal(h.run('runtimeReady(agents[0])'),false);}
 h.run(`agents[0].runtime_check=${JSON.stringify(good)};`);assert.equal(h.run('runtimeReady(agents[0])'),true);h.run("agents[0].connection.last_seen_at-=76;");assert.equal(h.run('runtimeReady(agents[0])'),false);
});
test('receipt refresh only reads server evidence and never posts fake completion',async()=>{
 const h=await harness();h.respond('agents',[{...agent,runtime_check:{runtime:'grok',status:'failed',verification:'worker_reported_test',last_checked_at:Date.now()/1000,expires_at:Date.now()/1000+60,error_code:'runtime_failed'}}]);h.run('showConnection(agents[0]);');await h.node('connection-refresh').onclick();assert.ok(h.node('connection-evidence').textContent.includes(labels.ko.testFailed));assert.ok(h.node('connection-evidence').textContent.includes('runtime_failed'));assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
});
test('every wizard localization key exists in both languages and handle help is connected',()=>{
 const wizard=html.slice(html.indexOf('id="connection-wizard"'),html.indexOf('id="agents-list"'));for(const [,key] of wizard.matchAll(/data-i18n="([^"]+)"/g))for(const language of ['ko','en'])assert.equal(typeof labels[language][key],'string',language+': '+key);assert.match(html,/id="agent-handle" aria-describedby="agent-handle-help"/);assert.match(html,/id="connection-evidence" aria-live="polite"/);
});
test('room start requires every accepted eligible agent; pending invitation is excluded',async()=>{
 const h=await harness();h.run("room={effective_permissions:['room:control','room:approve'],members:{a:{kind:'agent',status:'active',permissions:[...participantPermissions],connection:{runtime:'codex',state:'idle',last_seen_at:Date.now()/1000},runtime_check:{runtime:'codex',status:'passed',verification:'worker_reported_test',expires_at:Date.now()/1000+300}}}};");assert.equal(h.run('allParticipantsReady(room)'),true);
 h.run("room.members.b={kind:'agent',status:'pending',permissions:[...participantPermissions]};");assert.equal(h.run('allParticipantsReady(room)'),true);
 h.run("room.members.b.status='active';");assert.equal(h.run('allParticipantsReady(room)'),false);assert.equal(h.run('startReadiness(room)'),'startNeedWorker');
 h.run("room.members.b={...room.members.a};");assert.equal(h.run('allParticipantsReady(room)'),true);
 h.run("room.members.b={...room.members.b,runtime_check:{...room.members.b.runtime_check,status:'invalidated'}};");assert.equal(h.run('allParticipantsReady(room)'),false);
 h.run("room.members.b.permissions=['room:read'];");assert.equal(h.run('allParticipantsReady(room)'),true);
});
for(const language of ['ko','en'])test('Grok native is blocked; API mode requires explicit billing acknowledgment in '+language,async()=>{
 const h=await harness();h.run(`view.language='${language}';showConnection(agents[0]);`);h.node('connection-runtime').value='grok';h.node('connection-runtime').onchange();assert.equal(h.node('connection-auth-mode').value,'native');assert.equal(h.node('connection-command').textContent,'');assert.equal(h.node('connection-issue').disabled,true);assert.equal(h.node('connection-auth-warning').textContent,labels[language].grokNativeUnsupported);h.run('issueConnectionCredential();');await tick();assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
 h.node('connection-auth-mode').value='api_key';h.node('connection-auth-mode').onchange();assert.equal(h.node('connection-command').textContent,'');assert.equal(h.node('connection-auth-warning').textContent,labels[language].apiConsentRequired);
 h.node('connection-api-consent').checked=true;h.node('connection-api-consent').onchange();assert.match(h.node('connection-command').textContent,/--runtime grok --auth-mode api_key/);assert.equal(h.node('connection-issue').disabled,false);
 h.node('connection-api-consent').checked=false;h.node('connection-api-consent').onchange();assert.equal(h.node('connection-command').textContent,'');
});
test('native command is explicit and API mode never automatically follows a runtime change',async()=>{
 const h=await harness();h.run('showConnection(agents[0]);');assert.match(h.node('connection-command').textContent,/--auth-mode native/);h.node('connection-auth-mode').value='api_key';h.node('connection-auth-mode').onchange();assert.equal(h.node('connection-command').textContent,'');h.node('connection-api-consent').checked=true;h.node('connection-api-consent').onchange();assert.match(h.node('connection-command').textContent,/--auth-mode api_key/);
 h.node('connection-runtime').value='claude';h.node('connection-runtime').onchange();assert.equal(h.node('connection-auth-mode').value,'native');assert.equal(h.node('connection-api-consent').checked,false);assert.match(h.node('connection-command').textContent,/--runtime claude --auth-mode native/);
});
test('Grok room guide never supplies an unsupported native run command or silent API fallback',async()=>{
 const h=await harness();h.run("agents[0].connection={runtime:'grok'};showOnboarding({id:'room',topic:'Synthetic',mode:'free_discussion'},agents[0]);");assert.ok(!h.node('onboarding-commands').textContent.includes('cli run'));assert.ok(h.node('onboarding-commands').textContent.includes(labels.ko.grokRoomUnsupported));
 h.run('showConnection(agents[0]);');h.node('connection-auth-mode').value='api_key';h.node('connection-auth-mode').onchange();h.node('connection-api-consent').checked=true;h.node('connection-api-consent').onchange();h.run("showOnboarding({id:'room',topic:'Synthetic',mode:'free_discussion'},agents[0]);");assert.match(h.node('onboarding-commands').textContent,/cli run.*--runtime grok --auth-mode api_key/);
 h.run('signedOut();');assert.equal(h.run('connectionAuthChoices.size'),0);
});
for(const language of ['ko','en'])test('failed or uncertain test guidance requires explicit paid retry in '+language,()=>{
 assert.ok(labels[language].runtimeFailureHelp.includes('--retry-check'));
 assert.ok(!labels[language].runtimeFailureHelp.includes(language==='ko'?'같은 명령으로 다시 테스트':'then test again with the same command'));
});
test('older registry GET cannot hide the wizard opened by a completed registration',async()=>{
 const h=await harness();let resolveRead;const registered={id:'new-agent',display_name:'New Agent',handle:'new-agent'};
 h.handlers.set('agents',options=>options.method==='POST'?Promise.resolve({ok:true,json:async()=>registered}):new Promise(resolve=>{resolveRead=resolve;}));
 const pendingRead=h.run('refreshAgents()');await tick();h.node('agent-name').value=registered.display_name;h.node('agent-handle').value=registered.handle;h.node('register-agent').onsubmit({preventDefault(){}});await tick();assert.equal(h.run('connectionAgentId'),'new-agent');assert.equal(h.node('connection-wizard').hidden,false);
 resolveRead({ok:true,json:async()=>[agent]});await pendingRead;
 assert.equal(h.run('connectionAgentId'),'new-agent');assert.equal(h.node('connection-wizard').hidden,false);assert.match(h.node('connection-identity').textContent,/@new-agent/);assert.equal(h.run("agents.some(a=>a.id==='new-agent')"),true);
});
for(const language of ['ko','en'])test('resume-worker is explained as optional, never added to the default command '+language,async()=>{
 const h=await harness();h.run(`view.language='${language}';showConnection(agents[0]);`);assert.ok(labels[language].resumeWorkerHelp.includes('--resume-worker'));assert.ok(!h.node('connection-command').textContent.includes('--resume-worker'));
});
