// Static CSS source checks for the reduced-motion contract. This asserts how the stylesheet is
// written, not how it renders; a real browser is never involved.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const css=readFileSync(new URL('../talkabout/web/style.css',import.meta.url),'utf8');
const REDUCED='@media (prefers-reduced-motion: reduce)';
const stripped=css.replace(/\/\*[\s\S]*?\*\//g,'');
const lineOf=(source,index)=>source.slice(0,index).split('\n').length;

// Brace matcher that skips quoted strings, so a brace inside content/url cannot desync the scan.
function blockAt(source,openIndex){
 let depth=0,quote=null;
 for(let i=openIndex;i<source.length;i++){
  const ch=source[i];
  if(quote){if(ch==='\\'){i++;continue;}if(ch===quote)quote=null;continue;}
  if(ch==='"'||ch==="'"){quote=ch;continue;}
  if(ch==='{')depth++;
  else if(ch==='}'){depth--;if(depth===0)return {body:source.slice(openIndex+1,i),end:i};}
 }
 return null;
}
function blockAfter(source,header){
 const start=source.indexOf(header);
 assert.notEqual(start,-1,'missing block: '+header);
 const open=source.indexOf('{',start);
 assert.notEqual(open,-1,'missing opening brace after: '+header);
 const block=blockAt(source,open);
 assert.ok(block,'unterminated block opened at line '+lineOf(source,start));
 return {start,body:block.body};
}
// Top-level rules only: the reduced-motion body is expected to be a flat list of rule blocks.
function topLevelRules(body){
 const rules=[];
 let depth=0,selectorStart=0,bodyStart=-1;
 for(let i=0;i<body.length;i++){
  const ch=body[i];
  if(ch==='{'){if(depth===0)bodyStart=i+1;depth++;}
  else if(ch==='}'){depth--;if(depth===0){rules.push({selector:body.slice(selectorStart,bodyStart-1).replace(/\s+/g,' ').trim(),declarations:body.slice(bodyStart,i)});selectorStart=i+1;}}
 }
 return rules;
}
const reduced=blockAfter(stripped,REDUCED);
const reducedRules=topLevelRules(reduced.body);
const declarationsFor=selector=>reducedRules.filter(rule=>rule.selector.split(',').map(part=>part.trim()).includes(selector)).map(rule=>rule.declarations).join(';');

test('reduced-motion block suppresses motion only and carries no layout or paint declarations',()=>{
 assert.equal((stripped.match(/@media \(prefers-reduced-motion: reduce\)/g)||[]).length,1,'exactly one reduce block expected');
 const properties=reducedRules.flatMap(rule=>[...rule.declarations.matchAll(/(?:^|[;{])\s*([a-zA-Z-]+)\s*:/g)].map(match=>match[1]));
 assert.ok(properties.length>0,'reduce block declares nothing');
 assert.ok(properties.every(property=>property==='animation'||property==='transition'),'unexpected declaration(s): '+properties.filter(property=>property!=='animation'&&property!=='transition').join(', '));
 for(const property of ['position','padding','border-radius','background','backdrop-filter']){
  assert.doesNotMatch(reduced.body,new RegExp('(?:^|[;{\\s])'+property+'\\s*:'),'reduce block must not declare '+property);
 }
});

test('the stage-caption pill rule is declared exactly once, outside the reduced-motion block',()=>{
 assert.equal((stripped.match(/\.stage-caption\b/g)||[]).length,1);
 assert.equal((reduced.body.match(/\.stage-caption\b/g)||[]).length,0);
});

for(const selector of ['body::before','.room-stage .motes circle','.room-stage .prop-lights circle','.live-dot'])test('reduced-motion stops the '+selector+' animation',()=>{
 assert.match(declarationsFor(selector),/animation\s*:\s*none\s*!important/,'no animation:none for '+selector);
});

test('existing character and global transition suppression is preserved',()=>{
 for(const selector of ['.room-stage .ch','.room-stage .ch *'])assert.match(declarationsFor(selector),/animation\s*:\s*none\s*!important/,'lost '+selector);
 for(const selector of ['*','*::before','*::after'])assert.match(declarationsFor(selector),/transition\s*:\s*none\s*!important/,'lost '+selector);
});

test('every @media block closes where it opens',()=>{
 const source=stripped;
 let count=0,match;
 const media=/@media[^{]*/g;
 while((match=media.exec(source))){
  const open=source.indexOf('{',match.index);
  assert.notEqual(open,-1,'@media without opening brace at line '+lineOf(source,match.index));
  assert.ok(blockAt(source,open),'unbalanced @media block opened at line '+lineOf(source,match.index));
  count++;
  media.lastIndex=open+1;
 }
 assert.ok(count>0,'no @media blocks found');
});

test('the stylesheet brace depth returns to zero',()=>{
 let depth=0,quote=null;
 for(let i=0;i<stripped.length;i++){
  const ch=stripped[i];
  if(quote){if(ch==='\\'){i++;continue;}if(ch===quote)quote=null;continue;}
  if(ch==='"'||ch==="'"){quote=ch;continue;}
  if(ch==='{')depth++;
  else if(ch==='}'){depth--;assert.ok(depth>=0,'stray closing brace at line '+lineOf(stripped,i));}
 }
 assert.equal(depth,0,'unclosed rule block at end of stylesheet');
});
