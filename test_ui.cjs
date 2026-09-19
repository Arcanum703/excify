const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function render(plan) {
  const elements = new Map();
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {innerHTML:'', textContent:'', value:'', style:{}, addEventListener(){}, hidden:false});
      return elements.get(id);
    },
    addEventListener(){},
  };
  const context = vm.createContext({document, window:{}, fetch:() => new Promise(() => {}),
    AbortSignal:{timeout(){}}, setInterval(){}, setTimeout(){}, clearTimeout(){}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'static/app.js'), 'utf8'), context);
  context.renderPlans({plans:[plan]});
  return elements.get('planList').innerHTML;
}
const aiWait = {at:'2026-09-19T10:00:00Z', code:'ai_wait', action:'hold', ai_action:'wait',
  ai_reasoning:'Wait for the target while there is time.', text:'AI chose to wait.', rate:364.28, rate_date:'2026-09-18'};
const target = {at:'2026-09-19T11:00:00Z', code:'target_execute', action:'execute', ai_action:null,
  text:'Favorable target reached.', rate:380, rate_date:'2026-09-18'};
function plan(events, changes={}) {
  return {id:'test-plan', pair:'EUR/HUF', amount:'100.00', target_rate:'380', minimum_rate:'350',
    deadline:'2026-09-20T23:59:00Z', strategy:'optimal', status:'active', reason:events.at(-1).text,
    last_checked_at:events.at(-1).at, events, ...changes};
}
function latest(html) { return html.match(/<p class="latest-outcome">([\s\S]*?)<\/p>/)?.[1] || ''; }

test('target execution replaces the displayed current hold result', () => {
  const html = render(plan([aiWait,target],{status:'completed'}));
  assert.match(latest(html), /EXECUTE/);
  assert.doesNotMatch(latest(html), /HOLD/);
  assert.match(html, /Previous AI proposal/);
  assert.match(html, /Previous outcome/);
  assert.doesNotMatch(html, /Backend result: <strong>HOLD/);
});
test('latest AI veto shows proposal and actual hold outcome separately', () => {
  const html = render(plan([{...aiWait,ai_action:'execute',code:'below_minimum',text:'Protected minimum blocks execution.'}]));
  assert.match(latest(html), /HOLD/);
  assert.match(html, /AI proposal for that check: <strong>EXECUTE/);
});
test('target-only decisions do not invent an AI verdict', () => {
  const html = render(plan([target],{status:'completed'}));
  assert.match(latest(html), /EXECUTE/);
  assert.doesNotMatch(html, /class="ai-verdict"/);
});
test('new pending analysis labels previous AI verdict as historical', () => {
  const html = render(plan([aiWait],{pending_ai:{created_at:'2026-09-19T11:00:00Z'}}));
  assert.match(html, /AI analyzing/);
  assert.match(html, /Previous AI proposal/);
});
test('decision text remains escaped', () => {
  const html = render(plan([{...aiWait,ai_reasoning:'<script>unsafe</script>',text:'<img src=x onerror=unsafe>'}]));
  assert.doesNotMatch(html, /<script>|<img src=x/);
});
