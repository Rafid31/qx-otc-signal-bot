/**
 * QX OTC Signal Bot — Frontend App v2.4.0
 * ==========================================
 * - WebSocket connection with auto-reconnect every 3s
 * - REST /api/signals fallback
 * - All state in memory (no localStorage / sessionStorage)
 * - Category tabs: All / Forex / Commodity / Crypto
 * - Filter buttons: All / BUY / SELL
 * - Signal cards with confidence bars, live price, reasons
 * - History table (last 100 signals)
 * - Countdown bar to next candle
 * - Demo fallback via engine.js DemoEngineManager
 */

'use strict';

// ══════════════════════════════════════════════════════════
// STATE — all in memory, never localStorage
// ══════════════════════════════════════════════════════════
const STATE = {
  backendUrl:      '',
  wsConn:          null,
  wsReconnectTimer: null,
  connected:       false,
  mode:            'DEMO',        // 'REAL' | 'DEMO'
  signals:         {},            // keyed by pair_id
  history:         [],            // last 100 signal events
  activeCategory:  'All',
  activeFilter:    'All',
  demoEngine:      null,
  demoInterval:    null,
  restInterval:    null,
  signalCount:     0,
  serverTime:      null,
  countdown:       60,
};

// ══════════════════════════════════════════════════════════
// DOM REFS (cached after DOMContentLoaded)
// ══════════════════════════════════════════════════════════
const $ = id => document.getElementById(id);

let DOM = {};
function cacheDOM() {
  DOM = {
    setupModal:      $('setup-modal'),
    backendInput:    $('backend-url-input'),
    connectBtn:      $('connect-btn'),
    demoBtn:         $('demo-btn'),
    connectionDot:   $('connection-dot'),
    connectionLabel: $('connection-label'),
    modeBadge:       $('mode-badge'),
    pairsCount:      $('pairs-count'),
    signalsCount:    $('signals-count'),
    countdownBar:    $('countdown-bar'),
    countdownText:   $('countdown-text'),
    liveClock:       $('live-clock'),
    categoryTabs:    $('category-tabs'),
    filterBtns:      $('filter-buttons'),
    cardsGrid:       $('cards-grid'),
    historyBody:     $('history-body'),
    historyCount:    $('history-count'),
    serverTimeEl:    $('server-time'),
  };
}

// ══════════════════════════════════════════════════════════
// SETUP MODAL
// ══════════════════════════════════════════════════════════
function showModal() {
  DOM.setupModal.classList.remove('hidden');
}

function hideModal() {
  DOM.setupModal.classList.add('hidden');
}

function onConnectClick() {
  let url = (DOM.backendInput.value || '').trim().replace(/\/$/, '');
  if (!url) { alert('Please enter your Railway backend URL.'); return; }
  if (!url.startsWith('http')) url = 'https://' + url;
  STATE.backendUrl = url;
  hideModal();
  startRealMode(url);
}

function onDemoClick() {
  hideModal();
  startDemoMode();
}

// ══════════════════════════════════════════════════════════
// WEBSOCKET — REAL MODE
// ══════════════════════════════════════════════════════════
function startRealMode(httpUrl) {
  stopDemo();
  const wsUrl = httpUrl.replace(/^https?:\/\//, '').replace(/\/$/, '');
  const proto  = httpUrl.startsWith('https') ? 'wss' : 'ws';
  connectWS(`${proto}://${wsUrl}/ws`, httpUrl);
}

function connectWS(wsUrl, httpUrl) {
  clearTimeout(STATE.wsReconnectTimer);
  if (STATE.wsConn) {
    try { STATE.wsConn.close(); } catch (_) {}
    STATE.wsConn = null;
  }

  setConnectionStatus(false, 'Connecting…');

  try {
    const ws = new WebSocket(wsUrl);
    STATE.wsConn = ws;

    ws.onopen = () => {
      setConnectionStatus(true, 'Connected');
      clearInterval(STATE.restInterval);
      // ping keepalive every 20s
      STATE.pingInterval = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping');
      }, 20000);
    };

    ws.onmessage = evt => {
      try {
        const data = JSON.parse(evt.data);
        if (data === 'pong') return;
        if (data.type === 'signals') handleSignalPayload(data);
      } catch (_) {}
    };

    ws.onerror = () => {};

    ws.onclose = () => {
      clearInterval(STATE.pingInterval);
      setConnectionStatus(false, 'Reconnecting…');
      // REST fallback during reconnect
      if (httpUrl && !STATE.restInterval) {
        fetchRest(httpUrl);
        STATE.restInterval = setInterval(() => fetchRest(httpUrl), 5000);
      }
      // Reconnect in 3s
      STATE.wsReconnectTimer = setTimeout(() => {
        connectWS(wsUrl, httpUrl);
      }, 3000);
    };
  } catch (e) {
    setConnectionStatus(false, 'Error');
    STATE.wsReconnectTimer = setTimeout(() => connectWS(wsUrl, httpUrl), 3000);
  }
}

async function fetchRest(httpUrl) {
  try {
    const r = await fetch(`${httpUrl}/api/signals`);
    if (!r.ok) return;
    const data = await r.json();
    if (data.type === 'signals') handleSignalPayload(data);
  } catch (_) {}
}

// ══════════════════════════════════════════════════════════
// DEMO MODE
// ══════════════════════════════════════════════════════════
function startDemoMode() {
  stopRealMode();
  setConnectionStatus(true, 'Demo Mode');
  STATE.mode = 'DEMO';

  if (!STATE.demoEngine) {
    STATE.demoEngine = new DemoEngineManager();
  }
  // Initial render
  renderDemoSignals();
  // Advance simulator every 60s (new candle), re-render every second for countdown
  STATE.demoInterval = setInterval(() => {
    const now = new Date();
    if (now.getSeconds() === 0) {
      STATE.demoEngine.tick();
      renderDemoSignals();
    }
    updateCountdown(60 - now.getSeconds());
    updateClock();
  }, 1000);
}

function renderDemoSignals() {
  const items = STATE.demoEngine.generateAll();
  const payload = {
    type: 'signals', mode: 'DEMO',
    real_pairs: 0, demo_pairs: items.length,
    data: items,
    server_time: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
    countdown: 60 - new Date().getSeconds(),
  };
  handleSignalPayload(payload);
}

function stopDemo() {
  clearInterval(STATE.demoInterval);
  STATE.demoInterval = null;
}

function stopRealMode() {
  clearTimeout(STATE.wsReconnectTimer);
  clearInterval(STATE.restInterval);
  clearInterval(STATE.pingInterval);
  STATE.restInterval = null;
  if (STATE.wsConn) {
    try { STATE.wsConn.close(); } catch (_) {}
    STATE.wsConn = null;
  }
}

// ══════════════════════════════════════════════════════════
// SIGNAL PAYLOAD HANDLER
// ══════════════════════════════════════════════════════════
function handleSignalPayload(payload) {
  STATE.mode       = payload.mode || 'DEMO';
  STATE.serverTime = payload.server_time;
  STATE.countdown  = payload.countdown || (60 - new Date().getSeconds());

  const prev = { ...STATE.signals };

  for (const item of payload.data) {
    const old = prev[item.pair_id];
    STATE.signals[item.pair_id] = item;
    // Only add to history if signal is BUY or SELL and changed
    if (item.signal !== 'WAIT') {
      const hasChanged = !old || old.signal !== item.signal || old.confidence !== item.confidence;
      if (hasChanged) {
        STATE.history.unshift({ ...item, recorded: new Date().toISOString() });
        if (STATE.history.length > 100) STATE.history.pop();
        STATE.signalCount++;
      }
    }
  }

  renderAll();
}

// ══════════════════════════════════════════════════════════
// RENDERING
// ══════════════════════════════════════════════════════════
function renderAll() {
  updateHeader();
  updateCountdown(STATE.countdown);
  updateClock();
  renderCards();
  renderHistory();
}

function updateHeader() {
  DOM.modeBadge.textContent = STATE.mode;
  DOM.modeBadge.className   = 'mode-badge ' + (STATE.mode === 'REAL' ? 'real' : 'demo');
  const all = Object.values(STATE.signals);
  DOM.pairsCount.textContent   = all.length;
  DOM.signalsCount.textContent = STATE.signalCount;
  if (STATE.serverTime) DOM.serverTimeEl.textContent = formatTime(STATE.serverTime);
}

// ── Audio beep (Web Audio API) ────────────────────────────
let _audioCtx = null;
function _getAudioCtx() {
  if (!_audioCtx) {
    try { _audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch(_) {}
  }
  return _audioCtx;
}

/**
 * Play a short alert beep.
 * freq1 = 880 Hz (high), freq2 = 660 Hz (mid-low) — two-tone alert sound.
 */
function playAlertBeep() {
  const ctx = _getAudioCtx();
  if (!ctx) return;
  try {
    const now = ctx.currentTime;
    [[880, 0, 0.08], [660, 0.09, 0.08]].forEach(([freq, start, dur]) => {
      const osc  = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type      = 'sine';
      osc.frequency.setValueAtTime(freq, now + start);
      gain.gain.setValueAtTime(0.25, now + start);
      gain.gain.exponentialRampToValueAtTime(0.001, now + start + dur);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now + start);
      osc.stop(now  + start + dur + 0.01);
    });
  } catch(_) {}
}

// Track previous countdown so we fire the beep exactly once on 6→5 transition
let _prevCountdown = 60;

function updateCountdown(secs) {
  const prev = _prevCountdown;
  _prevCountdown = secs;
  STATE.countdown = secs;

  const pct = (secs / 60) * 100;
  DOM.countdownBar.style.width  = pct + '%';
  DOM.countdownText.textContent = `Next candle in ${secs}s`;

  // Colour shift: green → amber → red
  DOM.countdownBar.className = 'countdown-fill'
    + (secs <= 5  ? ' alert5'
    :  secs <= 10 ? ' urgent'
    :  secs <= 20 ? ' warning' : '');

  // ── 5-second pre-candle alert ─────────────────────────
  const entering5s = (prev > 5 && secs <= 5);
  const in5s       = secs <= 5;

  document.querySelectorAll('.signal-card').forEach(card => {
    const isBuyOrSell = card.classList.contains('buy') || card.classList.contains('sell');
    if (in5s && isBuyOrSell) {
      card.classList.add('alert-5s');
    } else {
      card.classList.remove('alert-5s');
    }
  });

  // Fire beep once when crossing from 6 → 5
  if (entering5s) {
    const hasActiveSignal = document.querySelector('.signal-card.buy.alert-5s, .signal-card.sell.alert-5s');
    if (hasActiveSignal) playAlertBeep();
  }
}

function updateClock() {
  DOM.liveClock.textContent = new Date().toLocaleTimeString('en-GB', { hour12: false });
}

// ── Cards ─────────────────────────────────────────────────
function renderCards() {
  const category = STATE.activeCategory;
  const filter   = STATE.activeFilter;
  let items      = Object.values(STATE.signals);

  if (category !== 'All') {
    items = items.filter(s => s.category === category);
  }
  if (filter !== 'All') {
    items = items.filter(s => s.signal === filter);
  }

  // Sort: BUY first, then SELL, then WAIT; then by confidence desc
  items.sort((a, b) => {
    const order = { BUY: 0, SELL: 1, WAIT: 2 };
    const oa = order[a.signal] ?? 3;
    const ob = order[b.signal] ?? 3;
    if (oa !== ob) return oa - ob;
    return b.confidence - a.confidence;
  });

  DOM.cardsGrid.innerHTML = items.map(buildCard).join('');

  // Update count badges in tabs
  const allItems = Object.values(STATE.signals);
  document.querySelectorAll('.tab-btn').forEach(btn => {
    const cat   = btn.dataset.category;
    const count = cat === 'All'
      ? allItems.length
      : allItems.filter(s => s.category === cat).length;
    const badge = btn.querySelector('.tab-count');
    if (badge) badge.textContent = count;
  });
}

function buildCard(s) {
  const signalClass = s.signal === 'BUY' ? 'buy' : s.signal === 'SELL' ? 'sell' : 'wait';
  const signalIcon  = s.signal === 'BUY' ? '▲' : s.signal === 'SELL' ? '▼' : '◆';
  const catClass    = s.category === 'Forex' ? 'cat-forex'
                    : s.category === 'Commodity' ? 'cat-commodity' : 'cat-crypto';
  const srcIcon     = s.data_source === 'real' ? '🟢' : '🟡';
  const srcLabel    = s.data_source === 'real' ? 'REAL' : 'DEMO';
  const priceStr    = formatPrice(s.price, s.pair_id);
  const chgSign     = s.change_pct > 0 ? '+' : '';
  const chgClass    = s.change_pct > 0 ? 'price-up' : s.change_pct < 0 ? 'price-down' : '';
  const chgArrow    = s.change_pct > 0 ? '↑' : s.change_pct < 0 ? '↓' : '';
  const reasons     = (s.reason || '').split(' | ').slice(0, 3);

  return `
<div class="signal-card ${signalClass}" data-pair="${s.pair_id}">
  <div class="card-header">
    <div class="card-left">
      <span class="pair-name">${s.pair_name}</span>
      <span class="cat-badge ${catClass}">${s.category}</span>
    </div>
    <div class="signal-badge ${signalClass}">
      <span class="signal-icon">${signalIcon}</span>
      <span class="signal-text">${s.signal}</span>
    </div>
  </div>

  <div class="confidence-row">
    <span class="conf-label">Confidence</span>
    <span class="conf-value">${s.confidence}%</span>
  </div>
  <div class="confidence-track">
    <div class="confidence-bar ${signalClass}" style="width:${s.confidence}%"></div>
  </div>
  <div class="vote-row">
    <span class="vote-bull">▲ ${s.bull_pct ?? 0}%</span>
    <span class="vote-bear">▼ ${s.bear_pct ?? 0}%</span>
  </div>

  <div class="price-row">
    <span class="price-value">${priceStr}</span>
    <span class="price-change ${chgClass}">${chgArrow} ${chgSign}${(s.change_pct || 0).toFixed(3)}%</span>
  </div>

  <div class="reasons-list">
    ${reasons.map(r => `<div class="reason-tag">${r}</div>`).join('')}
  </div>

  <div class="card-footer">
    <span class="data-source-badge">${srcIcon} ${srcLabel}</span>
    <span class="payout-badge">Payout <strong>${s.payout}%</strong></span>
    <span class="candles-info">${s.candles || 0} candles</span>
  </div>
</div>`;
}

// ── History ───────────────────────────────────────────────
function renderHistory() {
  DOM.historyCount.textContent = STATE.history.length;
  if (!STATE.history.length) {
    DOM.historyBody.innerHTML = `<tr><td colspan="8" class="no-data">No signals yet — waiting for BUY or SELL…</td></tr>`;
    return;
  }
  DOM.historyBody.innerHTML = STATE.history.slice(0, 100).map(s => {
    const signalClass = s.signal === 'BUY' ? 'buy' : 'sell';
    const icon        = s.signal === 'BUY' ? '▲' : '▼';
    return `
<tr>
  <td class="mono">${formatTime(s.ts)}</td>
  <td><strong>${s.pair_name}</strong></td>
  <td><span class="cat-badge cat-${s.category.toLowerCase()}">${s.category}</span></td>
  <td><span class="signal-badge-sm ${signalClass}">${icon} ${s.signal}</span></td>
  <td>${s.confidence}%</td>
  <td>${s.payout}%</td>
  <td class="mono">${formatPrice(s.price, s.pair_id)}</td>
  <td class="reason-cell" title="${s.reason}">${(s.reason || '').substring(0, 40)}…</td>
</tr>`;
  }).join('');
}

// ══════════════════════════════════════════════════════════
// UI INTERACTIONS
// ══════════════════════════════════════════════════════════
function initTabs() {
  const tabs = [
    { id:'All',       label:'All',       count: 52 },
    { id:'Forex',     label:'Forex',     count: 42 },
    { id:'Commodity', label:'Commodity', count:  4 },
    { id:'Crypto',    label:'Crypto',    count:  6 },
  ];
  DOM.categoryTabs.innerHTML = tabs.map(t => `
<button class="tab-btn ${t.id === 'All' ? 'active' : ''}" data-category="${t.id}">
  ${t.label} <span class="tab-count">${t.count}</span>
</button>`).join('');

  DOM.categoryTabs.addEventListener('click', e => {
    const btn = e.target.closest('.tab-btn');
    if (!btn) return;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    STATE.activeCategory = btn.dataset.category;
    renderCards();
  });
}

function initFilterButtons() {
  const filters = [
    { id:'All',  label:'All Signals' },
    { id:'BUY',  label:'▲ BUY Only'  },
    { id:'SELL', label:'▼ SELL Only' },
  ];
  DOM.filterBtns.innerHTML = filters.map(f => `
<button class="filter-btn ${f.id === 'All' ? 'active' : ''}" data-filter="${f.id}">
  ${f.label}
</button>`).join('');

  DOM.filterBtns.addEventListener('click', e => {
    const btn = e.target.closest('.filter-btn');
    if (!btn) return;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    STATE.activeFilter = btn.dataset.filter;
    renderCards();
  });
}

// ══════════════════════════════════════════════════════════
// CONNECTION STATUS
// ══════════════════════════════════════════════════════════
function setConnectionStatus(connected, label) {
  STATE.connected = connected;
  DOM.connectionDot.className    = 'conn-dot ' + (connected ? 'connected' : 'disconnected');
  DOM.connectionLabel.textContent = label;
}

// ══════════════════════════════════════════════════════════
// HELPERS
// ══════════════════════════════════════════════════════════
const _JPY_PAIRS_JS   = new Set(['USDJPY_otc','EURJPY_otc','GBPJPY_otc','CHFJPY_otc','CADJPY_otc']);
const _LARGE_PAIRS_JS = new Set(['BTCUSD_otc','ETHUSD_otc','XAUUSD_otc']);
const _MID_PAIRS_JS   = new Set(['XAGUSD_otc','UKOIL_otc','USOIL_otc']);

function formatPrice(price, pairId) {
  if (price == null || isNaN(price)) return '—';
  if (_LARGE_PAIRS_JS.has(pairId))  return price.toFixed(2);
  if (_MID_PAIRS_JS.has(pairId))    return price.toFixed(3);
  if (_JPY_PAIRS_JS.has(pairId))    return price.toFixed(3);
  return price.toFixed(5);
}

function formatTime(isoStr) {
  if (!isoStr) return '';
  try {
    return new Date(isoStr).toLocaleTimeString('en-GB', { hour12: false });
  } catch (_) { return isoStr; }
}

// Countdown tick — runs every second independently of WS
function startCountdownTick() {
  setInterval(() => {
    const secs = 60 - new Date().getSeconds();
    updateCountdown(secs);
    updateClock();
  }, 1000);
}

// ══════════════════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════════════════
// Hard-coded Railway backend — auto-connects on every load, no modal needed
const RAILWAY_URL = 'https://qx-otc-signal-bot-production.up.railway.app';

document.addEventListener('DOMContentLoaded', () => {
  cacheDOM();
  initTabs();
  initFilterButtons();
  startCountdownTick();

  DOM.connectBtn.addEventListener('click', onConnectClick);
  DOM.demoBtn.addEventListener('click',    onDemoClick);

  DOM.backendInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') onConnectClick();
  });

  // Render empty grid placeholder immediately
  renderCards();

  // Auto-connect to Railway — skip modal entirely
  STATE.backendUrl = RAILWAY_URL;
  DOM.backendInput.value = RAILWAY_URL;
  hideModal();
  startRealMode(RAILWAY_URL);
});
