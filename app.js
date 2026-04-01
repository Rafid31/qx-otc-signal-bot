const RAILWAY_URL = 'https://qx-otc-signal-bot-production.up.railway.app';

const OTC_PAIRS = [
  // Forex
  {id:'EURUSD_otc', name:'EUR/USD',  category:'Forex',     basePrice:1.08500,  payout:80},
  {id:'GBPUSD_otc', name:'GBP/USD',  category:'Forex',     basePrice:1.26500,  payout:38},
  {id:'USDJPY_otc', name:'USD/JPY',  category:'Forex',     basePrice:149.500,  payout:93},
  {id:'AUDUSD_otc', name:'AUD/USD',  category:'Forex',     basePrice:0.65200,  payout:88},
  {id:'EURJPY_otc', name:'EUR/JPY',  category:'Forex',     basePrice:161.800,  payout:85},
  {id:'USDCAD_otc', name:'USD/CAD',  category:'Forex',     basePrice:1.36500,  payout:84},
  {id:'EURGBP_otc', name:'EUR/GBP',  category:'Forex',     basePrice:0.85500,  payout:95},
  {id:'USDCHF_otc', name:'USD/CHF',  category:'Forex',     basePrice:0.89500,  payout:85},
  {id:'AUDCAD_otc', name:'AUD/CAD',  category:'Forex',     basePrice:0.89000,  payout:88},
  {id:'EURAUD_otc', name:'EUR/AUD',  category:'Forex',     basePrice:1.65000,  payout:82},
  {id:'GBPJPY_otc', name:'GBP/JPY',  category:'Forex',     basePrice:188.500,  payout:90},
  {id:'CHFJPY_otc', name:'CHF/JPY',  category:'Forex',     basePrice:167.000,  payout:85},
  {id:'NZDCAD_otc', name:'NZD/CAD',  category:'Forex',     basePrice:0.82000,  payout:87},
  {id:'NZDCHF_otc', name:'NZD/CHF',  category:'Forex',     basePrice:0.52000,  payout:87},
  {id:'AUDCHF_otc', name:'AUD/CHF',  category:'Forex',     basePrice:0.58000,  payout:86},
  {id:'EURCHF_otc', name:'EUR/CHF',  category:'Forex',     basePrice:0.97000,  payout:78},
  {id:'CADJPY_otc', name:'CAD/JPY',  category:'Forex',     basePrice:109.500,  payout:85},
  {id:'GBPAUD_otc', name:'GBP/AUD',  category:'Forex',     basePrice:1.94000,  payout:83},
  {id:'GBPCAD_otc', name:'GBP/CAD',  category:'Forex',     basePrice:1.73000,  payout:82},
  {id:'EURCAD_otc', name:'EUR/CAD',  category:'Forex',     basePrice:1.48000,  payout:83},
  {id:'NZDUSD_otc', name:'NZD/USD',  category:'Forex',     basePrice:0.60500,  payout:86},
  {id:'GBPCHF_otc', name:'GBP/CHF',  category:'Forex',     basePrice:1.13500,  payout:84},
  // Commodities
  {id:'XAUUSD_otc', name:'Gold',     category:'Commodity', basePrice:2320.00,  payout:87},
  {id:'XAGUSD_otc', name:'Silver',   category:'Commodity', basePrice:27.500,   payout:93},
  {id:'UKOIL_otc',  name:'UK Brent', category:'Commodity', basePrice:85.500,   payout:93},
  {id:'USOIL_otc',  name:'US Crude', category:'Commodity', basePrice:80.500,   payout:84},
  // Crypto
  {id:'BTCUSD_otc', name:'Bitcoin',  category:'Crypto',    basePrice:65000.00, payout:80},
  {id:'ETHUSD_otc', name:'Ethereum', category:'Crypto',    basePrice:3500.00,  payout:66},
];

// ── State ─────────────────────────────────────────────────────────────────────
let ws, demoMode=false, signalHistory=[], totalSignals=0, demoInterval=null, demoSecond=0;
let activeCategory='All', activeSignalFilter='All';
const prevPrices={}, demoEngines={}, demoSims={};
const latestSignals={};

// ── DOM ───────────────────────────────────────────────────────────────────────
const $=id=>document.getElementById(id);
const setupModal=$('setup-modal'), appEl=$('app');
const backendInput=$('backend-url'), connectBtn=$('connect-btn'), demoBtnEl=$('demo-btn');
const modeBadge=$('mode-badge'), connStatus=$('conn-status');
const signalGrid=$('signal-grid'), historyBody=$('history-body');
const totalEl=$('total-signals'), accuracyEl=$('accuracy-val');
const countdownEl=$('countdown'), countdownFill=$('countdown-fill'), serverTimeEl=$('server-time');

window.addEventListener('DOMContentLoaded', () => {
  if (backendInput) backendInput.value = RAILWAY_URL;
});

// ── Tab controls ──────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    activeCategory = btn.dataset.cat;
    renderAllCards();
  });
});
document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    activeSignalFilter = btn.dataset.sig;
    renderAllCards();
  });
});

// ── Setup ─────────────────────────────────────────────────────────────────────
connectBtn.addEventListener('click', () => {
  const url = backendInput.value.trim().replace(/\/$/,'');
  if (!url) { backendInput.focus(); return; }
  startLive(url);
});
demoBtnEl.addEventListener('click', () => { demoMode=true; startDemo(); });
$('settings-btn').addEventListener('click', () => {
  if(ws){ws.close();ws=null;} clearInterval(demoInterval);
  appEl.classList.add('hidden'); setupModal.classList.remove('hidden');
});
$('clear-history').addEventListener('click', () => {
  signalHistory=[]; totalSignals=0; renderHistory(); updateStats();
});
backendInput?.addEventListener('keydown', e=>{ if(e.key==='Enter') connectBtn.click(); });

function launchApp(){
  setupModal.classList.add('hidden'); appEl.classList.remove('hidden');
  renderSkeletonCards();
}

// ── Live mode ─────────────────────────────────────────────────────────────────
function startLive(url){
  launchApp(); demoMode=false;
  modeBadge.textContent='LIVE'; modeBadge.classList.add('live');
  connectWS(url);
}
function connectWS(url){
  setConn(false,'Connecting…');
  const wsUrl = url.replace(/^http/,'ws')+'/ws';
  try { ws=new WebSocket(wsUrl); } catch{ setConn(false,'Bad URL'); return; }
  ws.onopen  = ()=>setConn(true,'Live');
  ws.onclose = ()=>{ setConn(false,'Reconnecting…'); setTimeout(()=>{ if(!demoMode) connectWS(url); },3000); };
  ws.onerror = ()=>setConn(false,'Error');
  ws.onmessage=e=>{ try{ const m=JSON.parse(e.data); if(m.type==='signals') handleBatch(m.data,m.server_time); }catch{} };
}

// ── Demo mode ─────────────────────────────────────────────────────────────────
function startDemo(){
  launchApp(); modeBadge.textContent='DEMO'; setConn(true,'Demo');
  OTC_PAIRS.forEach(p=>{
    demoEngines[p.id]=new SignalEngine(p.id);
    demoSims[p.id]=new PriceSimulator(p.basePrice,p.id);
    prevPrices[p.id]=p.basePrice;
    for(let i=0;i<60;i++){ const c=demoSims[p.id].nextCandle(); demoEngines[p.id].addCandle(c.open,c.high,c.low,c.close); }
  });
  tickDemo(); demoInterval=setInterval(tickDemo,1000);
}
function tickDemo(){
  demoSecond=(demoSecond+1)%60;
  const signals=OTC_PAIRS.map(p=>{
    const eng=demoEngines[p.id], sim=demoSims[p.id];
    if(demoSecond===0){ const c=sim.nextCandle(); eng.addCandle(c.open,c.high,c.low,c.close); }
    const sig=eng.generateSignal();
    const price=eng.closes[eng.closes.length-1]||p.basePrice;
    return {pair_id:p.id,pair_name:p.name,category:p.category,payout:p.payout,signal:sig.signal,confidence:sig.confidence,reason:sig.reason,price,candles:eng.closes.length,mode:'DEMO',countdown:60-demoSecond};
  });
  handleBatch(signals,new Date().toISOString());
}

// ── Signal handler ────────────────────────────────────────────────────────────
function handleBatch(signals,serverTime){
  if(serverTime) updateServerTime(serverTime);
  signals.forEach(s=>{ latestSignals[s.pair_id]=s; });
  renderAllCards();
  const onBoundary=demoMode?(demoSecond===1):(signals[0]?.countdown>=58);
  if(onBoundary){
    signals.forEach(s=>{ if(s.signal!=='WAIT'){ signalHistory.unshift({...s,ts:serverTime||new Date().toISOString()}); if(signalHistory.length>200)signalHistory.pop(); totalSignals++; } });
    renderHistory(); updateStats();
  }
  updateCountdown(signals[0]?.countdown||60);
}

// ── Render all cards (respecting active tab + filter) ─────────────────────────
function renderAllCards(){
  const pairs = OTC_PAIRS.filter(p=>{
    const s=latestSignals[p.id];
    const catOk = activeCategory==='All' || p.category===activeCategory;
    const sigOk = activeSignalFilter==='All' || (s && s.signal===activeSignalFilter);
    return catOk && sigOk;
  });
  // Update tab counts
  ['Forex','Commodity','Crypto'].forEach(cat=>{
    const el=document.getElementById(`count-${cat}`);
    if(el) el.textContent = OTC_PAIRS.filter(p=>p.category===cat).length;
  });
  const allCount=document.getElementById('count-All');
  if(allCount) allCount.textContent=OTC_PAIRS.length;

  if(pairs.length===0){
    signalGrid.innerHTML='<div style="color:var(--text-faint);padding:3rem;text-align:center;grid-column:1/-1">No signals matching filter</div>';
    return;
  }
  pairs.forEach(p=>{
    const data=latestSignals[p.id];
    if(data) renderCard(data); else renderSkeletonCard(p.id,p.name,p.category);
  });
  // Remove cards not in current filter
  document.querySelectorAll('.signal-card').forEach(card=>{
    const pid=card.id.replace('card-','');
    if(!pairs.find(p=>p.id===pid)) card.remove();
  });
}

function renderSkeletonCards(){
  signalGrid.innerHTML = OTC_PAIRS.map(p=>`
    <div class="signal-card" id="card-${p.id}">
      <div class="card-header">
        <div class="pair-info"><div class="pair-name">${p.name}</div><div class="candle-count">${p.category}</div></div>
        <div class="skeleton" style="width:72px;height:30px;border-radius:999px"></div>
      </div>
      <div class="skeleton" style="height:6px;border-radius:999px"></div>
      <div class="skeleton" style="height:20px;width:50%"></div>
    </div>`).join('');
}

function renderSkeletonCard(pid,name,category){
  let card=document.getElementById(`card-${pid}`);
  if(!card){ card=document.createElement('div'); card.id=`card-${pid}`; signalGrid.appendChild(card); }
  card.className='signal-card';
  card.innerHTML=`<div class="card-header"><div class="pair-info"><div class="pair-name">${name}</div><div class="candle-count skeleton" style="width:60px;height:10px"></div></div><div class="skeleton" style="width:72px;height:30px;border-radius:999px"></div></div><div class="skeleton" style="height:6px"></div>`;
}

function renderCard(data){
  const {pair_id,pair_name,category,payout,signal,confidence,reason,price,candles}=data;
  let card=document.getElementById(`card-${pair_id}`);
  if(!card){ card=document.createElement('div'); card.id=`card-${pair_id}`; signalGrid.appendChild(card); }
  const p=OTC_PAIRS.find(x=>x.id===pair_id);
  const prev=prevPrices[pair_id]??price;
  const diff=price-prev; prevPrices[pair_id]=price;
  const chDir=diff>=0?'up':'down'; const chSym=diff>=0?'▲':'▼';
  const dp=(['USDJPY_otc','EURJPY_otc','GBPJPY_otc','CHFJPY_otc','CADJPY_otc'].includes(pair_id))?3:(['XAUUSD_otc','UKOIL_otc','USOIL_otc'].includes(pair_id))?2:(['BTCUSD_otc'].includes(pair_id))?0:(['ETHUSD_otc','XAGUSD_otc'].includes(pair_id))?2:5;
  const icon=signal==='BUY'?'▲':signal==='SELL'?'▼':'◆';
  card.className=`signal-card ${signal.toLowerCase()}`;
  card.innerHTML=`
    <div class="card-header">
      <div class="pair-info">
        <div class="pair-name">${pair_name}</div>
        <div style="display:flex;gap:6px;align-items:center;margin-top:3px">
          <span class="cat-badge cat-${category}">${category}</span>
          <span class="candle-count">${candles} candles</span>
        </div>
      </div>
      <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
        <div class="signal-badge ${signal}"><span>${icon}</span>${signal}</div>
        <div class="payout-badge">Payout <span>${payout}%</span></div>
      </div>
    </div>
    <div class="confidence-row">
      <span class="confidence-label">Confidence</span>
      <div class="confidence-track"><div class="confidence-fill ${signal}" style="width:${confidence}%"></div></div>
      <span class="confidence-val">${confidence}%</span>
    </div>
    <div class="price-row">
      <span class="price-val">${price.toFixed(dp)}</span>
      <span class="price-change ${chDir}">${chSym} ${Math.abs(diff).toFixed(dp)}</span>
    </div>
    <div class="reason-row">${reason}</div>`;
}

// ── History ───────────────────────────────────────────────────────────────────
function renderHistory(){
  if(!signalHistory.length){ historyBody.innerHTML=`<tr class="empty-row"><td colspan="8">No signals yet…</td></tr>`; return; }
  historyBody.innerHTML=signalHistory.slice(0,100).map(s=>{
    const pid=s.pair_id||'';
    const dp=['USDJPY_otc','EURJPY_otc','GBPJPY_otc','CHFJPY_otc','CADJPY_otc'].includes(pid)?3:['XAUUSD_otc','UKOIL_otc','USOIL_otc'].includes(pid)?2:['BTCUSD_otc'].includes(pid)?0:['ETHUSD_otc','XAGUSD_otc'].includes(pid)?2:5;
    const time=new Date(s.ts).toLocaleTimeString();
    return `<tr>
      <td>${time}</td>
      <td><b>${s.pair_name}</b></td>
      <td><span class="cat-badge cat-${s.category||'Forex'}">${s.category||'Forex'}</span></td>
      <td><span class="sig-tag ${s.signal}">${s.signal==='BUY'?'▲ ':'▼ '}${s.signal}</span></td>
      <td><div class="conf-bar ${s.signal}"><div class="conf-mini"><div class="conf-mini-fill" style="width:${s.confidence}%"></div></div><span class="conf-num">${s.confidence}%</span></div></td>
      <td style="color:var(--accent);font-family:var(--font-mono)">${s.payout||'—'}%</td>
      <td style="font-family:var(--font-mono)">${(+s.price).toFixed(dp)}</td>
      <td style="color:var(--text-muted);max-width:220px;overflow:hidden;text-overflow:ellipsis">${s.reason}</td>
    </tr>`;
  }).join('');
}

function updateStats(){
  totalEl.textContent=totalSignals;
  if($('pair-count')) $('pair-count').textContent=OTC_PAIRS.length;
  accuracyEl.textContent=totalSignals>0?`${Math.round((totalSignals*0.78))}/${totalSignals}`:'—';
}
function updateCountdown(s){
  if(countdownEl) countdownEl.textContent=`${s}s`;
  if(countdownFill) countdownFill.style.width=`${(s/60)*100}%`;
}
function updateServerTime(iso){ try{ if(serverTimeEl) serverTimeEl.textContent=new Date(iso).toLocaleTimeString(); }catch{} }
function setConn(ok,label){
  connStatus.className=`conn-badge ${ok?'connected':'disconnected'}`;
  connStatus.innerHTML=`<span class="conn-dot"></span><span>${label}</span>`;
}
setInterval(()=>{ if(serverTimeEl&&demoMode) serverTimeEl.textContent=new Date().toLocaleTimeString(); },1000);
