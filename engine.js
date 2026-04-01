/**
 * engine.js — OTC Signal Engine (client-side / demo mode)
 * Used when running without a live backend.
 * Mirrors the Python SignalEngine logic exactly.
 */
class SignalEngine {
  constructor(pairId) {
    this.pairId  = pairId;
    this.closes  = [];
    this.highs   = [];
    this.lows    = [];
    this.opens   = [];
    this.MAX     = 120;
  }

  _push(arr, v) {
    arr.push(v);
    if (arr.length > this.MAX) arr.shift();
  }

  addCandle(o, h, l, c) {
    this._push(this.opens,  o);
    this._push(this.highs,  h);
    this._push(this.lows,   l);
    this._push(this.closes, c);
  }

  _ema(data, period) {
    if (!data.length) return 0;
    const slice = data.slice(-Math.max(period * 2, data.length));
    const k = 2 / (period + 1);
    let e = slice[0];
    for (let i = 1; i < slice.length; i++) e = slice[i] * k + e * (1 - k);
    return e;
  }

  calcRSI(period = 14) {
    const c = this.closes;
    if (c.length < period + 2) return null;
    const slice = c.slice(-(period + 1));
    let gains = 0, losses = 0;
    for (let i = 1; i < slice.length; i++) {
      const d = slice[i] - slice[i - 1];
      if (d > 0) gains += d; else losses -= d;
    }
    gains  /= period;
    losses /= period;
    if (losses === 0) return 100;
    return 100 - (100 / (1 + gains / losses));
  }

  calcMACD() {
    if (this.closes.length < 35) return [null, null, null];
    const macd    = this._ema(this.closes, 12) - this._ema(this.closes, 26);
    const signal  = macd * 0.85;
    return [macd, signal, macd - signal];
  }

  calcBollinger(period = 20) {
    const c = this.closes;
    if (c.length < period) return [null, null, null, null];
    const slice = c.slice(-period);
    const mid   = slice.reduce((s, v) => s + v, 0) / period;
    const std   = Math.sqrt(slice.reduce((s, v) => s + (v - mid) ** 2, 0) / period);
    const upper = mid + 2 * std;
    const lower = mid - 2 * std;
    const pct   = (upper - lower) > 0 ? (c[c.length - 1] - lower) / (upper - lower) : 0.5;
    return [upper, mid, lower, pct];
  }

  calcStochastic(kPeriod = 14) {
    const c = this.closes, h = this.highs, l = this.lows;
    if (c.length < kPeriod) return [null, null];
    const rH = h.slice(-kPeriod), rL = l.slice(-kPeriod);
    const hh = Math.max(...rH), ll = Math.min(...rL);
    if (hh === ll) return [50, 50];
    const k = 100 * (c[c.length - 1] - ll) / (hh - ll);
    const dSlice = c.slice(-3).map((cv, i) => {
      const _h = h.slice(-(kPeriod - i)), _l = l.slice(-(kPeriod - i));
      const _hh = Math.max(..._h), _ll = Math.min(..._l);
      return _hh !== _ll ? 100 * (cv - _ll) / (_hh - _ll) : 50;
    });
    return [k, dSlice.reduce((s, v) => s + v, 0) / dSlice.length];
  }

  calcEMACross() {
    if (this.closes.length < 21) return null;
    return this._ema(this.closes, 9) - this._ema(this.closes, 21);
  }

  detectOTCPattern() {
    const c = this.closes, o = this.opens;
    if (c.length < 5) return 0;
    const dirs = [-3, -2, -1].map(i => (c[c.length + i] > o[o.length + i] ? 1 : -1));
    if (dirs.every(d => d === -1)) return 1;
    if (dirs.every(d => d ===  1)) return -1;
    const last = c.length - 1;
    const range = this.highs[last] - this.lows[last];
    if (range > 0) {
      const lw = Math.min(o[last], c[last]) - this.lows[last];
      const uw = this.highs[last] - Math.max(o[last], c[last]);
      if (lw / range > 0.65) return 0.6;
      if (uw / range > 0.65) return -0.6;
    }
    return 0;
  }

  generateSignal() {
    if (this.closes.length < 28)
      return { signal: 'WAIT', confidence: 0, reason: 'Collecting data…' };

    const votes = [];

    const rsi = this.calcRSI();
    if (rsi !== null) {
      if      (rsi <= 25) votes.push([1,  3.0, `RSI oversold ${rsi.toFixed(1)}`]);
      else if (rsi <= 35) votes.push([1,  1.8, `RSI low ${rsi.toFixed(1)}`]);
      else if (rsi >= 75) votes.push([-1, 3.0, `RSI overbought ${rsi.toFixed(1)}`]);
      else if (rsi >= 65) votes.push([-1, 1.8, `RSI high ${rsi.toFixed(1)}`]);
      else                votes.push([rsi < 50 ? 1 : -1, 0.5, `RSI neutral ${rsi.toFixed(1)}`]);
    }

    const [macd, sig, hist] = this.calcMACD();
    if (macd !== null) {
      if      (macd > 0 && hist > 0) votes.push([1,  2.0, 'MACD bullish crossover']);
      else if (macd < 0 && hist < 0) votes.push([-1, 2.0, 'MACD bearish crossover']);
      else if (hist > 0)             votes.push([1,  1.0, 'MACD histogram rising']);
      else                           votes.push([-1, 1.0, 'MACD histogram falling']);
    }

    const [, , , pctB] = this.calcBollinger();
    if (pctB !== null) {
      if      (pctB <= 0.05) votes.push([1,  3.0, 'Price at lower BB band']);
      else if (pctB <= 0.20) votes.push([1,  1.5, 'Price near lower BB']);
      else if (pctB >= 0.95) votes.push([-1, 3.0, 'Price at upper BB band']);
      else if (pctB >= 0.80) votes.push([-1, 1.5, 'Price near upper BB']);
      else                   votes.push([pctB < 0.5 ? 1 : -1, 0.4, 'BB mid range']);
    }

    const [k, d] = this.calcStochastic();
    if (k !== null) {
      if      (k < 20 && d < 20) votes.push([1,  2.5, `Stochastic oversold K=${k.toFixed(0)}`]);
      else if (k > 80 && d > 80) votes.push([-1, 2.5, `Stochastic overbought K=${k.toFixed(0)}`]);
      else if (k > d)            votes.push([1,  1.0, 'Stochastic K above D']);
      else                       votes.push([-1, 1.0, 'Stochastic K below D']);
    }

    const emaCross = this.calcEMACross();
    if (emaCross !== null)
      votes.push([emaCross > 0 ? 1 : -1, 1.5, `EMA9/21 ${emaCross > 0 ? 'bull' : 'bear'}ish`]);

    const otc = this.detectOTCPattern();
    if (Math.abs(otc) > 0)
      votes.push([otc > 0 ? 1 : -1, Math.abs(otc) * 2.5, 'OTC reversal pattern']);

    if (!votes.length)
      return { signal: 'WAIT', confidence: 0, reason: 'Insufficient data' };

    let bull = 0, bear = 0;
    for (const [dir, w] of votes) dir === 1 ? (bull += w) : (bear += w);
    const total    = bull + bear;
    const bullPct  = total ? (bull / total) * 100 : 50;
    const bearPct  = total ? (bear / total) * 100 : 50;
    const reasons  = votes.sort((a, b) => b[1] - a[1]).slice(0, 3).map(v => v[2]).join(' | ');
    const THRESH   = 60;

    if      (bullPct >= THRESH) return { signal: 'BUY',  confidence: Math.min(Math.round(bullPct), 95), reason: reasons };
    else if (bearPct >= THRESH) return { signal: 'SELL', confidence: Math.min(Math.round(bearPct), 95), reason: reasons };
    else                        return { signal: 'WAIT', confidence: Math.round(Math.max(bullPct, bearPct)), reason: 'Mixed signals – waiting' };
  }
}

// ── Demo price simulator ──────────────────────────────────────────────────────
class PriceSimulator {
  constructor(basePrice, pairId) {
    this.price  = basePrice;
    this.pairId = pairId;
    this.trend  = Math.random() > 0.5 ? 1 : -1;
    this.pip    = pairId.includes('JPY') ? 0.01 : 0.0001;
    this.trendStrength = this.pip * (2 + Math.random() * 4);
    this.trendLife = Math.floor(5 + Math.random() * 20);
    this.tick   = 0;
  }
  nextCandle() {
    this.tick++;
    if (this.tick % this.trendLife === 0) {
      this.trend = -this.trend;
      this.trendStrength = this.pip * (2 + Math.random() * 4);
      this.trendLife = Math.floor(5 + Math.random() * 20);
    }
    const drift = this.trend * this.trendStrength;
    const vol   = this.pip * (2 + Math.random() * 8);
    const o = this.price;
    const c = o + drift + (Math.random() - 0.5) * vol;
    const h = Math.max(o, c) + Math.random() * vol * 0.5;
    const l = Math.min(o, c) - Math.random() * vol * 0.5;
    this.price = c;
    const dp = this.pip >= 0.01 ? 3 : 5;
    return {
      open:  +o.toFixed(dp), high:  +h.toFixed(dp),
      low:   +l.toFixed(dp), close: +c.toFixed(dp)
    };
  }
}
