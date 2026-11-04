// Synthetic DOM/fetch only. No provider, operating account, credential or model call.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {ViewState,labels} from '../talkabout/web/state.mjs';
import {createCollaborationBoard} from '../talkabout/web/collaboration-board.mjs';
const source=readFileSync(new URL('../talkabout/web/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
const html=readFileSync(new URL('../talkabout/web/index.html',import.meta.url),'utf8');
const tick=()=>new Promise(setImmediate);
const localInput=offset=>{const date=new Date(Date.now()+offset),pad=value=>String(value).padStart(2,'0');return date.getFullYear()+'-'+pad(date.getMonth()+1)+'-'+pad(date.getDate())+'T'+pad(date.getHours())+':'+pad(date.getMinutes());};
const deferred=()=>{let resolve;const promise=new Promise(settle=>{resolve=settle;});return {promise,resolve};};
const ok=data=>({ok:true,status:200,json:async()=>data});
const failure=status=>({ok:false,status,json:async()=>({code:'synthetic'})});

class Node{
 constructor(id){this.id=id;this.value='';this.children=[];this.text='';this.type='password';this.hidden=false;this.open=false;this.disabled=false;this.checked=false;this.listeners={};this.className='';}
 set textContent(v){this.text=v??'';this.children=[];}get textContent(){return this.text+this.children.map(child=>child&&typeof child==='object'&&'textContent' in child?child.textContent:String(child)).join('');}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.text='';this.children=items;}get options(){return this.children;}get selectedOptions(){return this.children.filter(child=>child.selected===true);}
 close(){this.open=false;}showModal(){this.open=true;}addEventListener(name,fn){this.listeners[name]=fn;}setAttribute(){}scrollIntoView(){}querySelector(){return new Node('nested');}click(){this.onclick?.();}
}

async function harness({language='ko',manage=true,credential=true,consent=true}={}){
 const nodes=new Map(),calls=[],handlers=new Map(),confirms=[];
 const node=id=>{if(!nodes.has(id))nodes.set(id,new Node(id));return nodes.get(id);};
 const context=vm.createContext({ViewState,labels,createCollaborationBoard,crypto:globalThis.crypto,AbortController,URLSearchParams,Blob,Date,
  document:{getElementById:node,createElement:()=>new Node('created'),createElementNS:()=>new Node('svg'),querySelectorAll:()=>[],addEventListener(){},documentElement:{},hidden:false},
  window:{setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},addEventListener(){},matchMedia:undefined},
  location:{origin:'http://127.0.0.1:19999',search:''},history:{replaceState(){},pushState(){}},confirm:message=>{confirms.push(message);return consent;},
  navigator:{clipboard:{writeText:async()=>{}}},EventSource:function(){this.addEventListener=()=>{};this.close=()=>{};},
  fetch:async(url,options={})=>{const path=url.replace('/api/v1/',''),method=options.method||'GET';calls.push({path,method,options,body:options.body?JSON.parse(options.body):null});const handler=handlers.get(method+' '+path);if(handler)return handler(options);if(path==='capabilities')return ok({text_attachments:{enabled:false},agent_invitations:{enabled:false}});return ok([]);}});
 const run=code=>vm.runInContext(code,context);
 run(source);await tick();
 const inviter={id:'inviter-bot',display_name:'Inviter bot',handle:'inviter-bot'},reader={id:'reader-bot',display_name:'Reader bot',handle:'reader-bot'},pending={id:'pending-bot',display_name:'Pending bot',handle:'pending-bot'},targetA={id:'target-a',display_name:'Target A',handle:'target-a'},targetB={id:'target-b',display_name:'Target B',handle:'target-b'};
 const room={id:'room',topic:'Synthetic delegated room',state:'preparing',mode:'bounded_rounds',generation:4,room_revision:7,
  effective_permissions:['room:read','room:manage_members','credential:manage'],limits:{rounds:1},messages:[],tasks:[],artifacts:[],
  members:{[inviter.id]:{kind:'agent',status:'active',revision:3,epoch:2,permissions:['room:read','room:speak','task:claim','task:heartbeat','task:complete','task:skip','room:invite_agents']},
           [reader.id]:{kind:'agent',status:'active',revision:2,epoch:1,permissions:['room:read']},
           [pending.id]:{kind:'agent',status:'pending',revision:1,epoch:0,permissions:['room:read']},
           human:{kind:'human',status:'active',revision:1,epoch:0,permissions:['room:read','room:manage_members']}}};
 run(`user={id:'human',scopes:['agent:manage',${manage?"'room:manage_members',":''}${credential?"'credential:manage'":''}]};agents=${JSON.stringify([inviter,reader,pending,targetA,targetB])};view.language='${language}';view.select('room');view.room=${JSON.stringify(room)};refresh=async()=>{};`);
 const respond=(method,path,data,status=200)=>handlers.set(method+' '+path,async()=>status<400?ok(data):failure(status));
 const capability=async enabled=>{run('agentInviteEnabled='+enabled+';');};
 const openPanel=()=>{run('renderParticipants(view.room);');};
 const fillGrant=({targets=['target-a'],uses='1',ack=true}={})=>run(`el('agent-invite-inviter').value='inviter-bot';el('agent-invite-expiry').value='${localInput(20*60*1000)}';el('agent-invite-uses').value='${uses}';el('agent-invite-topic-ack').checked=${ack};${targets.map(value=>`agentInviteTargetInputs.find(input=>input.value==='${value}').checked=true;`).join('')}`);
 const switchRoom=async(roomData)=>{run('clearDetail();');run(`view.select('${roomData.id}');view.room=${JSON.stringify(roomData)};`);run('renderParticipants(view.room);');await tick();};
 return {node,run,room,agents:[inviter,reader,pending,targetA,targetB],calls,handlers,confirms,respond,capability,openPanel,fillGrant,switchRoom};
}

for(const language of ['ko','en'])test('delegated invite panel is hidden while the capability is off '+language,async()=>{
 const h=await harness({language});h.openPanel();await tick();
 assert.equal(h.node('agent-invite-panel').hidden,true);
 assert.equal(h.calls.filter(call=>call.path==='rooms/room/agent-invite-policies').length,0,'capability off never calls the policy endpoint');
});

test('policy form sends the exact grant body after all required confirmations',async()=>{
 const h=await harness();h.capability(true);h.respond('GET','rooms/room/agent-invite-policies',[{inviter_id:'inviter-bot',revision:2,active:true,target_ids:['target-a'],expires_at:Math.floor(Date.now()/1000)+600,max_uses:1,used:0}]);
 h.respond('POST','rooms/room/agent-invite-policy',{ok:true});
 h.openPanel();await tick();
 assert.equal(h.node('agent-invite-panel').hidden,false);
 h.fillGrant();await h.run('submitAgentInvitePolicy({preventDefault(){}})');await tick();
 const request=h.calls.find(call=>call.method==='POST'&&call.path==='rooms/room/agent-invite-policy');
 assert.ok(request,'grant request is sent');
 assert.deepEqual(request.body,{action:'grant',inviter_id:'inviter-bot',target_ids:['target-a'],inviter_epoch:2,inviter_revision:3,expected_generation:4,expected_policy_revision:2,expires_at:request.body.expires_at,max_uses:1,acknowledge_topic_sharing:true});
 const now=Math.floor(Date.now()/1000);
 assert.ok(request.body.expires_at>now&&request.body.expires_at<=now+3600,'expiry is a Unix integer within one hour');
 assert.ok(request.options.headers['Idempotency-Key'],'idempotency key is sent');
});

test('grant validation blocks a missing topic acknowledgement, too many targets and an expiry over one hour',async()=>{
 const h=await harness();h.capability(true);h.openPanel();await tick();
 h.run(`el('agent-invite-inviter').value='inviter-bot';el('agent-invite-uses').value='1';`);
 h.run(`el('agent-invite-expiry').value='${localInput(20*60*1000)}';`);
 await h.run('submitAgentInvitePolicy({preventDefault(){}})');await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST').length,0,'topic acknowledgement is required');
 h.run(`el('agent-invite-topic-ack').checked=true;agentInviteTargetInputs.find(input=>input.value==='target-a').checked=true;agentInviteTargetInputs.find(input=>input.value==='target-b').checked=true;el('agent-invite-uses').value='3';`);
 await h.run('submitAgentInvitePolicy({preventDefault(){}})');await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST').length,0,'invite count cannot exceed targets');
 h.run(`el('agent-invite-uses').value='2';el('agent-invite-expiry').value='${localInput(2*3600*1000)}';`);
 await h.run('submitAgentInvitePolicy({preventDefault(){}})');await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST').length,0,'expiry beyond one hour is rejected');
 assert.ok(h.node('agent-invite-error').textContent.length>0);
});

test('409 on grant clears the policy cache, refetches once and never retries the mutation',async()=>{
 let gets=0;
 const h=await harness();h.capability(true);
 h.handlers.set('GET rooms/room/agent-invite-policies',async()=>ok([{inviter_id:'inviter-bot',revision:gets++?3:2,active:true,target_ids:['target-a'],expires_at:Math.floor(Date.now()/1000)+600,max_uses:1,used:0}]));
 h.respond('POST','rooms/room/agent-invite-policy',{},409);h.openPanel();await tick();
 h.fillGrant();await h.run('submitAgentInvitePolicy({preventDefault(){}})');await tick();await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST').length,1,'stale request is not retried');
 assert.equal(h.node('agent-invite-error').textContent,labels.ko.agentInviteStale);
 assert.ok(gets>=2,'stale cache is refreshed');
 assert.ok(h.node('agent-invite-policies').textContent.includes(labels.ko.agentInviteRevision+' 3'),'fresh server revision is displayed');
});

test('policy list renders only server fields and revoke sends the exact body',async()=>{
 const h=await harness();h.capability(true);h.respond('GET','rooms/room/agent-invite-policies',[{inviter_id:'inviter-bot',revision:2,active:true,target_ids:['target-a','target-b'],expires_at:Math.floor(Date.now()/1000)+900,max_uses:2,used:1},{inviter_id:'reader-bot',revision:0,active:false,target_ids:[],expires_at:Math.floor(Date.now()/1000)+900,max_uses:1}]);
 h.respond('POST','rooms/room/agent-invite-policy',{ok:true});h.openPanel();await tick();await tick();
 const list=h.node('agent-invite-policies').textContent;
 assert.ok(list.includes(labels.ko.agentInviteRevision+' 2'));
 assert.ok(list.includes(labels.ko.agentInviteUsesCount+' 1/2'));
 assert.ok(list.includes(labels.ko.agentInviteMaxUses+' 1'),'missing used shows the server max_uses only');
 assert.equal(list.includes(labels.ko.agentInviteUsesCount+' 0/'),false,'missing used is never guessed as zero');
 assert.ok(list.includes(labels.ko.agentInviteInactive));
 assert.equal(list.includes('undefined'),false);
 assert.ok(labels.ko.agentInviteRevokeConfirm.includes('유지'),'revoke text says accepted members stay');
 h.run('revokeAgentInvitePolicy(view.room,agentInvitePolicies[0])');await tick();await tick();
 const request=h.calls.find(call=>call.method==='POST'&&call.path==='rooms/room/agent-invite-policy');
 assert.deepEqual(request.body,{action:'revoke',inviter_id:'inviter-bot',expected_generation:4,expected_policy_revision:2});
});

test('policy list shows approved targets, keeps server active separate from client-expired, and only reports known mismatches',async()=>{
 const h=await harness();h.capability(true);const past=Math.floor(Date.now()/1000)-60;
 h.respond('GET','rooms/room/agent-invite-policies',[{inviter_id:'inviter-bot',revision:2,active:true,target_ids:['target-a','<img src=x onerror="alert(1)">'],expires_at:past,max_uses:2,used:1,generation:3,inviter_epoch:1,inviter_revision:1}]);
 h.openPanel();await tick();await tick();
 const list=h.node('agent-invite-policies').textContent;
 assert.ok(list.includes(labels.ko.agentInviteAllowedTargets));
 assert.ok(list.includes('target-a'));
 assert.ok(list.includes('<img src=x onerror="alert(1)">'),'target id is rendered as text, never parsed as HTML');
 assert.ok(list.includes(labels.ko.agentInviteServerState));
 assert.ok(list.includes(labels.ko.agentInviteServerActive));
 assert.ok(list.includes(labels.ko.agentInviteExpiredClient),'client clock expiry is labelled separately from the server active flag');
 assert.ok(list.includes(labels.ko.agentInvitePolicyMismatch));
 assert.ok(list.includes(labels.ko.agentInviteGeneration+' 3/4'));
 assert.ok(list.includes(labels.ko.agentInviteInviterEpoch+' 1/2'));
 assert.ok(list.includes(labels.ko.agentInviteInviterRevision+' 1/3'));
 assert.equal(list.includes('undefined'),false);
});

test('inviter permission grant is a separate confirmed PATCH with the backend execution acknowledgement',async()=>{
 const h=await harness();h.capability(true);h.respond('PATCH','rooms/room/memberships/reader-bot',{ok:true});h.openPanel();await tick();
 h.run("grantInviterScope(view.room,agents[1],view.room.members['reader-bot'])");
 assert.ok(h.confirms.some(message=>message.includes(labels.ko.agentInviteScopeConfirm)),'cancellation/execution warning is confirmed');
 assert.ok(labels.ko.agentInviteScopeConfirm.includes('provider')&&labels.ko.agentInviteScopeConfirm.includes('비용'),'warning covers provider transfer and cost');
 await tick();
 const request=h.calls.find(call=>call.method==='PATCH'&&call.path==='rooms/room/memberships/reader-bot');
 assert.deepEqual(request.body,{expected_revision:2,status:'active',permissions:['room:read','room:invite_agents'],acknowledge_external_execution:true});
 assert.equal(h.calls.filter(call=>call.path.endsWith('/credentials')).length,0,'permission grant never issues a credential');
});

test('inviter and pending credentials are issued only by an explicit click with the approved scopes',async()=>{
 const h=await harness();h.capability(true);h.respond('POST','agents/inviter-bot/credentials',{token:'invite-secret',expires_at:Math.floor(Date.now()/1000)+600});h.respond('POST','agents/pending-bot/credentials',{token:'read-secret',expires_at:Math.floor(Date.now()/1000)+600});
 h.run("issueAgentInviteCredential(view.room,agents[0])");await tick();await tick();
 const invite=h.calls.find(call=>call.method==='POST'&&call.path==='agents/inviter-bot/credentials');
 assert.deepEqual(invite.body,{room_id:'room',expires_in:3600,scopes:['room:read','room:invite_agents']});
 assert.equal(h.node('credential-value').value,'invite-secret');
 assert.equal(h.node('credential-dialog').open,true);
 h.run("issuePendingReadCredential(view.room,agents[2])");await tick();await tick();
 const pending=h.calls.find(call=>call.method==='POST'&&call.path==='agents/pending-bot/credentials');
 assert.deepEqual(pending.body,{room_id:'room',expires_in:3600,scopes:['agent:self_read','room:read']});
 assert.equal(h.node('credential-value').value,'read-secret');
});

test('declining the confirmation issues nothing',async()=>{
 const h=await harness({consent:false});h.capability(true);h.openPanel();await tick();
 h.run("grantInviterScope(view.room,agents[0],view.room.members['inviter-bot'])");
 h.run("issueAgentInviteCredential(view.room,agents[0])");
 h.run("issuePendingReadCredential(view.room,agents[2])");
 await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST'||call.method==='PATCH').length,0);
});

test('read-only active participant expansion stays available with the capability off and sends the acknowledgement',async()=>{
 const h=await harness();h.capability(false);h.openPanel();await tick();
 assert.equal(h.node('agent-invite-panel').hidden,true,'delegation panel stays hidden');
 assert.equal(h.calls.filter(call=>call.path==='rooms/room/agent-invite-policies').length,0,'no policy call while off');
 h.respond('PATCH','rooms/room/memberships/reader-bot',{ok:true});
 h.run("expandParticipation(view.room,agents[1],view.room.members['reader-bot'])");
 assert.ok(h.confirms.some(message=>message.includes(labels.ko.agentInviteExpandConfirm)),'execution warning is confirmed');
 await tick();
 const request=h.calls.find(call=>call.method==='PATCH'&&call.path==='rooms/room/memberships/reader-bot');
 assert.deepEqual(request.body,{expected_revision:2,status:'active',permissions:['room:read','room:speak','task:claim','task:heartbeat','task:complete','task:skip'],acknowledge_external_execution:true});
 assert.equal(h.calls.filter(call=>call.path==='rooms/room/agent-invite-policies').length,0);
});

test('expansion unions existing permissions so an invite scope is never silently removed',async()=>{
 const h=await harness();h.capability(false);h.run("view.room.members['reader-bot'].permissions=['room:read','room:invite_agents'];");h.openPanel();await tick();
 h.respond('PATCH','rooms/room/memberships/reader-bot',{ok:true});
 h.run("expandParticipation(view.room,agents[1],view.room.members['reader-bot'])");
 await tick();
 const request=h.calls.find(call=>call.method==='PATCH'&&call.path==='rooms/room/memberships/reader-bot');
 assert.deepEqual(request.body.permissions,['room:read','room:invite_agents','room:speak','task:claim','task:heartbeat','task:complete','task:skip']);
});

test('expansion includes room:contribute only in co-creation mode',async()=>{
 const h=await harness();h.capability(false);h.run("view.room.mode='co_creation';");h.openPanel();await tick();
 h.respond('PATCH','rooms/room/memberships/reader-bot',{ok:true});
 h.run("expandParticipation(view.room,agents[1],view.room.members['reader-bot'])");
 await tick();
 const request=h.calls.find(call=>call.method==='PATCH'&&call.path==='rooms/room/memberships/reader-bot');
 assert.deepEqual(request.body.permissions,['room:read','room:speak','task:claim','task:heartbeat','task:complete','task:skip','room:contribute']);
});

test('capability is gated by the server flag and room permission',async()=>{
 const h=await harness();h.respond('GET','capabilities',{text_attachments:{enabled:false},agent_invitations:{enabled:true}});
 await h.run('refreshAgentInvitationSettings()');
 h.openPanel();await tick();
 assert.equal(h.node('agent-invite-panel').hidden,false);
 h.run("view.room.effective_permissions=['room:read'];renderParticipants(view.room);");
 assert.equal(h.node('agent-invite-panel').hidden,true,'panel needs room:manage_members');
});

test('policy UI additionally requires the room effective room:read permission',async()=>{
 const h=await harness();h.capability(true);
 h.run("view.room.effective_permissions=['room:manage_members','credential:manage'];renderParticipants(view.room);");
 await tick();
 assert.equal(h.node('agent-invite-panel').hidden,true,'policy panel is hidden without room:read');
 assert.equal(h.calls.filter(call=>call.path==='rooms/room/agent-invite-policies').length,0);
 h.run("view.room.effective_permissions=['room:read','room:manage_members','credential:manage'];renderParticipants(view.room);");
 await tick();
 assert.equal(h.node('agent-invite-panel').hidden,false,'room:read restores the policy panel');
});

test('accepted read-only participants show manual promotion, credential and worker guidance and pending is not unconditional',async()=>{
 const h=await harness();h.capability(true);h.openPanel();await tick();
 let text=h.node('members').textContent;
 assert.ok(text.includes(labels.ko.agentInviteReadOnlyGuidance),'read-only accepted member sees the separate promotion/credential/worker steps');
 assert.ok(text.includes(labels.ko.pendingAcceptanceHelp),'pending member sees the lifecycle caveat');
 assert.equal(text.includes('다시 초대할 필요가 없습니다'),false,'pending help no longer promises that waiting is enough');
 assert.equal(h.calls.filter(call=>call.method==='POST'||call.method==='PATCH').length,0,'rendering performs no promotion, credential or acceptance mutation');
 h.run("view.room.members['reader-bot'].connection={runtime:'codex',state:'idle',last_seen_at:Date.now()/1000};renderParticipants(view.room);");
 assert.ok(h.node('members').textContent.includes(labels.ko.agentInviteExecutionRisk),'the risk warning remains when a worker connection becomes visible');
 assert.equal(h.calls.filter(call=>call.method==='POST'||call.method==='PATCH').length,0);
});

test('read-only execution risk warning is unconditional even when connection is null',async()=>{
 const h=await harness();h.capability(true);h.run("view.room.members['reader-bot'].connection=null;renderParticipants(view.room);");
 const text=h.node('members').textContent;
 assert.ok(text.includes(labels.ko.agentInviteReadOnlyGuidance));
 assert.ok(text.includes(labels.ko.agentInviteExecutionRisk),'a missing connection snapshot is not evidence that no credential or future worker exists');
 assert.equal(h.calls.filter(call=>call.method==='POST'||call.method==='PATCH').length,0,'the warning itself never promotes, issues or accepts');
});

test('pending cleanup is explicit, works with the capability off, and sends only the whitelisted purge body',async()=>{
 const h=await harness();h.run("user.scopes.push('room:read')");h.respond('POST','rooms/room/memberships/pending-bot/purge',{purged:true});
 h.openPanel();await tick();
 const card=h.node('members').textContent;
 assert.ok(card.includes(labels.ko.agentInviteCleanupHelp),'manager sees the server-verification explanation');
 assert.ok(card.includes(labels.ko.agentInviteCleanupRequest),'cleanup action is visible even while delegated invites are off');
 h.run("purgePendingInvitation(view.room,agents.find(a=>a.id==='pending-bot'),view.room.members['pending-bot'])");
 await tick();await tick();
 const calls=h.calls.filter(call=>call.method==='POST'&&call.path==='rooms/room/memberships/pending-bot/purge');
 assert.equal(calls.length,1);
 assert.deepEqual(calls[0].body,{expected_revision:1,acknowledge_cleanup:true});
 assert.ok(calls[0].options.headers['Idempotency-Key'],'idempotency key is sent');
 assert.equal(h.confirms.filter(message=>message===labels.ko.agentInviteCleanupConfirm).length,1);
 assert.equal(h.node('notice').textContent,labels.ko.agentInviteCleanupDone);
 assert.equal(h.calls.filter(call=>call.method==='POST'||call.method==='PATCH').length,1,'cleanup never auto-reinvites, issues or promotes');
});

test('pending cleanup is hidden and inert without room management permission',async()=>{
 const h=await harness({manage:false});h.run("user.scopes.push('room:read')");h.openPanel();await tick();
 const card=h.node('members').textContent;
 assert.equal(card.includes(labels.ko.agentInviteCleanupHelp),false);
 assert.equal(card.includes(labels.ko.agentInviteCleanupRequest),false);
 h.run("purgePendingInvitation(view.room,agents.find(a=>a.id==='pending-bot'),view.room.members['pending-bot'])");
 await tick();await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST'||call.method==='PATCH').length,0);
 assert.equal(h.confirms.length,0);
});

test('pending cleanup treats a 409 live-origin response as not purged and never retries',async()=>{
 const h=await harness();h.run("user.scopes.push('room:read')");
 h.handlers.set('POST rooms/room/memberships/pending-bot/purge',async()=>({ok:false,status:409,json:async()=>({code:'invitation_still_live'})}));
 h.openPanel();await tick();
 h.run("purgePendingInvitation(view.room,agents.find(a=>a.id==='pending-bot'),view.room.members['pending-bot'])");
 await tick();await tick();
 assert.equal(h.node('notice').textContent,labels.ko.agentInviteCleanupNotPurged);
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path==='rooms/room/memberships/pending-bot/purge').length,1,'no automatic retry');
});

for(const [status,code,label] of [[404,'not_found',labels.ko.agentInviteCleanupNotFound],[429,'membership_history_capacity',labels.ko.agentInviteCleanupCapacity]])test('pending cleanup maps '+code+' without retry',async()=>{
 const h=await harness();h.run("user.scopes.push('room:read')");
 h.handlers.set('POST rooms/room/memberships/pending-bot/purge',async()=>({ok:false,status,json:async()=>({code})}));
 h.openPanel();await tick();
 h.run("purgePendingInvitation(view.room,agents.find(a=>a.id==='pending-bot'),view.room.members['pending-bot'])");
 await tick();await tick();
 assert.equal(h.node('notice').textContent,label);
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path==='rooms/room/memberships/pending-bot/purge').length,1,'no automatic retry');
});

test('pending cleanup blocks duplicate clicks and discards a late response after a room switch',async()=>{
 const h=await harness();h.run("user.scopes.push('room:read')");const first=deferred();
 h.handlers.set('POST rooms/room/memberships/pending-bot/purge',()=>first.promise);
 h.openPanel();await tick();
 h.run("purgePendingInvitation(view.room,agents.find(a=>a.id==='pending-bot'),view.room.members['pending-bot'])");
 await tick();
 h.run("purgePendingInvitation(view.room,agents.find(a=>a.id==='pending-bot'),view.room.members['pending-bot'])");
 await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path==='rooms/room/memberships/pending-bot/purge').length,1,'busy ownership blocks a duplicate request');
 const roomB={...JSON.parse(JSON.stringify(h.room)),id:'room-b',room_revision:8};
 await h.switchRoom(roomB);h.node('notice').textContent='';
 first.resolve(ok({purged:true}));await tick();await tick();
 assert.notEqual(h.node('notice').textContent,labels.ko.agentInviteCleanupDone);
});

test('late cleanup success is ignored after the human loses management permission',async()=>{
 const h=await harness();h.run("user.scopes.push('room:read')");const first=deferred();
 h.handlers.set('POST rooms/room/memberships/pending-bot/purge',()=>first.promise);
 h.openPanel();await tick();
 h.run("purgePendingInvitation(view.room,agents.find(a=>a.id==='pending-bot'),view.room.members['pending-bot'])");
 await tick();h.run("user.scopes=user.scopes.filter(scope=>scope!=='room:manage_members');");
 first.resolve(ok({purged:true}));await tick();await tick();
 assert.notEqual(h.node('notice').textContent,labels.ko.agentInviteCleanupDone);
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path==='rooms/room/memberships/pending-bot/purge').length,1);
});

test('closed rooms never offer pending cleanup',async()=>{
 const h=await harness();h.run("user.scopes.push('room:read');view.room.state='closed';renderParticipants(view.room);");
 const card=h.node('members').textContent;
 assert.equal(card.includes(labels.ko.agentInviteCleanupHelp),false);
 assert.equal(card.includes(labels.ko.agentInviteCleanupRequest),false);
 h.run("purgePendingInvitation(view.room,agents.find(a=>a.id==='pending-bot'),view.room.members['pending-bot'])");
 await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST'||call.method==='PATCH').length,0);
 assert.equal(h.confirms.length,0);
});

test('cleanup URL encodes room and principal path segments without query or fragment escape',async()=>{
 const h=await harness();h.run("user.scopes.push('room:read');");
 const rid='room%2F?#/x',pid='pending%2F?#/bot',encodedPath='rooms/'+encodeURIComponent(rid)+'/memberships/'+encodeURIComponent(pid)+'/purge';
 h.handlers.set('POST '+encodedPath,async()=>ok({purged:true}));
 h.run("const hostileRoomId="+JSON.stringify(rid)+",hostileAgentId="+JSON.stringify(pid)+";const hostileRoom={...view.room,id:hostileRoomId};hostileRoom.members={...view.room.members,[hostileAgentId]:{kind:'agent',status:'pending',revision:1,epoch:0,permissions:['room:read']}};view.select(hostileRoomId);view.room=hostileRoom;");
 h.run("const hostileAgent={id:hostileAgentId,display_name:'Hostile',handle:'hostile'};purgePendingInvitation(view.room,hostileAgent,view.room.members[hostileAgent.id])");
 await tick();await tick();
 const calls=h.calls.filter(call=>call.method==='POST'&&call.path.endsWith('/purge'));
 assert.equal(calls.length,1);
 assert.equal(calls[0].path,encodedPath,'each dynamic segment is encoded exactly once');
 assert.equal(calls[0].path.includes('?'),false,'no query injection from the room or principal id');
 assert.equal(calls[0].path.includes('#'),false,'no fragment injection from the room or principal id');
 assert.equal(calls[0].path.split('/').length,5,'the encoded ids do not add path segments');
});

test('pending cleanup uses English labels and confirmation',async()=>{
 const h=await harness({language:'en'});h.run("user.scopes.push('room:read')");h.respond('POST','rooms/room/memberships/pending-bot/purge',{purged:true});
 h.openPanel();await tick();
 assert.ok(h.node('members').textContent.includes(labels.en.agentInviteCleanupHelp));
 h.run("purgePendingInvitation(view.room,agents.find(a=>a.id==='pending-bot'),view.room.members['pending-bot'])");
 await tick();await tick();
 assert.equal(h.confirms.filter(message=>message===labels.en.agentInviteCleanupConfirm).length,1);
 assert.equal(h.node('notice').textContent,labels.en.agentInviteCleanupDone);
});

test('cleanup explanation and labels stay inert with a hostile pending name',async()=>{
 const h=await harness();h.run("user.scopes.push('room:read');agents.find(a=>a.id==='pending-bot').display_name='<img src=x onerror=alert(1)>';");
 h.openPanel();await tick();
 const card=h.node('members').textContent;
 assert.ok(card.includes('<img src=x onerror=alert(1)>'),'hostile identity is rendered as text');
 assert.ok(card.includes(labels.ko.agentInviteCleanupHelp));
 assert.equal(h.calls.filter(call=>call.method==='POST'||call.method==='PATCH').length,0);
});

test('inviter selection only offers active agents holding room:invite_agents and guides to grant it first',async()=>{
 const h=await harness();h.capability(true);h.openPanel();await tick();
 assert.deepEqual(JSON.parse(JSON.stringify(h.run('[...el("agent-invite-inviter").options].map(option=>option.value)'))),['inviter-bot'],'only the scoped inviter is offered');
 h.run("view.room.members['inviter-bot'].permissions=view.room.members['inviter-bot'].permissions.filter(permission=>permission!=='room:invite_agents');renderParticipants(view.room);");
 await tick();
 assert.equal(h.run('el("agent-invite-inviter").options.length'),0,'no inviter without the invite scope');
 assert.ok(h.node('agent-invite-targets').textContent.includes(labels.ko.agentInviteNoInviter),'explicit grant-first guidance is shown');
 assert.equal(h.run('agentInviteTargetInputs.length'),0);
 assert.ok(h.node('members').textContent.includes(labels.ko.agentInviteScopeGrant),'participant-card scope grant remains available');
});

test('double credential action is blocked by the in-flight guard',async()=>{
 const h=await harness();h.capability(true);h.respond('POST','agents/pending-bot/credentials',{token:'once',expires_at:Math.floor(Date.now()/1000)+600});
 const first=h.run("issuePendingReadCredential(view.room,agents[2])"),second=h.run("issuePendingReadCredential(view.room,agents[2])");
 await Promise.all([first,second]);await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path==='agents/pending-bot/credentials').length,1,'one credential per in-flight action');
 assert.equal(h.node('credential-value').value,'once');
});

test('a late credential secret is discarded when the target membership changed during the request',async()=>{
 const h=await harness();h.capability(true);
 h.handlers.set('POST agents/pending-bot/credentials',async()=>{h.run("view.room.members['pending-bot'].revision=9;");return ok({token:'late-secret',expires_at:Math.floor(Date.now()/1000)+600});});
 h.run("issuePendingReadCredential(view.room,agents[2])");await tick();await tick();
 assert.equal(h.node('credential-dialog').open,false,'changed membership discards the secret');
 assert.equal(h.node('credential-value').value,'');
 assert.equal(h.node('notice').textContent,labels.ko.agentInviteStale);
});

test('an already displayed credential secret is removed when membership changes are observed',async()=>{
 const h=await harness();h.capability(true);h.respond('POST','agents/pending-bot/credentials',{token:'visible',expires_at:Math.floor(Date.now()/1000)+600});
 h.run("issuePendingReadCredential(view.room,agents[2])");await tick();await tick();
 assert.equal(h.node('credential-dialog').open,true);
 h.run("view.room.members['pending-bot'].permissions=['room:read','room:speak'];guardAgentInviteSecret(view.room);");
 assert.equal(h.node('credential-dialog').open,false);
 assert.equal(h.node('credential-value').value,'');
 assert.equal(h.node('notice').textContent,labels.ko.agentInviteStale);
});

test('a late membership PATCH response is discarded after the view epoch changes',async()=>{
 const h=await harness();h.capability(true);
 h.handlers.set('PATCH rooms/room/memberships/inviter-bot',async()=>{h.run("view.select('other');");return ok({ok:true});});
 h.run("grantInviterScope(view.room,agents[0],view.room.members['inviter-bot'])");await tick();await tick();
 assert.notEqual(h.node('notice').textContent,labels.ko.agentInviteScopeGranted);
 assert.equal(h.run('agentInviteOperationActive({id:"room"})'),false,'in-flight guard is released after the late response');
});

test('member actions require the room effective permission even when the global scope exists',async()=>{
 const h=await harness();h.capability(true);h.run("view.room.effective_permissions=['room:read'];");h.openPanel();await tick();
 assert.equal(h.node('agent-invite-panel').hidden,true);
 h.respond('PATCH','rooms/room/memberships/inviter-bot',{ok:true});h.respond('PATCH','rooms/room/memberships/reader-bot',{ok:true});
 h.run("grantInviterScope(view.room,agents[0],view.room.members['inviter-bot']);expandParticipation(view.room,agents[1],view.room.members['reader-bot']);");await tick();
 assert.equal(h.calls.filter(call=>call.method==='PATCH').length,0,'room effective permission gates execution');
});

test('a late message response preserves newer text and another room draft',async()=>{
 const h=await harness();h.capability(true);
 h.run("view.room.effective_permissions=['room:read','room:manage_members','credential:manage','room:question','room:advice'];renderParticipants(view.room);");
 let resolveFirst;const first=new Promise(done=>{resolveFirst=done;});
 h.handlers.set('POST rooms/room/messages',()=>first);
 h.run("el('message-kind').value='question';el('message-text').value='first draft';");
 h.run("el('message').onsubmit({preventDefault(){}})");
 await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path==='rooms/room/messages').length,1);
 h.run("el('message-text').value='newer draft';");
 resolveFirst(ok({ok:true}));await tick();await tick();
 assert.equal(h.node('message-text').value,'newer draft','a newer same-room input is not cleared');
 const roomB={...JSON.parse(JSON.stringify(h.room)),id:'room-b',room_revision:3};
 let resolveSecond;const second=new Promise(done=>{resolveSecond=done;});
 h.handlers.set('POST rooms/room/messages',()=>second);
 h.run("el('message-text').value='room A second';el('message').onsubmit({preventDefault(){}})");
 await tick();
 await h.switchRoom(roomB);
 h.run("el('message-text').value='room B draft';");
 resolveSecond(ok({ok:true}));await tick();await tick();
 assert.equal(h.node('message-text').value,'room B draft','a late room A success never clears the room B draft');
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path==='rooms/room/messages').length,2,'no duplicate message POST');
});

test('a previous room request cannot release the busy ownership of another room',async()=>{
 const h=await harness();h.capability(true);
 const first=deferred(),second=deferred();
 h.handlers.set('POST agents/pending-bot/credentials',async options=>JSON.parse(options.body).room_id==='room'?first.promise:second.promise);
 const firstRun=h.run("issuePendingReadCredential(view.room,agents[2])");await tick();
 const roomB={...JSON.parse(JSON.stringify(h.room)),id:'room-b',room_revision:3};
 await h.switchRoom(roomB);
 const secondRun=h.run("issuePendingReadCredential(view.room,agents[2])");await tick();
 first.resolve(ok({token:'room-a-secret',expires_at:Math.floor(Date.now()/1000)+600}));await firstRun;await tick();
 const repeated=h.run("issuePendingReadCredential(view.room,agents[2])");await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path==='agents/pending-bot/credentials'&&call.body.room_id==='room-b').length,1,'room B stays guarded after room A settles');
 second.resolve(ok({token:'room-b-secret',expires_at:Math.floor(Date.now()/1000)+600}));
 await Promise.all([secondRun,repeated]);await tick();
 assert.equal(h.node('credential-value').value,'room-b-secret');
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path==='agents/pending-bot/credentials').length,2,'exactly one credential per room');
});

test('a previous policy request cannot release the policy busy ownership of another room',async()=>{
 const h=await harness();h.capability(true);
 const first=deferred(),second=deferred();
 h.handlers.set('POST rooms/room/agent-invite-policy',async()=>first.promise);
 h.handlers.set('POST rooms/room-b/agent-invite-policy',async()=>second.promise);
 const revokeBody={action:'revoke',inviter_id:'inviter-bot',expected_generation:4,expected_policy_revision:1};
 const firstRun=h.run('agentInvitePolicyRequest(view.room,'+JSON.stringify(revokeBody)+",'agentInviteRevoked')");await tick();
 const roomB={...JSON.parse(JSON.stringify(h.room)),id:'room-b',room_revision:3};
 await h.switchRoom(roomB);
 const secondRun=h.run('agentInvitePolicyRequest(view.room,'+JSON.stringify(revokeBody)+",'agentInviteRevoked')");await tick();
 first.resolve(ok({ok:true}));await firstRun;await tick();
 const repeated=h.run('agentInvitePolicyRequest(view.room,'+JSON.stringify(revokeBody)+",'agentInviteRevoked')");await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path==='rooms/room-b/agent-invite-policy').length,1,'room B policy stays guarded after room A settles');
 second.resolve(ok({ok:true}));
 await Promise.all([secondRun,repeated]);await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path==='rooms/room/agent-invite-policy').length,1);
});

test('one in-flight operation in a room blocks the other operation types too',async()=>{
 const h=await harness();h.capability(true);
 const policy=deferred();
 h.handlers.set('POST rooms/room/agent-invite-policy',async()=>policy.promise);
 h.respond('POST','agents/pending-bot/credentials',{token:'after-policy',expires_at:Math.floor(Date.now()/1000)+600});
 const policyRun=h.run('agentInvitePolicyRequest(view.room,{action:"revoke",inviter_id:"inviter-bot",expected_generation:4,expected_policy_revision:1},"agentInviteRevoked")');await tick();
 h.run("issuePendingReadCredential(view.room,agents[2])");await tick();
 assert.equal(h.calls.filter(call=>call.path==='agents/pending-bot/credentials').length,0,'credential waits while a policy mutation owns the room');
 policy.resolve(ok({ok:true}));await policyRun;await tick();
 h.run("issuePendingReadCredential(view.room,agents[2])");await tick();await tick();
 assert.equal(h.calls.filter(call=>call.path==='agents/pending-bot/credentials').length,1);
 assert.equal(h.node('credential-value').value,'after-policy');
});

test('policy targets exclude every recorded room member regardless of status',async()=>{
 const h=await harness();h.capability(true);h.openPanel();await tick();
 const targets=h.run('agentInviteTargetInputs.map(input=>input.value)');
 assert.deepEqual(JSON.parse(JSON.stringify(targets)),['target-a','target-b'],'members are not offered as targets');
 h.run("agents=agents.filter(agent=>!agent.id.startsWith('target-'));renderParticipants(view.room);");
 assert.equal(h.run('agentInviteTargetInputs.length'),0);
 assert.ok(h.node('agent-invite-targets').textContent.includes(labels.ko.agentInviteNoTargets));
});

test('closed rooms cannot submit a policy grant',async()=>{
 const h=await harness();h.capability(true);h.openPanel();await tick();
 h.run("view.room.state='closed';renderParticipants(view.room);");
 assert.equal(h.node('agent-invite-form').hidden,true,'grant form is hidden for a closed room');
 h.fillGrant();await h.run('submitAgentInvitePolicy({preventDefault(){}})');await tick();
 assert.equal(h.calls.filter(call=>call.method==='POST').length,0,'closed room grant is blocked');
});

test('a failed policy GET is not retried automatically and only an explicit retry refetches',async()=>{
 let gets=0;const h=await harness();h.capability(true);
 h.handlers.set('GET rooms/room/agent-invite-policies',async()=>{gets++;return failure(500);});
 h.openPanel();await tick();await tick();await tick();
 assert.equal(gets,1,'one failed fetch, no render loop');
 h.openPanel();await tick();await tick();
 assert.equal(gets,1,'same failing state is not refetched on render');
 assert.ok(h.node('agent-invite-policies').textContent.includes(labels.ko.agentInvitePoliciesFailed));
 h.run('retryAgentInvitePolicies(view.room)');await tick();await tick();
 assert.equal(gets,2,'explicit retry refetches once');
});

test('a displayed secret is removed when the human loses room management permission, credential scope or the room closes',async()=>{
 const scenarios=[
  {name:'room permission',lose:()=>"view.room.effective_permissions=['room:read'];",guard:true},
  {name:'credential scope',lose:()=>"user.scopes=user.scopes.filter(scope=>scope!=='credential:manage');",guard:true},
  {name:'closed room',lose:()=>"view.room.state='closed';",guard:true}
 ];
 for(const scenario of scenarios){
  const h=await harness();h.capability(true);h.respond('POST','agents/pending-bot/credentials',{token:'visible',expires_at:Math.floor(Date.now()/1000)+600});
  h.run('issuePendingReadCredential(view.room,agents[2])');await tick();await tick();
  assert.equal(h.node('credential-dialog').open,true,scenario.name+' initially shows');
  h.run(scenario.lose()+'guardAgentInviteSecret(view.room);');
  assert.equal(h.node('credential-dialog').open,false,scenario.name+' clears the secret');
  assert.equal(h.node('credential-value').value,'');
  assert.equal(h.node('notice').textContent,labels.ko.agentInviteStale);
 }
});

test('a credential secret is not shown when permission is lost before the response',async()=>{
 const scenarios=[
  {name:'room permission',lose:()=>"view.room.effective_permissions=['room:read'];"},
  {name:'credential scope',lose:()=>"user.scopes=user.scopes.filter(scope=>scope!=='credential:manage');"},
  {name:'closed room',lose:()=>"view.room.state='closed';"}
 ];
 for(const scenario of scenarios){
  const h=await harness();h.capability(true);
  h.handlers.set('POST agents/pending-bot/credentials',async()=>{h.run(scenario.lose());return ok({token:'hidden',expires_at:Math.floor(Date.now()/1000)+600});});
  h.run('issuePendingReadCredential(view.room,agents[2])');await tick();await tick();
  assert.equal(h.node('credential-dialog').open,false,scenario.name+' suppresses the secret');
  assert.equal(h.node('credential-value').value,'');
  assert.equal(h.node('notice').textContent,labels.ko.agentInviteStale);
 }
});

test('all delegated invite HTML keys have full ko and en labels',async()=>{ 
 const keys=[...html.matchAll(/data-i18n="(agentInvite[^"]+)"/g)].map(match=>match[1]);
 assert.ok(keys.length>0);
 for(const key of keys){assert.equal(typeof labels.ko[key],'string',key+' ko');assert.equal(typeof labels.en[key],'string',key+' en');assert.equal(labels.ko[key].length>1,true);assert.equal(labels.en[key].length>1,true);}
 assert.ok(html.includes('id="agent-invite-panel" hidden'),'panel starts hidden');
 assert.ok(html.includes('aria-labelledby="agent-invite-title"'));
 assert.ok(html.includes('id="agent-invite-status" role="status"'));
 assert.ok(html.includes('id="agent-invite-error" role="alert"'));
 assert.ok(labels.ko.agentInviteDisclosure.includes('수락 전'));
 assert.ok(labels.ko.agentInviteDisclosure.includes('수락 후'));
});
