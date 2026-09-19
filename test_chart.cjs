const test = require('node:test');
const assert = require('node:assert/strict');
const {buildModel, renderSvg} = require('./static/rate-chart.js');
const history = [
  {date:'2026-09-14',rate:365.33},
  {date:'2026-09-15',rate:366.03},
  {date:'2026-09-17',rate:362.98},
  {date:'2026-09-18',rate:364.28},
];
const event = (action, date, rate, extra = {}) => ({action,rate,rate_date:date,pair:'EUR/HUF',at:'2026-09-19T10:00:00Z',authority:'AI-directed timing, rule-enforced safety limits',ai_action:action === 'execute' ? 'execute':'wait',ai_reasoning:'The current trend and goal support this test decision.',...extra});

test('plots actual values on a calendar-date axis', () => {
  const model = buildModel(history, []);
  assert.equal(model.points.length, 4);
  assert.equal(model.points[3].rate, 364.28);
  assert.equal(model.points[2].x - model.points[1].x, 2 * (model.points[1].x - model.points[0].x));
});
test('maps execute and wait to the published observation date', () => {
  const model = buildModel(history, [{id:'one',amount:100,events:[event('execute','2026-09-15',366.03),event('hold','2026-09-18',364.28)]}]);
  assert.equal(model.markers.length, 2);
  assert.equal(model.markers[0].x, model.points[1].x);
  assert.equal(model.markers[1].y, model.points[3].y);
  assert.equal(model.markers[1].label, 'AI wait');
});
test('distinguishes an AI execute veto from an AI wait', () => {
  const marker = buildModel(history,[{id:'one',events:[event('hold','2026-09-18',364.28,{ai_action:'execute',safety_allowed:false,code:'below_minimum'})]}]).markers[0];
  assert.equal(marker.label, 'Safety veto');
});
test('does not invent markers for missing or mismatched observations', () => {
  const events = [event('hold','2026-09-13',365),event('execute','2026-09-18',400)];
  assert.equal(buildModel(history,[{id:'one',events}]).markers.length,0);
});
test('deduplicates delivery retries using decision context', () => {
  const e = event('execute','2026-09-18',364.28,{context_id:'same-decision'});
  assert.equal(buildModel(history,[{id:'one',events:[e,e]}]).markers.length,1);
});
test('escapes model-generated explanations in SVG', () => {
  const model = buildModel(history,[{id:'one',events:[event('hold','2026-09-18',364.28,{ai_reasoning:'<script>attack</script>'})]}]);
  const svg = renderSvg(model);
  assert.ok(svg.includes('decision-marker'));
  assert.ok(!svg.includes('<script>'));
});
test('empty history has a clear empty chart, not mock values', () => {
  assert.equal(buildModel([],[]).points.length,0);
  assert.ok(renderSvg(buildModel([],[])).includes('No verified observations'));
});
