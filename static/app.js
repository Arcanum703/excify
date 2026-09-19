// Same-origin requests keep session cookies and API access on the app's host.
// FastAPI serves the same dashboard locally and on Vercel.
// Provider outages are visible; the dashboard never substitutes invented rates.
const $ = id => document.getElementById(id);
const fmt = value => Number(value).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const rateFmt = value => value == null ? '—' : Number(value).toFixed(4);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const when = value => value ? new Date(value).toLocaleString([], {dateStyle:'medium', timeStyle:'short'}) : 'Not yet checked';
let market = null, plans = [], pendingExchange = null, pendingPlan = null, sessionReady = false, toastTimer;

async function api(path, body, method = body === undefined ? 'GET' : 'POST') {
  const response = await fetch(path, {method, headers: {'Content-Type':'application/json', 'X-Excify-Intent':'plan'}, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(25000)});
  let data;
  try { data = await response.json(); } catch { throw new Error('The server could not complete the request. Refresh your saved plans before retrying.'); }
  if (!response.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map(e => e.msg).join('; ') : data.detail || 'Request failed. Please retry.');
  return data;
}
function errorMessage(error) { return error.name === 'TimeoutError' ? 'The request timed out. Refresh to check whether it was saved before retrying.' : error.message || 'Connection unavailable. Please retry.'; }
function showError(error) { $('globalError').textContent = errorMessage(error); $('globalError').hidden = false; }
function toast(text) { clearTimeout(toastTimer); $('toast').textContent = text; $('toast').hidden = false; toastTimer = setTimeout(() => $('toast').hidden = true, 5000); }
async function busy(id, action) { const button = $(id); button.disabled = true; try { await action(); } catch (error) { showError(error); } finally { button.disabled = false; } }

/* ── TAB SWITCH ── */
function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach(panel => panel.classList.toggle('active', panel.id === `tab-${name}`));
  document.querySelectorAll('.tab-btn').forEach(button => { const selected = button.id === `${name}Tab`; button.classList.toggle('active', selected); button.setAttribute('aria-selected', String(selected)); });
}
function planThisAmount() { $('predAmount').value = $('amountInput').value; switchTab('predictify'); $('predAmount').focus(); }

/* ── LIVE RATE ── */
async function fetchLiveRate() {
  // Primary: use the app's verified market snapshot for every display.
  try {
    market = await api('/api/market');
    $('liveRate').textContent = rateFmt(market.rate);
    $('rateDate').textContent = `1 EUR = ${rateFmt(market.rate)} HUF · published ${market.date}`;
    $('mRateToday').textContent = rateFmt(market.rate);
    $('mRateAvg').textContent = rateFmt(market.average_rate);
    const difference = market.average_rate ? (market.rate / market.average_rate - 1) * 100 : null;
    $('mRateDiff').textContent = difference == null ? '—' : `${difference >= 0 ? '+' : ''}${difference.toFixed(2)}%`;
    $('mRateDiff').className = `metric-value ${difference >= 0 ? 'positive' : 'negative'}`;
    if (!$('targetRate').value) $('targetRate').value = (market.rate * 1.008).toFixed(4);
    if (!$('minimumRate').value) $('minimumRate').value = (market.rate * 0.985).toFixed(4);
    drawChart(market.history);
    updatePreview();
    if (!market.usable) throw new Error('The latest published rate is too old to execute. Autopilot will hold safely.');
  } catch (error) {
    // Fail closed: keep historical context, but do not offer an executable quote.
    if (market) market.usable = false;
    $('liveRate').textContent = market ? `${rateFmt(market.rate)} · stale` : 'Unavailable';
    $('depositBtn').disabled = true;
    $('amountPreview').textContent = 'No executable reference quote available.';
    showError(error);
  }
}
let chartModel = {points:[], markers:[]};
function drawChart(points) {
  chartModel = ExcifyChart.buildModel(points, plans);
  $('rateChart').innerHTML = ExcifyChart.renderSvg(chartModel);
  $('chartStart').textContent = chartModel.points[0]?.date || '';
  $('chartEnd').textContent = chartModel.points.at(-1)?.date || '';
  const select = $('chartDecisionSelect'), selected = select.value;
  select.innerHTML = '<option value="">Inspect an agent decision…</option>' + chartModel.markers.map((m,i) => `<option value="${i}">${esc(m.rate_date)} · ${esc(m.label)} · EUR ${fmt(m.amount)}</option>`).join('');
  select.disabled = !chartModel.markers.length;
  select.value = selected;
  $('chartDecision').textContent = chartModel.markers.length ? `${chartModel.markers.length} saved decisions plotted at their actual published rate. Choose a marker to inspect its rationale.` : 'No agent decisions yet. Activate an AI plan to add real execute/hold markers; no example markers are fabricated.';
}
function inspectMarker(index) {
  const marker = chartModel.markers[Number(index)];
  if (!marker || index === '') return;
  $('chartDecisionSelect').value = String(index);
  $('chartDecision').textContent = `${marker.description} Checked ${when(marker.at)}. Backend outcome: ${marker.action}. ${marker.text || ''}`;
}
$('chartDecisionSelect').addEventListener('change', event => inspectMarker(event.target.value));
$('rateChart').addEventListener('click', event => { const marker = event.target.closest('[data-marker]'); if (marker) inspectMarker(marker.dataset.marker); });
$('rateChart').addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { const marker = event.target.closest('[data-marker]'); if (marker) { event.preventDefault(); inspectMarker(marker.dataset.marker); } } });

/* ── DEPOSIT INPUT PREVIEW ── */
function updatePreview() {
  const input = $('amountInput'), amount = Number(input.value), valid = input.value !== '' && input.checkValidity();
  $('receiveAmount').textContent = market && valid ? fmt(amount * market.rate) : '—';
  $('depositBtn').disabled = !valid || !market?.usable || !sessionReady;
  if (market) {
    $('amountPreview').textContent = `EUR → HUF · ${market.source} · ${market.date}`;
    const delta = market.average_rate ? amount * (market.rate - market.average_rate) : 0;
    $('reasoningText').textContent = valid && market.average_rate ? `For EUR ${fmt(amount)}, today’s reference quote is HUF ${fmt(Math.abs(delta))} ${delta >= 0 ? 'higher' : 'lower'} than conversion at the recent historical average. This is a comparison, not a prediction or realized profit.` : 'Enter an amount to see the difference against the recent historical average.';
  }
}
/* ── RULES OVERRIDE ── */
function planInput() {
  for (const id of ['predAmount','predDeadline','targetRate','minimumRate']) if (!$(id).reportValidity()) throw new Error('Please complete the highlighted field.');
  const minimum = Number($('minimumRate').value), target = Number($('targetRate').value);
  if (minimum >= target) throw new Error('Your protected minimum must be lower than the favorable target.');
  return {amount:Number($('predAmount').value), target_rate:target, minimum_rate:minimum, deadline:`${$('predDeadline').value}T23:59:00Z`, strategy:document.querySelector('input[name="strategy"]:checked').value};
}
/* ── TYPEWRITER ── */
function renderEvents(events) {
  return events.map(event => `<li><time>${esc(when(event.at))}${event.rate != null ? ` · ${rateFmt(event.rate)} HUF/EUR · observation ${esc(event.rate_date || '')}` : ''}</time>${event.ai_action ? `<strong>Devin chose: ${esc(event.ai_action.toUpperCase())}</strong><p class="ai-rationale">${esc(event.ai_reasoning)}</p>` : ''}<p><strong>${event.action ? `Backend: ${esc(event.action.toUpperCase())}. ` : ''}</strong>${esc(event.text)}</p>${event.session_id ? `<p class="muted">Devin session: ${esc(event.session_id)}</p>` : ''}${event.authority ? `<p class="muted">${esc(event.authority)}</p>` : ''}</li>`).join('');
}
/* ── STATUS BADGE ── */
function statusBadge(status) { return `<span class="status-badge ${esc(status)}">${esc(status === 'completed' ? 'Simulated exchange' : status)}</span>`; }
/* ── OUTCOME ── */
function receiptMarkup(receipt) {
  if (!receipt) return '';
  return `<div class="receipt"><span class="metric-label">Simulation receipt · no money moved</span><strong>EUR ${fmt(receipt.sent_eur)} → HUF ${fmt(receipt.received_huf)}</strong><p class="muted">${rateFmt(receipt.rate)} HUF/EUR · rate dated ${esc(receipt.rate_date)} · ${esc(receipt.source)}</p><p class="muted">${esc(receipt.fees)}</p><p class="muted" style="word-break:break-all">${esc(receipt.id)}</p></div>`;
}
/* ── HISTORY ── */
function renderPlans(data) {
  plans = (data.plans || []).filter(plan => plan.pair === 'EUR/HUF');
  $('planList').innerHTML = plans.length ? plans.map((plan,index) => {
    const lastAI = plan.events.findLast(event => event.ai_action);
    return `<article class="plan-card"><div class="card-heading"><h3>EUR ${fmt(plan.amount)} → HUF</h3>${statusBadge(plan.pending_ai && plan.status === 'active' ? 'AI analyzing' : plan.status)}</div>
      <p class="muted">AI-directed timing, rule-enforced safety limits</p>
      <div class="plan-meta"><div><span class="metric-label">Favorable target</span><strong>${rateFmt(plan.target_rate)}</strong></div><div><span class="metric-label">Protected minimum</span><strong>${rateFmt(plan.minimum_rate)}</strong></div><div><span class="metric-label">Deadline · UTC</span><strong>${esc(plan.deadline.slice(0,10))}</strong></div></div>
      <p class="plan-reason">${esc(plan.reason)}</p>
      ${lastAI ? `<p class="ai-verdict">AI proposal: <strong>${esc(lastAI.ai_action.toUpperCase())}</strong> · Backend result: <strong>${esc(lastAI.action.toUpperCase())}</strong></p>` : ''}
      ${plan.pending_ai ? `<p class="muted">Analysis requested ${esc(when(plan.pending_ai.created_at))}. No execution occurs until a valid AI result passes the safety checks.</p>` : ''}
      <p class="muted" style="margin-top:10px">${plan.strategy === 'optimal' ? 'AI optimal timing' : 'AI timing above target only'} · Last completed check: ${esc(when(plan.last_checked_at))}</p>
      ${receiptMarkup(plan.receipt)}
      ${['active','paused'].includes(plan.status) ? `<div class="button-row"><button class="btn-secondary" data-action="toggle" data-id="${esc(plan.id)}" role="switch" aria-checked="${plan.status === 'active'}">Autopilot ${plan.status === 'active' ? 'ON — pause' : 'OFF — resume'}</button><button class="btn-secondary" data-action="check" data-id="${esc(plan.id)}" ${plan.status === 'paused' ? 'disabled' : ''}>${plan.pending_ai ? 'Check AI result' : 'Ask AI to recheck'}</button></div>` : ''}
      <details ${index === 0 && lastAI ? 'open' : ''}><summary>Decision trail (${plan.events.length})</summary><ol class="timeline">${renderEvents(plan.events)}</ol></details></article>`;
  }).join('') : '<div class="history-empty"><strong>No EUR/HUF plans yet.</strong><p class="muted">Activate autopilot to request a real AI timing decision.</p></div>';
  const completed = plans.filter(plan => plan.receipt);
  $('historyList').innerHTML = completed.length ? completed.slice(0,3).map(plan => `<div class="history-item"><div><strong>EUR ${fmt(plan.amount)}</strong><p class="muted">${esc(when(plan.receipt.executed_at))}</p></div><div style="text-align:right"><strong class="positive">HUF ${fmt(plan.receipt.received_huf)}</strong><p class="muted">${plan.receipt.authority === 'User-confirmed simulation' ? 'User-confirmed' : 'AI-directed'} simulation</p></div></div>`).join('') : '<div class="history-empty">Your first simulated exchange starts here.</div>';
  if (market) drawChart(market.history);
}
async function refreshPlans() {
  try {
    const state = await api('/api/state');
    sessionReady = true;
    renderPlans(state);
    $('aiStatus').textContent = state.ai_configured ? 'Devin configured · AI has execute/wait authority within your safety limits.' : 'Devin is not configured. Autonomous plans hold safely; no rule-based execution fallback.';
    $('schedulerStatus').textContent = state.background_enabled ? 'Vercel requests AI checks daily between 17:00 and 18:00 UTC. Devin delivers decisions back to the server, even with your browser closed. Activation and resume request a check too.' : 'Local/preview mode: no background scheduler or production callback. Use Check AI result to collect a pending decision.';
    const heartbeat = state.heartbeat;
    const old = heartbeat && Date.now() - new Date(heartbeat.checked_at).getTime() > 27 * 3600000;
    $('schedulerHeartbeat').textContent = heartbeat ? `Last server run: ${when(heartbeat.checked_at)} · ${old ? 'OVERDUE — monitoring needs attention' : heartbeat.status === 'ok' ? 'Healthy' : 'Degraded — some checks could not complete'}` : 'First server check not yet verified.';
    $('schedulerHeartbeat').className = old || heartbeat?.status === 'degraded' ? 'negative muted' : 'muted';
    updatePreview();
  } catch (error) { showError(error); }
}
$('planList').addEventListener('click', async event => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  button.disabled = true;
  try {
    const plan = plans.find(p => p.id === button.dataset.id);
    const data = button.dataset.action === 'toggle' ? await api(`/api/plans/${plan.id}`, {enabled:plan.status !== 'active'}, 'PATCH') : await api(`/api/plans/${plan.id}/check`, {});
    renderPlans(data);
    toast(button.dataset.action === 'toggle' ? 'Plan updated and saved.' : 'Check complete. Repeated manual checks are limited to once every five minutes.');
  } catch (error) { showError(error); button.disabled = false; }
});
/* ── AUDIO ── */
function playAudio(text) {
  if ('speechSynthesis' in window) { speechSynthesis.cancel(); const utterance = new SpeechSynthesisUtterance(text); utterance.lang = 'en-US'; speechSynthesis.speak(utterance); }
}
/* ── DEPOSIT ── */
function runDeposit() {
  if (!$('amountInput').reportValidity() || !market?.usable || !sessionReady) return;
  const amount = Number($('amountInput').value);
  if (!pendingExchange || pendingExchange.amount !== amount) pendingExchange = {amount, confirmation:true, request_id:crypto.randomUUID()};
  $('confirmAmount').textContent = `EUR ${fmt(amount)} → HUF ${fmt(amount * market.rate)}`;
  $('confirmRate').textContent = `Reference rate: ${rateFmt(market.rate)} · ${market.date}`;
  $('dialogError').textContent = '';
  $('exchangeDialog').showModal();
}
async function confirmExchange() {
  $('confirmBtn').disabled = true;
  try { const data = await api('/api/exchange', pendingExchange); renderPlans(data); pendingExchange = null; $('exchangeDialog').close(); toast('Simulation complete. Your receipt is saved.'); }
  catch (error) { $('dialogError').textContent = errorMessage(error); }
  finally { $('confirmBtn').disabled = false; }
}
/* ════════════════════════════
   PREDICTIFY LOGIC (simulated)
════════════════════════════ */
async function runPredictify(event) {
  event.preventDefault();
  if (!$('planForm').reportValidity()) return;
  await busy('predBtn', async () => {
    // Special rate alerts are derived from server decisions, never client overrides.
    const input = planInput(), fingerprint = JSON.stringify(input);
    if (pendingPlan?.fingerprint !== fingerprint) pendingPlan = {fingerprint, request_id:crypto.randomUUID()};
    const data = await api('/api/plans', {...input, request_id:pendingPlan.request_id});
    pendingPlan = null;
    $('globalError').hidden = true;
    renderPlans(data);
    $('authorizePlan').checked = false;
    toast('Autopilot plan saved. Review its status and decision log below.');
    $('savedPlansHeading').scrollIntoView({behavior:'smooth', block:'center'});
  });
}
async function runReplay() {
  await busy('replayBtn', async () => {
    // Rates come from actual dated historical observations.
    const data = await api('/api/replay', planInput());
    // The replay deadline is historical; no random future dates are generated.
    $('predResults').hidden = false;
    // Timeline
    $('replayTimeline').innerHTML = renderEvents(data.plan.events);
    // Build cards
    $('replaySummary').innerHTML = `${statusBadge(data.plan.status)}<p style="margin-top:10px">${esc(data.plan.reason)}</p>${receiptMarkup(data.plan.receipt)}`;
    $('predResults').scrollIntoView({behavior:'smooth', block:'nearest'});
  });
}
/* ── Set default date to 7 days from now ── */
const deadline = new Date(Date.now() + 7 * 86400000);
$('predDeadline').value = deadline.toISOString().slice(0,10);
$('predDeadline').min = new Date(Date.now() + 26 * 3600000).toISOString().slice(0,10);
$('predDeadline').max = new Date(Date.now() + 89 * 86400000).toISOString().slice(0,10);
/* ════════════════════════════
   VOICE MODE AGENT
════════════════════════════ */
(function initVoiceMode() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) { $('voiceModeSection').hidden = true; return; }
  const recognition = new Recognition(); recognition.lang = 'en-US';
  let listening = false;
  /* ── UI helpers ── */
  function setListening(on) { listening = on; $('voiceModeBtn').textContent = on ? 'Listening…' : 'Use voice'; }
  /* ── Number parser (digits + English words) ── */
  function parseAmount(text) { const match = text.match(/\b\d[\d,]*(?:\.\d+)?\b/); return match ? Number(match[0].replaceAll(',', '')) : null; }
  /* ── Speak: backend /speak (ElevenLabs turbo_v2) or browser fallback ── */
  function speakText(text) { $('voiceTranscript').textContent = text; playAudio(text); }
  /* ── Last-month average from Frankfurter ── */
  function averageText() { return market?.average_rate ? `Recent historical average: ${rateFmt(market.average_rate)} HUF per EUR.` : 'Historical rates are unavailable.'; }
  /* ── Intent dispatcher ── */
  function handleTranscript(raw) {
    const text = raw.toLowerCase(); $('voiceTranscript').textContent = `Heard: ${raw}`;
    /* INTENT 1 — Exchange / Buy */
    if (/exchange|convert|buy/.test(text)) { const amount = parseAmount(text); if (amount) { $('amountInput').value = amount; updatePreview(); switchTab('agent'); speakText('Amount entered. Review and confirm the simulation on screen.'); } else speakText('Please enter the amount on screen.'); return; }
    /* INTENT 5 — Predictify */
    if (/plan|deadline|autopilot/.test(text)) { switchTab('predictify'); speakText('Set your target, minimum, and deadline.'); return; }
    /* INTENT 3 — Last month average (Frankfurter) */
    if (/average|history/.test(text)) { speakText(averageText()); return; }
    /* INTENT 2 — Live rate */
    if (/rate|current/.test(text)) { speakText(market?.usable ? `Reference rate: ${rateFmt(market.rate)} HUF per EUR, dated ${market.date}.` : 'A verified rate is unavailable.'); return; }
    /* INTENT 4 — Status */
    if (/status|monitor/.test(text)) { speakText(`${plans.filter(p => p.status === 'active').length} active plans in this session.`); return; }
    /* INTENT — Compare yesterday vs today */
    if (/compare/.test(text)) { speakText($('reasoningText').textContent); return; }
    /* DEFAULT */
    speakText('Try convert 500 euros, current rate, or autopilot.');
  }
  /* ── Recognition events ── */
  recognition.onstart = () => setListening(true);
  recognition.onend = () => setListening(false);
  recognition.onresult = event => handleTranscript(event.results[0][0].transcript);
  recognition.onerror = () => { setListening(false); $('voiceTranscript').textContent = 'Voice is unavailable. All controls work without a microphone.'; };
  /* ── Tap-to-toggle ── */
  window.voiceToggle = () => { try { listening ? recognition.stop() : recognition.start(); } catch { setListening(false); } };
})();
Promise.all([fetchLiveRate(), refreshPlans()]);
setInterval(() => { if (!document.hidden) { fetchLiveRate(); refreshPlans(); } }, 600000);
let checkingPending = false;
setInterval(async () => {
  if (document.hidden || checkingPending || !plans.some(plan => plan.pending_ai)) return;
  checkingPending = true;
  try {
    for (const plan of plans.filter(plan => plan.pending_ai)) renderPlans(await api(`/api/plans/${plan.id}/check`, {}));
    await refreshPlans();
  } catch (error) { showError(error); }
  finally { checkingPending = false; }
}, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { fetchLiveRate(); refreshPlans(); } });
