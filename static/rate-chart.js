(function(root) {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const authority = 'AI-directed timing, rule-enforced safety limits';
  function buildModel(history, plans) {
    const points = history.filter(p => /^\d{4}-\d{2}-\d{2}$/.test(p.date) && Number.isFinite(p.rate) && p.rate > 0)
      .map(p => ({...p, timestamp:Date.parse(p.date + 'T00:00:00Z')})).sort((a,b) => a.timestamp - b.timestamp);
    if (!points.length) return {points:[],markers:[]};
    const low = Math.min(...points.map(p => p.rate)), high = Math.max(...points.map(p => p.rate));
    const range = high - low || Math.max(high * 0.001, 0.01);
    const start = points[0].timestamp, span = points.at(-1).timestamp - start || 86400000;
    for (const point of points) { point.x = 48 + (point.timestamp - start) / span * 436; point.y = 187 - (point.rate - low) / range * 142; }
    const byDate = new Map(points.map(p => [p.date,p]));
    const unique = new Map();
    for (const plan of plans) {
      for (const event of plan.events || []) {
        const point = byDate.get(event.rate_date);
        if (event.authority !== authority || !['hold','execute'].includes(event.action) || event.pair !== 'EUR/HUF' || !point || !Number.isFinite(Number(event.rate)) || Math.abs(Number(event.rate) - point.rate) > 0.000001) continue;
        const label = event.code === 'target_execute' ? 'Target execute' : event.action === 'execute' ? 'AI execute' : event.ai_action === 'execute' ? 'Safety veto' : event.ai_action === 'wait' ? 'AI wait' : 'Safety hold';
        const color = event.action === 'execute' ? '#397028' : event.ai_action === 'execute' ? '#b42318' : '#a56512';
        const key = `${plan.id}:${event.context_id || `${event.at}:${event.rate_date}:${event.action}`}`;
        unique.set(key, {...event, x:point.x, y:point.y, label, color, planId:plan.id, amount:plan.amount,
          description:`${label} · ${point.date} · ${point.rate.toFixed(2)} HUF per EUR. ${event.ai_reasoning || event.text || ''}`});
      }
    }
    return {points, markers:[...unique.values()].sort((a,b) => a.at.localeCompare(b.at)), low, high};
  }
  function renderSvg(model) {
    if (!model.points.length) return '<text x="30" y="110" fill="#667568">No verified observations available</text>';
    const path = model.points.map(p => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ');
    const first = model.points[0], last = model.points.at(-1);
    const guides = [0,0.5,1].map(f => { const y = 187 - f * 142; return `<line x1="48" y1="${y}" x2="484" y2="${y}" stroke="#e2e8f0"/><text x="42" y="${y+4}" text-anchor="end" fill="#667568" font-size="10">${(model.low + f * (model.high-model.low)).toFixed(1)}</text>`; }).join('');
    const markers = model.markers.map((m,index) => `<g class="decision-marker" data-marker="${index}" tabindex="0" role="button" aria-label="${escape(m.description)}"><title>${escape(m.description)}</title><circle cx="${m.x}" cy="${m.y}" r="8" fill="white" stroke="${m.color}" stroke-width="3"/><circle cx="${m.x}" cy="${m.y}" r="3" fill="${m.color}"/></g>`).join('');
    return `<text x="48" y="19" fill="#667568" font-size="10">HUF per EUR</text>${guides}<polygon points="${first.x},200 ${path} ${last.x},200" fill="#e8faf0" opacity="0.7"/><polyline points="${path}" fill="none" stroke="#50872d" stroke-width="3" stroke-linejoin="round"/><circle cx="${last.x}" cy="${last.y}" r="4" fill="#163300"/>${markers}`;
  }
  const api = {buildModel, renderSvg};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ExcifyChart = api;
})(globalThis);
