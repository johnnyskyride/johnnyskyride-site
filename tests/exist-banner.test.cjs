const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync('index.html', 'utf8');
const code = html.slice(html.indexOf('const EXIST_PAYMENT_LINKS'), html.indexOf('})();', html.indexOf('const EXIST_PAYMENT_LINKS')) + 5);
for (const blocked of [false, true]) {
  let stored = '1';
  const els = {};
  for (const id of ['exist-banner','exist-reopen','exist-close','exist-title']) els[id] = {hidden:false,attrs:{},setAttribute(k,v){this.attrs[k]=v},addEventListener(k,v){this[k]=v},focus(){this.focused=true}};
  vm.runInNewContext(code, {document:{getElementById:id=>els[id],querySelectorAll:()=>['333','777','custom'].map(k=>({dataset:{existLink:k},removeAttribute(name){assert.equal(name,'aria-disabled')},set href(url){assert.match(url,/^https:\/\/buy\.stripe\.com\//)}}))},sessionStorage:{getItem(){if(blocked)throw Error();return stored},setItem(k,v){if(blocked)throw Error();stored=v}}});
  assert.equal(els['exist-banner'].hidden, !blocked);
  els['exist-close'].click();
  assert.equal(els['exist-banner'].hidden,true);
  assert.equal(els['exist-reopen'].hidden,false);
  assert.equal(els['exist-reopen'].focused,true);
  els['exist-reopen'].click();
  assert.equal(els['exist-banner'].hidden,false);
  assert.equal(els['exist-title'].focused,true);
}
assert(html.indexOf('id="exist-banner"') > html.indexOf('id="home"'));
assert(html.indexOf('id="exist-banner"') < html.indexOf('id="board"'));
assert.equal((html.match(/data-exist-link=/g)||[]).length,3);
console.log('PASS: session restoration, blocked storage, minimize, reopen, focus, homepage containment, three options');

assert.equal((html.match(/data-exist-link="(?:333|777|custom)" href="https:\/\/buy\.stripe\.com\//g)||[]).length,3);
console.log("PASS: all three real checkout URLs are wired with no-JS fallback");
