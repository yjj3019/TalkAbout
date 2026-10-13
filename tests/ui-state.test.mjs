import test from 'node:test';
import assert from 'node:assert/strict';
import {ViewState,labels} from '../talkabout/web/state.mjs';
test('duplicate click and stable retry key',()=>{const v=new ViewState();v.select('a');const first=v.start('pause',{revision:1});assert.equal(v.start('pause',{revision:1}),null);v.finish(first,false);const retry=v.start('pause',{revision:1});assert.equal(first.key,retry.key);});
test('late read cannot reopen closed view',()=>{const v=new ViewState();const old=v.select('a');v.close();assert.equal(v.accept(old,{id:'a'}),false);assert.equal(v.room,null);});
test('back navigation rejects response from a different room',()=>{const v=new ViewState();const old=v.select('a');const current=v.select('b');assert.equal(v.accept(old,{id:'a'}),false);assert.equal(v.accept(current,{id:'b'}),true);});
test('stale mutation completion does not refresh another view',()=>{const v=new ViewState();v.select('a');const mutation=v.start('pause',{});v.close();v.select('b');assert.equal(v.finish(mutation,true),false);});
test('KO EN labels match and language preserves read context',()=>{assert.deepEqual(Object.keys(labels.ko).sort(),Object.keys(labels.en).sort());const v=new ViewState();const ticket=v.select('a');v.languageTo('en');assert.equal(v.accept(ticket,{id:'a'}),true);assert.equal(v.language,'en');});
