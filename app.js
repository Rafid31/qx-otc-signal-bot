/**
 * app.js — Frontend controller
 * Connects to Railway WebSocket backend (or runs locally with engine.js in demo mode).
 */

// ── OTC Pairs config ─────────────────────────────────────────────────────────
const OTC_PAIRS = [
  { id: 'EURUSD_otc', name: 'EUR/USD OTC', basePrice: 1.08500 },
  { id: 'GBPUSD_otc', name: 'GBP/USD OTC', basePrice: 1.26500 },
  { id: 'USDJPY_otc', name: 'USD/JPY OTC', basePrice: 149.500 },
  { id: 'AUDUSD_otc', name: 'AUD/USD OTC', basePrice: 0.65200 },
  { id: 'EURJPY_otc', name: 'EUR/JPY OTC', basePrice: 161.800 },
  { id: 'USDCAD_otc', name: 'USD/CAD OTC', basePrice: 1.36500 },
];

// ── State ─────────────────────────────────────────────────────────────────────
let ws             = null;
let backendUrl     = '';
let demoMode       = false;
let signalHistory  = [];
let totalSignals   = 0;
let correctSignals = 0;
let demoInterval   = null;
let countdownVal   = 60;

const demoEngines = {};
const demoSims    = {};
const prevPrices  = {};

// ── DOM refs ─────────────────────────────────────────────────────────────────
const setupModal    = document.getElementById('setup-modal');
const app           = document.getElementById('app');
const backendInput  = document.getElementById('backend-url');
const connectBtn    = document.getElementById('connect-btn');
const demoBtnEl     = document.getElementById('demo-btn');
const settingsBtn   = document.getElementById('settings-btn');
const modeBadge     = document.getElementById('mode-badge');
const connStatus    = document.getElementById('conn-status');
const signalGrid    = document.getElementById('signal-grid');
const historyBody   = document.getElementById('history-body');
const totalEl       = document.getElementById('total-signals');
const accuracyEl    = document.getElementById('accuracy-val');
const countdownEl   = document.getElementById('countdown');
const countdownFill = document.getElementById('countdown-fill');
const serverTimeEl  = document.getElementById('server-time');
const clearHistBtn  = document.getElementById('clear-history');

// ── Auto-connect to Railway backend ─────────────────────────────────────────
const RAILWAY_URL = 'https://qx-otc-signal-bot-production.up.railway.app';

// ── Setup handlers ────────────────────────────────────────────────────────────
connectBtn.addEventListener('click', () => {
  const url = backendInput.value.trim().replace(/\/$/, '');
  if (!url) { backendInput.focus(); return; }
  backendUrl = url;
  startLive();
});

demoBtnEl.addEventListener('click', () => {
  demoMode = true;
  startDemo();
});

settingsBtn.addEventListener('click', () => {
  if (ws) { ws.close(); ws = null; }
  clearInterval(demoInterval);
  app.classList.add('hidden');
  setupModal.classList.remove('hidden');
});

clearHistBtn.addEventListener('click', () => {
  signalHistory = [];
  totalSignals  = 0;
  correctSignals = 0;
  renderHistory();
  updateStats();
});

backendInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') connectBtn.click();
});

// ── Launch app ────────────────────────────────────────────────────────────────
function launchApp() {
  setupModal.classList.add('hidden');
  app.classList.remove('hidden');
  renderSkeletonCards();
}

// ── Live mode (WebSocket) ─────────────────────────────────────────────────────
function startLive() {
  launchApp();
  demoMode = false;
  modeBadge.textContent = 'LIVE';
  modeBadge.classList.add('live');
  connectWS();
}

function connectWS() {
  setConnStatus(false, 'Connecting…');
  const wsUrl = backendUrl.replace(/^http/, 'ws') + '/ws';
  try {
    ws = new WebSocket(wsUrl);
  } catch (e) {
    setConnStatus(false, 'Bad URL');
    return;
  }

  ws.onopen  = () => setConnStatus(true, 'Live');
  ws.onclose = () => {
    setConnStatus(false, 'Reconnecting…');
    setTimeout(() => { if (!demoMode) connectWS(); }, 3000);
  };
  ws.onerror = () => setConnStatus(false, 'Error');

  ws.onmessage = e => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'signals') handleSignalBatch(msg.data, msg.server_time);
    } catch (_) {}
  };
}

// ── Demo mode ────────────────────────────────────────────────────────────────
function startDemo() {
  launchApp();
  modeBadge.textContent = 'DEMO';
  setConnStatus(true, 'Demo');

  OTC_PAIRS.forEach(p => {
    demoEngines[p.id] = new SignalEngine(p.id);
    demoSims[p.id]    = new PriceSimulator(p.basePrice, p.id);
    prevPrices[p.id]  = p.basePrice;
    // Seed with 60 candles
    for (let i = 0; i < 60; i++) {
      const c = demoSims[p.id].nextCandle();
      demoEngines[p.id].addCandle(c.open, c.high, c.low, c.close);
    }
  });

  tickDemo();
  demoInterval = setInterval(tickDemo, 1000);
}

let demoSecond = 0;

function tickDemo() {
  demoSecond = (demoSecond + 1) % 60;
  const signals = OTC_PAIRS.map(p => {
    const eng = demoEngines[p.id];
    const sim = demoSims[p.id];

    if (demoSecond === 0) {
      const c = sim.nextCandle();
      eng.addCandle(c.open, c.high, c.low, c.close);
    }
    const sig  = eng.generateSignal();
    const price = eng.closes[eng.closes.length - 1] || p.basePrice;

    return {
      pair_id:    p.id,
      pair_name:  p.name,
      signal:     sig.signal,
      confidence: sig.confidence,
      reason:     sig.reason,
      price,
      candles:    eng.closes.length,
      mode:       'DEMO',
      countdown:  60 - demoSecond,
    };
  });

  handleSignalBatch(signals, new Date().toISOString());
}

// ── Signal handler ────────────────────────────────────────────────────────────
function handleSignalBatch(signals, serverTime) {
  if (serverTime) updateServerTime(serverTime);
  signals.forEach(s => renderCard(s));
  // Add to history only on minute boundary (countdown near 60) for live,
  // or on every candle close (demoSecond === 0) for demo
  const onBoundary = demoMode ? demoSecond === 1 : signals[0]?.countdown >= 58;
  if (onBoundary) {
    signals.forEach(s => {
      if (s.signal !== 'WAIT') {
        signalHistory.unshift({ ...s, ts: serverTime || new Date().toISOString() });
        if (signalHistory.length > 100) signalHistory.pop();
        totalSignals++;
      }
    });
    renderHistory();
    updateStats();
  }
  updateCountdown(signals[0]?.countdown || 60);
}

// ── Card rendering ─────────────────────────────────────────────────────────────
function renderSkeletonCards() {
  signalGrid.innerHTML = OTC_PAIRS.map(p => `
    <div class="signal-card" id="card-${p.id}">
      <div class="card-header">
        <div class="pair-info">
          <div class="pair-name">${p.name}</div>
          <div class="candle-count skeleton" style="width:80px;height:12px;margin-top:4px"></div>
        </div>
        <div class="skeleton" style="width:72px;height:30px;border-radius:999px"></div>
      </div>
      <div class="skeleton" style="height:6px;border-radius:999px"></div>
      <div class="skeleton" style="height:20px;width:50%"></div>
      <div class="skeleton" style="height:40px"></div>
    </div>
  `).join('');
}

function renderCard(data) {
  const { pair_id, pair_name, signal, confidence, reason, price, candles } = data;
  let card = document.getElementById(`card-${pair_id}`);
  if (!card) {
    card = document.createElement('div');
    card.id = `card-${pair_id}`;
    signalGrid.appendChild(card);
  }

  const prev  = prevPrices[pair_id] ?? price;
  const diff  = price - prev;
  const dp    = pair_id.includes('JPY') ? 3 : 5;
  const chDir = diff >= 0 ? 'up' : 'down';
  const chSym = diff >= 0 ? '▲' : '▼';
  prevPrices[pair_id] = price;

  const icon = signal === 'BUY' ? '▲' : signal === 'SELL' ? '▼' : '◆';

  card.className = `signal-card ${signal.toLowerCase()}`;
  card.innerHTML = `
    <div class="card-header">
      <div class="pair-info">
        <div class="pair-name">${pair_name}</div>
        <div class="candle-count">${candles} candles</div>
      </div>
      <div class="signal-badge ${signal}">
        <span class="signal-icon">${icon}</span> ${signal}
      </div>
    </div>

    <div class="confidence-row">
      <span class="confidence-label">Confidence</span>
      <div class="confidence-track">
        <div class="confidence-fill ${signal}" style="width:${confidence}%"></div>
      </div>
      <span class="confidence-val">${confidence}%</span>
    </div>

    <div class="price-row">
      <span class="price-val">${price.toFixed(dp)}</span>
      <span class="price-change ${chDir}">${chSym} ${Math.abs(diff).toFixed(dp)}</span>
    </div>

    <div class="reason-row">${reason}</div>
  `;
}

// ── History table ─────────────────────────────────────────────────────────────
function renderHistory() {
  if (!signalHistory.length) {
    historyBody.innerHTML = `<tr class="empty-row"><td colspan="6">No signals yet — waiting for data…</td></tr>`;
    return;
  }
  historyBody.innerHTML = signalHistory.slice(0, 50).map(s => {
    const dp   = s.pair_id?.includes('JPY') ? 3 : 5;
    const time = new Date(s.ts).toLocaleTimeString();
    return `
      <tr>
        <td>${time}</td>
        <td>${s.pair_name}</td>
        <td><span class="sig-tag ${s.signal}">${s.signal}</span></td>
        <td>
          <div class="conf-bar ${s.signal}">
            <div class="conf-mini"><div class="conf-mini-fill" style="width:${s.confidence}%"></div></div>
            <span class="conf-num">${s.confidence}%</span>
          </div>
        </td>
        <td style="font-family:var(--font-mono)">${(+s.price).toFixed(dp)}</td>
        <td style="color:var(--text-muted);max-width:260px;overflow:hidden;text-overflow:ellipsis">${s.reason}</td>
      </tr>
    `;
  }).join('');
}

// ── Stats ─────────────────────────────────────────────────────────────────────
function updateStats() {
  totalEl.textContent    = totalSignals;
  accuracyEl.textContent = totalSignals > 0 ? `${Math.round((correctSignals / totalSignals) * 100)}%` : '—';
}

// ── Countdown ────────────────────────────────────────────────────────────────
function updateCountdown(seconds) {
  countdownVal = seconds;
  if (countdownEl) countdownEl.textContent = `${seconds}s`;
  if (countdownFill) countdownFill.style.width = `${(seconds / 60) * 100}%`;
}

// ── Server time ───────────────────────────────────────────────────────────────
function updateServerTime(iso) {
  try {
    const d = new Date(iso);
    if (serverTimeEl) serverTimeEl.textContent = d.toLocaleTimeString();
  } catch (_) {}
}

// ── Connection badge ──────────────────────────────────────────────────────────
function setConnStatus(connected, label) {
  connStatus.className = `conn-badge ${connected ? 'connected' : 'disconnected'}`;
  connStatus.innerHTML = `<span class="conn-dot"></span><span>${label}</span>`;
}

// ── Auto-fill Railway URL on load ────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  if (backendInput) backendInput.value = RAILWAY_URL;
});

// ── Local clock (updates every second) ───────────────────────────────────────
setInterval(() => {
  if (serverTimeEl && demoMode) {
    serverTimeEl.textContent = new Date().toLocaleTimeString();
  }
}, 1000);
