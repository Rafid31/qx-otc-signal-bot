// QX OTC Signal Engine - Professional Algorithm for Synthetic Markets
// Optimized for OTC (Over-The-Counter) trading with 80%+ accuracy target

class SignalEngine {
  constructor() {
    this.history = {}; // Stores candle history per asset
    this.signals = {}; // Current signals
  }

  // Process new candle and generate signal
  analyzeCandle(asset, candles) {
    if (!candles || candles.length < 20) return null;
    
    // Store history
    this.history[asset] = candles;
    
    // Run multi-indicator analysis
    const rsi = this.calculateRSI(candles, 14);
    const macd = this.calculateMACD(candles);
    const ema = this.calculateEMA(candles, 9);
    const bb = this.calculateBollingerBands(candles, 20, 2);
    const trend = this.detectTrend(candles);
    const momentum = this.calculateMomentum(candles, 10);
    
    // OTC-specific: Synthetic markets have patterns
    const syntheticPattern = this.detectSyntheticPattern(candles);
    
    // Combine signals with weighted scoring
    let score = 0;
    let buySignals = 0;
    let sellSignals = 0;
    
    // RSI Analysis (Weight: 20%)
    if (rsi < 30) { score += 20; buySignals++; }
    else if (rsi > 70) { score -= 20; sellSignals++; }
    else if (rsi < 40) { score += 10; buySignals++; }
    else if (rsi > 60) { score -= 10; sellSignals++; }
    
    // MACD Analysis (Weight: 25%)
    if (macd.histogram > 0 && macd.prev < 0) { score += 25; buySignals++; } // Bullish cross
    else if (macd.histogram < 0 && macd.prev > 0) { score -= 25; sellSignals++; } // Bearish cross
    else if (macd.histogram > 0) { score += 12; buySignals++; }
    else if (macd.histogram < 0) { score -= 12; sellSignals++; }
    
    // Bollinger Bands (Weight: 15%)
    const lastPrice = candles[candles.length - 1].close;
    if (lastPrice < bb.lower) { score += 15; buySignals++; }
    else if (lastPrice > bb.upper) { score -= 15; sellSignals++; }
    
    // EMA Trend (Weight: 15%)
    if (lastPrice > ema) { score += 15; buySignals++; }
    else { score -= 15; sellSignals++; }
    
    // Momentum (Weight: 10%)
    if (momentum > 0) { score += 10; buySignals++; }
    else { score -= 10; sellSignals++; }
    
    // Synthetic Pattern (Weight: 15%) - OTC markets have predictable reversals
    if (syntheticPattern === 'reversal_up') { score += 15; buySignals++; }
    else if (syntheticPattern === 'reversal_down') { score -= 15; sellSignals++; }
    
    // Generate signal
    const signal = {
      asset,
      direction: score > 15 ? 'BUY' : score < -15 ? 'SELL' : null,
      confidence: Math.min(Math.abs(score), 100),
      indicators: { rsi, macd: macd.histogram, ema, bb, momentum, trend },
      timestamp: Date.now(),
      buySignals,
      sellSignals
    };
    
    // Only emit strong signals
    if (signal.direction && signal.confidence >= 60) {
      this.signals[asset] = signal;
      return signal;
    }
    
    return null;
  }
  
  // RSI (Relative Strength Index)
  calculateRSI(candles, period = 14) {
    const closes = candles.map(c => c.close);
    const changes = [];
    for (let i = 1; i < closes.length; i++) {
      changes.push(closes[i] - closes[i-1]);
    }
    
    const gains = changes.map(c => c > 0 ? c : 0);
    const losses = changes.map(c => c < 0 ? -c : 0);
    
    const avgGain = gains.slice(-period).reduce((a,b) => a+b, 0) / period;
    const avgLoss = losses.slice(-period).reduce((a,b) => a+b, 0) / period;
    
    if (avgLoss === 0) return 100;
    const rs = avgGain / avgLoss;
    return 100 - (100 / (1 + rs));
  }
  
  // MACD (Moving Average Convergence Divergence)
  calculateMACD(candles) {
    const closes = candles.map(c => c.close);
    const ema12 = this.calculateEMAFromArray(closes, 12);
    const ema26 = this.calculateEMAFromArray(closes, 26);
    const macdLine = ema12 - ema26;
    
    // Signal line (9-period EMA of MACD)
    const macdHistory = [macdLine]; // Simplified
    const signal = macdLine * 0.9; // Approximation
    const histogram = macdLine - signal;
    const prev = candles.length > 1 ? (closes[closes.length-2] - closes[closes.length-3]) : 0;
    
    return { histogram, macdLine, signal, prev };
  }
  
  // EMA (Exponential Moving Average)
  calculateEMA(candles, period) {
    const closes = candles.map(c => c.close);
    return this.calculateEMAFromArray(closes, period);
  }
  
  calculateEMAFromArray(data, period) {
    const k = 2 / (period + 1);
    let ema = data.slice(0, period).reduce((a,b) => a+b, 0) / period;
    for (let i = period; i < data.length; i++) {
      ema = data[i] * k + ema * (1 - k);
    }
    return ema;
  }
  
  // Bollinger Bands
  calculateBollingerBands(candles, period = 20, stdDev = 2) {
    const closes = candles.map(c => c.close).slice(-period);
    const sma = closes.reduce((a,b) => a+b, 0) / closes.length;
    const variance = closes.reduce((sum, val) => sum + Math.pow(val - sma, 2), 0) / closes.length;
    const std = Math.sqrt(variance);
    
    return {
      upper: sma + (stdDev * std),
      middle: sma,
      lower: sma - (stdDev * std)
    };
  }
  
  // Detect trend
  detectTrend(candles) {
    const closes = candles.map(c => c.close);
    const recent = closes.slice(-10);
    const older = closes.slice(-20, -10);
    
    const recentAvg = recent.reduce((a,b) => a+b, 0) / recent.length;
    const olderAvg = older.reduce((a,b) => a+b, 0) / older.length;
    
    if (recentAvg > olderAvg * 1.001) return 'uptrend';
    if (recentAvg < olderAvg * 0.999) return 'downtrend';
    return 'sideways';
  }
  
  // Momentum
  calculateMomentum(candles, period = 10) {
    const closes = candles.map(c => c.close);
    const current = closes[closes.length - 1];
    const past = closes[closes.length - period];
    return ((current - past) / past) * 100;
  }
  
  // OTC-Specific: Detect synthetic market reversal patterns
  detectSyntheticPattern(candles) {
    if (candles.length < 10) return null;
    
    const recent = candles.slice(-10);
    const closes = recent.map(c => c.close);
    
    // Check for consecutive bearish candles followed by bullish reversal
    let consecutiveBearish = 0;
    for (let i = recent.length - 2; i >= 0; i--) {
      if (closes[i+1] < closes[i]) consecutiveBearish++;
      else break;
    }
    
    // Check for consecutive bullish candles followed by bearish reversal
    let consecutiveBullish = 0;
    for (let i = recent.length - 2; i >= 0; i--) {
      if (closes[i+1] > closes[i]) consecutiveBullish++;
      else break;
    }
    
    // OTC markets often reverse after 3-5 consecutive moves
    if (consecutiveBearish >= 3 && closes[closes.length-1] > closes[closes.length-2]) {
      return 'reversal_up';
    }
    if (consecutiveBullish >= 3 && closes[closes.length-1] < closes[closes.length-2]) {
      return 'reversal_down';
    }
    
    return null;
  }
}

// Export for use in app.js
window.SignalEngine = SignalEngine;
