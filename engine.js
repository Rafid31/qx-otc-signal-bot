/**
 * QX OTC Signal Bot — JavaScript Signal Engine v2.0.0
 * =====================================================
 * Complete JS mirror of the Python backend algorithm.
 * Used as the demo/offline fallback when WebSocket is unavailable.
 *
 * Contains:
 *   - PriceSimulator  : realistic OTC OHLC price generator per pair type
 *   - SignalEngine    : 6-indicator weighted voting algorithm
 *   - DEMO_PAIRS      : all 28 pairs with base prices and metadata
 */

'use strict';

// ══════════════════════════════════════════════════════════
// ALL 28 QX OTC PAIRS
// ══════════════════════════════════════════════════════════
const DEMO_PAIRS = {
  // ── FOREX OTC ──────────────────────────────────────────
  EURUSD_otc: { name:'EUR/USD OTC',  category:'Forex',     payout:80,  base:1.0850,   pip:0.0001  },
  GBPUSD_otc: { name:'GBP/USD OTC',  category:'Forex',     payout:38,  base:1.2650,   pip:0.0001  },
  USDJPY_otc: { name:'USD/JPY OTC',  category:'Forex',     payout:93,  base:151.50,   pip:0.05    },
  AUDUSD_otc: { name:'AUD/USD OTC',  category:'Forex',     payout:88,  base:0.6520,   pip:0.0001  },
  EURJPY_otc: { name:'EUR/JPY OTC',  category:'Forex',     payout:85,  base:164.50,   pip:0.05    },
  USDCAD_otc: { name:'USD/CAD OTC',  category:'Forex',     payout:84,  base:1.3600,   pip:0.0001  },
  EURGBP_otc: { name:'EUR/GBP OTC',  category:'Forex',     payout:95,  base:0.8580,   pip:0.0001  },
  USDCHF_otc: { name:'USD/CHF OTC',  category:'Forex',     payout:85,  base:0.9050,   pip:0.0001  },
  AUDCAD_otc: { name:'AUD/CAD OTC',  category:'Forex',     payout:88,  base:0.8950,   pip:0.0001  },
  EURAUD_otc: { name:'EUR/AUD OTC',  category:'Forex',     payout:82,  base:1.6620,   pip:0.0001  },
  GBPJPY_otc: { name:'GBP/JPY OTC',  category:'Forex',     payout:90,  base:191.80,   pip:0.05    },
  CHFJPY_otc: { name:'CHF/JPY OTC',  category:'Forex',     payout:85,  base:167.40,   pip:0.05    },
  NZDCAD_otc: { name:'NZD/CAD OTC',  category:'Forex',     payout:87,  base:0.8230,   pip:0.0001  },
  NZDCHF_otc: { name:'NZD/CHF OTC',  category:'Forex',     payout:87,  base:0.5620,   pip:0.0001  },
  AUDCHF_otc: { name:'AUD/CHF OTC',  category:'Forex',     payout:86,  base:0.5900,   pip:0.0001  },
  EURCHF_otc: { name:'EUR/CHF OTC',  category:'Forex',     payout:78,  base:0.9780,   pip:0.0001  },
  CADJPY_otc: { name:'CAD/JPY OTC',  category:'Forex',     payout:85,  base:111.40,   pip:0.05    },
  GBPAUD_otc: { name:'GBP/AUD OTC',  category:'Forex',     payout:83,  base:1.9380,   pip:0.0001  },
  GBPCAD_otc: { name:'GBP/CAD OTC',  category:'Forex',     payout:82,  base:1.7180,   pip:0.0001  },
  EURCAD_otc: { name:'EUR/CAD OTC',  category:'Forex',     payout:83,  base:1.4760,   pip:0.0001  },
  NZDUSD_otc: { name:'NZD/USD OTC',  category:'Forex',     payout:86,  base:0.6050,   pip:0.0001  },
  GBPCHF_otc: { name:'GBP/CHF OTC',  category:'Forex',     payout:84,  base:1.1450,   pip:0.0001  },
  // ── COMMODITIES OTC ────────────────────────────────────
  XAUUSD_otc: { name:'Gold OTC',      category:'Commodity', payout:87,  base:2320.0,   pip:0.5     },
  XAGUSD_otc: { name:'Silver OTC',    category:'Commodity', payout:93,  base:27.50,    pip:0.01    },
  UKOIL_otc:  { name:'UK Brent OTC',  category:'Commodity', payout:93,  base:84.50,    pip:0.02    },
  USOIL_otc:  { name:'US Crude OTC',  category:'Commodity', payout:84,  base:81.20,    pip:0.02    },
  // ── CRYPTO OTC ─────────────────────────────────────────
  BTCUSD_otc: { name:'Bitcoin OTC',   category:'Crypto',    payout:80,  base:65000.0,  pip:10.0    },
  ETHUSD_otc: { name:'Ethereum OTC',  category:'Crypto',    payout:66,  base:3200.0,   pip:0.5     },
};

// ══════════════════════════════════════════════════════════
// UTILITIES
// ══════════════════════════════════════════════════════════

/** Seeded pseudo-random number generator (mulberry32) */
function _seededRng(seed) {
  let s = seed;
  return function () {
    s |= 0; s = s + 0x6D2B79F5 | 0;
    let t = Math.imul(s ^ s >>> 15, 1 | s);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

/** Box-Muller normal distribution */
function _randNorm(rng, mean = 0, std = 1) {
  const u1 = Math.max(1e-15, rng());
  const u2 = rng();
  const z  = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
  return mean + std * z;
}

/** Unique integer seed from a pair ID string */
function _pairSeed(pairId) {
  return pairId.split('').reduce((acc, c) => (acc * 31 + c.charCodeAt(0)) & 0x7fffffff, 0) || 1;
}

// ══════════════════════════════════════════════════════════
// PRICE SIMULATOR
// Generates realistic OHLC candles per pair type.
// Used ONLY in demo/offline mode.
// ══════════════════════════════════════════════════════════
class PriceSimulator {
  /**
   * @param {string} pairId  - e.g. "EURUSD_otc"
   * @param {number} count   - how many historical candles to seed
   */
  constructor(pairId, count = 90) {
    const cfg   = DEMO_PAIRS[pairId];
    this.pairId = pairId;
    this.pip    = cfg.pip;
    this.rng    = _seededRng(_pairSeed(pairId));
    // Seed historical candles
    this.candles = [];  // [{o, h, l, c}]
    let price = cfg.base;
    for (let i = 0; i < count; i++) {
      const candle = this._makeCandle(price);
      this.candles.push(candle);
      price = candle.c;
    }
  }

  _makeCandle(prevClose) {
    const pip   = this.pip;
    const move  = _randNorm(this.rng, 0, pip * 3);
    const open  = prevClose;
    const close = prevClose + move;
    const wick  = Math.abs(_randNorm(this.rng, 0, pip * 0.8));
    return {
      o: open,
      h: Math.max(open, close) + wick,
      l: Math.min(open, close) - wick,
      c: close,
    };
  }

  /** Advance simulator: add one new candle, drop oldest */
  tick() {
    const last   = this.candles[this.candles.length - 1];
    const candle = this._makeCandle(last.c);
    this.candles.push(candle);
    if (this.candles.length > 200) this.candles.shift();
    return candle;
  }

  /** Return latest close price */
  get lastPrice() {
    return this.candles[this.candles.length - 1].c;
  }
}

// ══════════════════════════════════════════════════════════
// TECHNICAL INDICATOR FUNCTIONS
// ══════════════════════════════════════════════════════════

/**
 * RSI using Wilder's exponential smoothing.
 * @param {number[]} closes
 * @param {number}   period
 * @returns {number} RSI value (0-100), or NaN
 */
function calcRSI(closes, period = 14) {
  if (closes.length < period + 1) return NaN;
  const alpha = 1 / period;
  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1];
    if (d > 0) avgGain += d; else avgLoss -= d;
  }
  avgGain /= period;
  avgLoss /= period;
  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    const g = d > 0 ? d : 0;
    const l = d < 0 ? -d : 0;
    avgGain = avgGain * (1 - alpha) + g * alpha;
    avgLoss = avgLoss * (1 - alpha) + l * alpha;
  }
  if (avgLoss === 0) return 100;
  return 100 - (100 / (1 + avgGain / avgLoss));
}

/**
 * Bollinger Bands.
 * @returns {{ upper, mid, lower }} — or null if not enough data
 */
function calcBollinger(closes, period = 20, nStd = 2) {
  if (closes.length < period) return null;
  const slice = closes.slice(-period);
  const mean  = slice.reduce((a, b) => a + b, 0) / period;
  const vari  = slice.reduce((a, b) => a + (b - mean) ** 2, 0) / period;
  const std   = Math.sqrt(vari);
  return { upper: mean + nStd * std, mid: mean, lower: mean - nStd * std };
}

/**
 * Stochastic Oscillator (%K, %D).
 * @param {{ h, l, c }[]} candles
 * @returns {{ k, d }} or null
 */
function calcStochastic(candles, kPeriod = 14, dPeriod = 3) {
  if (candles.length < kPeriod + dPeriod - 1) return null;
  // Compute rolling %K
  const kArr = [];
  for (let i = kPeriod - 1; i < candles.length; i++) {
    const slice  = candles.slice(i - kPeriod + 1, i + 1);
    const hi     = Math.max(...slice.map(c => c.h));
    const lo     = Math.min(...slice.map(c => c.l));
    const denom  = hi - lo;
    kArr.push(denom === 0 ? 50 : 100 * (candles[i].c - lo) / denom);
  }
  if (kArr.length < dPeriod) return null;
  const k = kArr[kArr.length - 1];
  const d = kArr.slice(-dPeriod).reduce((a, b) => a + b, 0) / dPeriod;
  return { k, d };
}

/**
 * EMA — exponential moving average.
 * @param {number[]} values
 * @param {number}   period
 * @returns {number} latest EMA value, or NaN
 */
function calcEMA(values, period) {
  if (values.length < period) return NaN;
  const k   = 2 / (period + 1);
  let ema   = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
  for (let i = period; i < values.length; i++) {
    ema = values[i] * k + ema * (1 - k);
  }
  return ema;
}

/**
 * MACD (12,26,9).
 * @returns {{ macdLine, signalLine, histogram }} or null
 */
function calcMACD(closes, fast = 12, slow = 26, signal = 9) {
  if (closes.length < slow + signal) return null;
  // Build full EMA arrays using incremental method
  function emaArray(arr, period) {
    const k = 2 / (period + 1);
    const out = new Array(arr.length).fill(NaN);
    let ema = arr.slice(0, period).reduce((a, b) => a + b, 0) / period;
    out[period - 1] = ema;
    for (let i = period; i < arr.length; i++) {
      ema = arr[i] * k + ema * (1 - k);
      out[i] = ema;
    }
    return out;
  }
  const emaFast = emaArray(closes, fast);
  const emaSlow = emaArray(closes, slow);
  const macdArr = emaFast.map((v, i) =>
    isNaN(v) || isNaN(emaSlow[i]) ? NaN : v - emaSlow[i]
  );
  // Signal line = EMA(signal) of macdArr, starting after first valid MACD
  const validStart = slow - 1;
  const macdValid  = macdArr.slice(validStart);
  if (macdValid.length < signal) return null;
  const sigK = 2 / (signal + 1);
  let sigEma = macdValid.slice(0, signal).reduce((a, b) => a + b, 0) / signal;
  for (let i = signal; i < macdValid.length; i++) {
    sigEma = macdValid[i] * sigK + sigEma * (1 - sigK);
  }
  const macdLine   = macdValid[macdValid.length - 1];
  const prevMacd   = macdValid[macdValid.length - 2] ?? macdLine;
  const histogram  = macdLine - sigEma;
  const prevHist   = prevMacd - sigEma; // approximate
  return { macdLine, signalLine: sigEma, histogram, prevHist };
}

/**
 * OTC Exhaustion Reversal Pattern.
 * 3 consecutive same-direction candles → expect flip.
 * @param {{ o, c }[]} candles
 * @returns {'BUY'|'SELL'|null}
 */
function detectReversal(candles) {
  if (candles.length < 4) return null;
  const last3 = candles.slice(-3);
  const dirs  = last3.map(c => c.c >= c.o ? 'bull' : 'bear');
  if (dirs.every(d => d === 'bear')) return 'BUY';
  if (dirs.every(d => d === 'bull')) return 'SELL';
  return null;
}

// ══════════════════════════════════════════════════════════
// SIGNAL ENGINE
// ══════════════════════════════════════════════════════════
const TOTAL_WEIGHT  = 14.5;  // 3+3+2.5+2.5+2+1.5
const SIGNAL_THRESH = 60.0;  // % vote to fire

class SignalEngine {
  /**
   * @param {string} pairId
   */
  constructor(pairId) {
    this.pairId    = pairId;
    this.candles   = [];  // [{o, h, l, c}]
    this.minCandles = 30;
  }

  /** Add a single OHLC candle. */
  addCandle(o, h, l, c) {
    this.candles.push({ o, h, l, c });
    if (this.candles.length > 200) this.candles.shift();
  }

  /** Add array of candle objects {o,h,l,c} */
  loadCandles(arr) {
    this.candles = arr.slice(-200);
  }

  /**
   * Generate a trading signal.
   * @returns {{ signal, confidence, reason, bullPct, bearPct, price, source }}
   */
  generateSignal() {
    const empty = {
      signal:'WAIT', confidence:0, reason:'Insufficient data',
      bullPct:0, bearPct:0, price:null, source:'demo',
    };
    if (this.candles.length < this.minCandles) return empty;

    const cnds   = this.candles;
    const closes = cnds.map(c => c.c);
    const last   = closes[closes.length - 1];

    // ── 1. RSI (14) weight 3.0 ────────────────────────────
    const rsi   = calcRSI(closes, 14);
    let rsiV    = null, rsiLbl = '';
    if (!isNaN(rsi)) {
      if (rsi <= 30)  { rsiV = 'BUY';  rsiLbl = `RSI oversold ${rsi.toFixed(1)}`; }
      if (rsi >= 70)  { rsiV = 'SELL'; rsiLbl = `RSI overbought ${rsi.toFixed(1)}`; }
    }

    // ── 2. Bollinger Bands (20,2) weight 3.0 ─────────────
    const bb    = calcBollinger(closes, 20, 2);
    let bbV     = null, bbLbl = '';
    if (bb) {
      if (last <= bb.lower) { bbV = 'BUY';  bbLbl = `BB lower ${bb.lower.toPrecision(6)}`; }
      if (last >= bb.upper) { bbV = 'SELL'; bbLbl = `BB upper ${bb.upper.toPrecision(6)}`; }
    }

    // ── 3. Stochastic (14,3) weight 2.5 ──────────────────
    const st    = calcStochastic(cnds, 14, 3);
    let stV     = null, stLbl = '';
    if (st) {
      if (st.k < 20 && st.d < 20) { stV = 'BUY';  stLbl = `Stoch oversold K${st.k.toFixed(0)}/D${st.d.toFixed(0)}`; }
      if (st.k > 80 && st.d > 80) { stV = 'SELL'; stLbl = `Stoch overbought K${st.k.toFixed(0)}/D${st.d.toFixed(0)}`; }
    }

    // ── 4. OTC Reversal Pattern weight 2.5 ───────────────
    const revV  = detectReversal(cnds);
    const revLbl = revV ? '3-candle reversal' : '';

    // ── 5. MACD (12,26,9) weight 2.0 ─────────────────────
    const macd  = calcMACD(closes, 12, 26, 9);
    let macV    = null, macLbl = '';
    if (macd) {
      const { histogram: h, prevHist: ph } = macd;
      if (h > 0 && ph <= 0)  { macV = 'BUY';  macLbl = 'MACD crossed up'; }
      else if (h < 0 && ph >= 0) { macV = 'SELL'; macLbl = 'MACD crossed down'; }
      else if (h > 0) { macV = 'BUY';  macLbl = 'MACD bull hist'; }
      else if (h < 0) { macV = 'SELL'; macLbl = 'MACD bear hist'; }
    }

    // ── 6. EMA Cross (9/21) weight 1.5 ───────────────────
    const ema9  = calcEMA(closes,  9);
    const ema21 = calcEMA(closes, 21);
    let emaV    = null, emaLbl = '';
    if (!isNaN(ema9) && !isNaN(ema21)) {
      if (ema9 > ema21) { emaV = 'BUY';  emaLbl = 'EMA9 > EMA21'; }
      if (ema9 < ema21) { emaV = 'SELL'; emaLbl = 'EMA9 < EMA21'; }
    }

    // ── Weighted voting ───────────────────────────────────
    const votes = [
      [rsiV, 3.0, rsiLbl],
      [bbV,  3.0, bbLbl],
      [stV,  2.5, stLbl],
      [revV, 2.5, revLbl],
      [macV, 2.0, macLbl],
      [emaV, 1.5, emaLbl],
    ];
    let bullW = 0, bearW = 0;
    const labels = [];
    for (const [v, w, lbl] of votes) {
      if (v === 'BUY')  { bullW += w; if (lbl) labels.push(lbl); }
      if (v === 'SELL') { bearW += w; if (lbl) labels.push(lbl); }
    }
    const bullPct = +(bullW / TOTAL_WEIGHT * 100).toFixed(1);
    const bearPct = +(bearW / TOTAL_WEIGHT * 100).toFixed(1);

    let signal, confidence;
    if (bullPct >= SIGNAL_THRESH)      { signal = 'BUY';  confidence = Math.round(bullPct); }
    else if (bearPct >= SIGNAL_THRESH) { signal = 'SELL'; confidence = Math.round(bearPct); }
    else                               { signal = 'WAIT'; confidence = Math.round(Math.max(bullPct, bearPct)); }

    const reason = labels.slice(0, 3).join(' | ') || 'Mixed signals';

    return {
      signal,
      confidence,
      reason,
      bullPct,
      bearPct,
      price:  last,
      source: 'demo',
      indicators: {
        rsi:      isNaN(rsi)  ? null : +rsi.toFixed(2),
        bbUpper:  bb          ? +bb.upper.toFixed(6) : null,
        bbLower:  bb          ? +bb.lower.toFixed(6) : null,
        stochK:   st          ? +st.k.toFixed(2) : null,
        stochD:   st          ? +st.d.toFixed(2) : null,
        macdHist: macd        ? +macd.histogram.toFixed(8) : null,
        ema9:     isNaN(ema9) ? null : +ema9.toFixed(6),
        ema21:    isNaN(ema21)? null : +ema21.toFixed(6),
      },
    };
  }
}

// ══════════════════════════════════════════════════════════
// DEMO ENGINE MANAGER
// Manages one SignalEngine + PriceSimulator per pair.
// ══════════════════════════════════════════════════════════
class DemoEngineManager {
  constructor() {
    this.engines    = {};
    this.simulators = {};
    this._init();
  }

  _init() {
    for (const pairId of Object.keys(DEMO_PAIRS)) {
      const sim    = new PriceSimulator(pairId, 90);
      const engine = new SignalEngine(pairId);
      // Seed engine with simulator candles
      for (const c of sim.candles) {
        engine.addCandle(c.o, c.h, c.l, c.c);
      }
      this.engines[pairId]    = engine;
      this.simulators[pairId] = sim;
    }
  }

  /** Advance all simulators one candle tick. */
  tick() {
    for (const pairId of Object.keys(DEMO_PAIRS)) {
      const c = this.simulators[pairId].tick();
      this.engines[pairId].addCandle(c.o, c.h, c.l, c.c);
    }
  }

  /**
   * Generate signals for all pairs.
   * @returns {Object[]} array of signal objects
   */
  generateAll() {
    const now      = new Date();
    const countdown = 60 - now.getSeconds();
    return Object.keys(DEMO_PAIRS).map(pairId => {
      const cfg = DEMO_PAIRS[pairId];
      const sig = this.engines[pairId].generateSignal();
      const sim = this.simulators[pairId];
      const prev = sim.candles.length > 1
        ? sim.candles[sim.candles.length - 2].c
        : sig.price;
      const changePct = prev ? (sig.price - prev) / prev * 100 : 0;
      return {
        pair_id:     pairId,
        pair_name:   cfg.name,
        category:    cfg.category,
        payout:      cfg.payout,
        signal:      sig.signal,
        confidence:  sig.confidence,
        reason:      sig.reason,
        bull_pct:    sig.bullPct,
        bear_pct:    sig.bearPct,
        data_source: 'demo',
        price:       sig.price,
        change_pct:  +changePct.toFixed(4),
        candles:     this.engines[pairId].candles.length,
        countdown,
        indicators:  sig.indicators,
        ts:          now.toISOString().replace(/\.\d{3}Z$/, 'Z'),
      };
    });
  }
}

// Export for use in app.js
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { DemoEngineManager, SignalEngine, PriceSimulator, DEMO_PAIRS,
                     calcRSI, calcBollinger, calcStochastic, calcMACD, calcEMA, detectReversal };
}
