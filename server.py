"""
QX OTC Signal Bot — FastAPI Backend v2.1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Real data only — Yahoo Finance REST (Forex/Commodities) + Binance (Crypto)
• All 28 QX OTC pairs with proper "OTC" names
• 6-indicator weighted voting signal algorithm
• Anti-blocking: full browser headers (UA, Referer, Origin, Sec-Fetch)
  + query1/query2 rotation + range=1d/5d/2d fallback
• NO yfinance library — direct REST only (avoids Railway proxy blocks)
• WebSocket broadcasting every second + REST fallback
• No fake / simulated signals — demo mode only when ALL fetches fail
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

# ══════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("qx_bot")

# ══════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════
VERSION        = "2.2.0"
MIN_CANDLES    = 30        # need at least this many before signaling
SEED_CANDLES   = 90        # candles to seed on startup
MAX_CANDLES    = 200       # rolling window cap
REFRESH_SECS   = 60        # data refresh interval
BROADCAST_SECS = 1         # WS broadcast interval
TOTAL_WEIGHT   = 14.5      # sum of all indicator weights
SIGNAL_THRESH  = 60.0      # % vote threshold to fire BUY / SELL

# ══════════════════════════════════════════════════════════
# ALL 28 QX OTC PAIRS
# ══════════════════════════════════════════════════════════
PAIRS: Dict[str, dict] = {
    # ── FOREX OTC (22 pairs) ─────────────────────────────
    "EURUSD_otc": {"name": "EUR/USD OTC",  "symbol": "EURUSD=X",  "source": "yahoo",   "category": "Forex",     "payout": 80},
    "GBPUSD_otc": {"name": "GBP/USD OTC",  "symbol": "GBPUSD=X",  "source": "yahoo",   "category": "Forex",     "payout": 38},
    "USDJPY_otc": {"name": "USD/JPY OTC",  "symbol": "JPY=X",     "source": "yahoo",   "category": "Forex",     "payout": 93},
    "AUDUSD_otc": {"name": "AUD/USD OTC",  "symbol": "AUDUSD=X",  "source": "yahoo",   "category": "Forex",     "payout": 88},
    "EURJPY_otc": {"name": "EUR/JPY OTC",  "symbol": "EURJPY=X",  "source": "yahoo",   "category": "Forex",     "payout": 85},
    "USDCAD_otc": {"name": "USD/CAD OTC",  "symbol": "CAD=X",     "source": "yahoo",   "category": "Forex",     "payout": 84},
    "EURGBP_otc": {"name": "EUR/GBP OTC",  "symbol": "EURGBP=X",  "source": "yahoo",   "category": "Forex",     "payout": 95},
    "USDCHF_otc": {"name": "USD/CHF OTC",  "symbol": "CHF=X",     "source": "yahoo",   "category": "Forex",     "payout": 85},
    "AUDCAD_otc": {"name": "AUD/CAD OTC",  "symbol": "AUDCAD=X",  "source": "yahoo",   "category": "Forex",     "payout": 88},
    "EURAUD_otc": {"name": "EUR/AUD OTC",  "symbol": "EURAUD=X",  "source": "yahoo",   "category": "Forex",     "payout": 82},
    "GBPJPY_otc": {"name": "GBP/JPY OTC",  "symbol": "GBPJPY=X",  "source": "yahoo",   "category": "Forex",     "payout": 90},
    "CHFJPY_otc": {"name": "CHF/JPY OTC",  "symbol": "CHFJPY=X",  "source": "yahoo",   "category": "Forex",     "payout": 85},
    "NZDCAD_otc": {"name": "NZD/CAD OTC",  "symbol": "NZDCAD=X",  "source": "yahoo",   "category": "Forex",     "payout": 87},
    "NZDCHF_otc": {"name": "NZD/CHF OTC",  "symbol": "NZDCHF=X",  "source": "yahoo",   "category": "Forex",     "payout": 87},
    "AUDCHF_otc": {"name": "AUD/CHF OTC",  "symbol": "AUDCHF=X",  "source": "yahoo",   "category": "Forex",     "payout": 86},
    "EURCHF_otc": {"name": "EUR/CHF OTC",  "symbol": "EURCHF=X",  "source": "yahoo",   "category": "Forex",     "payout": 78},
    "CADJPY_otc": {"name": "CAD/JPY OTC",  "symbol": "CADJPY=X",  "source": "yahoo",   "category": "Forex",     "payout": 85},
    "GBPAUD_otc": {"name": "GBP/AUD OTC",  "symbol": "GBPAUD=X",  "source": "yahoo",   "category": "Forex",     "payout": 83},
    "GBPCAD_otc": {"name": "GBP/CAD OTC",  "symbol": "GBPCAD=X",  "source": "yahoo",   "category": "Forex",     "payout": 82},
    "EURCAD_otc": {"name": "EUR/CAD OTC",  "symbol": "EURCAD=X",  "source": "yahoo",   "category": "Forex",     "payout": 83},
    "NZDUSD_otc": {"name": "NZD/USD OTC",  "symbol": "NZDUSD=X",  "source": "yahoo",   "category": "Forex",     "payout": 86},
    "GBPCHF_otc": {"name": "GBP/CHF OTC",  "symbol": "GBPCHF=X",  "source": "yahoo",   "category": "Forex",     "payout": 84},
    # ── COMMODITIES OTC (4 pairs) ─────────────────────────
    "XAUUSD_otc": {"name": "Gold OTC",      "symbol": "GC=F",      "source": "yahoo",   "category": "Commodity", "payout": 87},
    "XAGUSD_otc": {"name": "Silver OTC",    "symbol": "SI=F",      "source": "yahoo",   "category": "Commodity", "payout": 93},
    "UKOIL_otc":  {"name": "UK Brent OTC",  "symbol": "BZ=F",      "source": "yahoo",   "category": "Commodity", "payout": 93},
    "USOIL_otc":  {"name": "US Crude OTC",  "symbol": "CL=F",      "source": "yahoo",   "category": "Commodity", "payout": 84},
    # ── CRYPTO OTC — Binance only (2 pairs) ──────────────
    "BTCUSD_otc": {"name": "Bitcoin OTC",   "symbol": "BTCUSDT",   "source": "binance", "category": "Crypto",    "payout": 80},
    "ETHUSD_otc": {"name": "Ethereum OTC",  "symbol": "ETHUSDT",   "source": "binance", "category": "Crypto",    "payout": 66},
}

# ══════════════════════════════════════════════════════════
# GLOBAL STATE
# ══════════════════════════════════════════════════════════
price_store: Dict[str, Optional[pd.DataFrame]] = {pid: None for pid in PAIRS}
data_mode:   Dict[str, str]                    = {pid: "demo" for pid in PAIRS}
store_lock = asyncio.Lock()


# ══════════════════════════════════════════════════════════
# ANTI-BLOCKING SESSION
# Full Chrome browser header set — mirrors a real browser request.
# This is the key to bypassing Yahoo Finance server-side bot detection.
# ══════════════════════════════════════════════════════════
def _make_session() -> requests.Session:
    """
    Return a requests.Session with a complete Chrome browser header set.
    Includes User-Agent, Referer, Origin, Accept, and all Sec-Fetch headers
    that Yahoo Finance checks to distinguish bots from real browsers.
    """
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin":          "https://finance.yahoo.com",
        "Referer":         "https://finance.yahoo.com/",
        "Connection":      "keep-alive",
        "Cache-Control":   "no-cache",
        "Pragma":          "no-cache",
        "Sec-Ch-Ua":       '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile":   "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest":  "empty",
        "Sec-Fetch-Mode":  "cors",
        "Sec-Fetch-Site":  "same-site",
    })
    return s


# ══════════════════════════════════════════════════════════
# DATA FETCHING — YAHOO FINANCE (direct REST, no yfinance lib)
# ══════════════════════════════════════════════════════════

# Endpoint rotation and range fallback matrix.
# We try all combinations until we get ≥ MIN_CANDLES rows.
_YF_HOSTS  = [
    "https://query1.finance.yahoo.com",
    "https://query2.finance.yahoo.com",
]
_YF_RANGES = ["1d", "5d", "2d"]   # 5d gives more bars if market just opened


def _parse_yahoo_json(data: dict) -> Optional[pd.DataFrame]:
    """Parse Yahoo Finance v8 chart JSON → clean OHLC DataFrame."""
    try:
        result = data["chart"]["result"][0]
        ts     = result["timestamp"]
        quote  = result["indicators"]["quote"][0]
        df = pd.DataFrame(
            {
                "Open":  quote["open"],
                "High":  quote["high"],
                "Low":   quote["low"],
                "Close": quote["close"],
            },
            index=pd.to_datetime(ts, unit="s", utc=True),
        )
        return df.dropna()
    except Exception as exc:
        log.debug(f"_parse_yahoo_json error: {exc}")
        return None


def fetch_yahoo_candles(symbol: str, limit: int = SEED_CANDLES) -> Optional[pd.DataFrame]:
    """
    Fetch 1-min OHLC from Yahoo Finance using direct REST calls only.
    No yfinance library — avoids its proxy/auth issues on Railway.

    Anti-blocking strategy:
      1. Full Chrome browser headers (UA, Referer, Origin, Sec-Fetch-*)
      2. Rotate between query1 and query2 endpoints
      3. Try range=1d first; fall back to 5d / 2d for more bars
      4. Back off 0.4s between attempts to avoid rate-limiting
    """
    for host in _YF_HOSTS:
        for range_val in _YF_RANGES:
            try:
                sess = _make_session()
                url  = (
                    f"{host}/v8/finance/chart/{symbol}"
                    f"?interval=1m&range={range_val}&includePrePost=false"
                )
                r = sess.get(url, timeout=14)
                if r.status_code == 429:
                    log.debug(f"  rate-limited {symbol} ({host[-6:]}/{range_val}), backing off")
                    time.sleep(1.0)
                    continue
                if r.status_code != 200:
                    log.debug(f"  HTTP {r.status_code} for {symbol} ({host[-6:]}/{range_val})")
                    continue
                df = _parse_yahoo_json(r.json())
                if df is not None and len(df) >= 10:
                    df = df.tail(limit)
                    log.info(
                        f"  ✓ Yahoo {symbol} "
                        f"({host.split('//')[1][:6]}/{range_val}): {len(df)} candles"
                    )
                    return df
            except Exception as exc:
                log.debug(f"  Yahoo attempt failed {symbol} {range_val}: {type(exc).__name__}: {exc}")
            time.sleep(0.4)   # brief back-off between attempts

    log.warning(f"  ✗ All Yahoo REST attempts failed for {symbol} — will use demo fallback")
    return None


# ══════════════════════════════════════════════════════════
# DATA FETCHING — BINANCE (crypto only)
# ══════════════════════════════════════════════════════════
def fetch_binance_candles(symbol: str, limit: int = SEED_CANDLES) -> Optional[pd.DataFrame]:
    """Fetch 1-min klines from Binance public REST API (no API key needed)."""
    try:
        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval=1m&limit={limit}"
        )
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        rows = r.json()
        df = pd.DataFrame(
            [
                {
                    "Open":  float(k[1]),
                    "High":  float(k[2]),
                    "Low":   float(k[3]),
                    "Close": float(k[4]),
                }
                for k in rows
            ]
        )
        if len(df) >= 10:
            log.info(f"  ✓ bnb  {symbol}: {len(df)} candles")
            return df
    except Exception as exc:
        log.warning(f"  ✗ Binance {symbol}: {exc}")
    return None


# ══════════════════════════════════════════════════════════
# DEMO FALLBACK SIMULATOR
# Only used when ALL real-data fetches fail for a pair.
# ══════════════════════════════════════════════════════════
_DEMO_BASES = {
    "EURUSD_otc": 1.0850,  "GBPUSD_otc": 1.2650,  "USDJPY_otc": 151.50,
    "AUDUSD_otc": 0.6520,  "EURJPY_otc": 164.50,  "USDCAD_otc": 1.3600,
    "EURGBP_otc": 0.8580,  "USDCHF_otc": 0.9050,  "AUDCAD_otc": 0.8950,
    "EURAUD_otc": 1.6620,  "GBPJPY_otc": 191.80,  "CHFJPY_otc": 167.40,
    "NZDCAD_otc": 0.8230,  "NZDCHF_otc": 0.5620,  "AUDCHF_otc": 0.5900,
    "EURCHF_otc": 0.9780,  "CADJPY_otc": 111.40,  "GBPAUD_otc": 1.9380,
    "GBPCAD_otc": 1.7180,  "EURCAD_otc": 1.4760,  "NZDUSD_otc": 0.6050,
    "GBPCHF_otc": 1.1450,  "XAUUSD_otc": 2320.00, "XAGUSD_otc": 27.50,
    "UKOIL_otc":  84.50,   "USOIL_otc":  81.20,   "BTCUSD_otc": 65000.0,
    "ETHUSD_otc": 3200.0,
}
_JPY_PAIRS   = {"USDJPY_otc","EURJPY_otc","GBPJPY_otc","CHFJPY_otc","CADJPY_otc"}
_LARGE_PAIRS = {"BTCUSD_otc","ETHUSD_otc","XAUUSD_otc"}
_MID_PAIRS   = {"XAGUSD_otc","UKOIL_otc","USOIL_otc"}


def _pip_size(pair_id: str) -> float:
    if pair_id in _LARGE_PAIRS:  return 5.0
    if pair_id in _MID_PAIRS:    return 0.05
    if pair_id in _JPY_PAIRS:    return 0.05
    return 0.0001


def generate_demo_candles(pair_id: str, count: int = SEED_CANDLES) -> pd.DataFrame:
    """Deterministic realistic OHLC simulator used only as last-resort fallback."""
    base = _DEMO_BASES.get(pair_id, 1.0)
    pip  = _pip_size(pair_id)
    rng  = np.random.default_rng(seed=sum(ord(c) for c in pair_id))
    rows = []
    price = base
    for _ in range(count):
        move    = rng.normal(0, pip * 3)
        open_p  = price
        close_p = price + move
        high_p  = max(open_p, close_p) + abs(rng.normal(0, pip * 0.8))
        low_p   = min(open_p, close_p) - abs(rng.normal(0, pip * 0.8))
        rows.append({"Open": open_p, "High": high_p, "Low": low_p, "Close": close_p})
        price = close_p
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════
# TECHNICAL INDICATORS
# ══════════════════════════════════════════════════════════

def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """RSI using Wilder's EMA smoothing."""
    delta    = closes.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _bollinger(closes: pd.Series, period: int = 20, n_std: float = 2.0):
    """Bollinger Bands — returns (upper, mid, lower) Series."""
    sma   = closes.rolling(period).mean()
    std   = closes.rolling(period).std(ddof=0)
    return sma + n_std * std, sma, sma - n_std * std


def _stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3):
    """Stochastic Oscillator — returns (%K, %D) Series."""
    low_min  = df["Low"].rolling(k_period).min()
    high_max = df["High"].rolling(k_period).max()
    denom    = (high_max - low_min).replace(0, np.nan)
    k        = 100 * (df["Close"] - low_min) / denom
    d        = k.rolling(d_period).mean()
    return k, d


def _macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD — returns (macd_line, signal_line, histogram) Series."""
    ema_fast    = closes.ewm(span=fast,   adjust=False).mean()
    ema_slow    = closes.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line


def _ema(closes: pd.Series, period: int) -> pd.Series:
    return closes.ewm(span=period, adjust=False).mean()


def _reversal_pattern(df: pd.DataFrame) -> Optional[str]:
    """
    OTC Exhaustion Reversal:
    3 consecutive same-direction candles = momentum exhausted → expect flip.
    Returns "BUY" (expect up), "SELL" (expect down), or None.
    """
    if len(df) < 4:
        return None
    tail = df.tail(4)
    dirs = [
        "bull" if row["Close"] >= row["Open"] else "bear"
        for _, row in tail.iterrows()
    ]
    if dirs[-3:] == ["bear", "bear", "bear"]:
        return "BUY"
    if dirs[-3:] == ["bull", "bull", "bull"]:
        return "SELL"
    return None


# ══════════════════════════════════════════════════════════
# SIGNAL ENGINE  — 6-Indicator Weighted Voting
# ══════════════════════════════════════════════════════════
def generate_signal(pair_id: str, df: Optional[pd.DataFrame]) -> dict:
    """
    Inputs : pair_id, DataFrame with Open/High/Low/Close columns
    Output : {signal, confidence, reason, bull_pct, bear_pct,
              data_source, price, candles, indicators}

    Weights: RSI(3.0) + BB(3.0) + Stoch(2.5) + Reversal(2.5)
             + MACD(2.0) + EMACross(1.5) = 14.5 total
    Fire threshold: ≥60% weighted vote → BUY or SELL; else WAIT
    """
    _empty = {
        "signal": "WAIT", "confidence": 0, "reason": "Insufficient data",
        "bull_pct": 0, "bear_pct": 0, "data_source": data_mode.get(pair_id, "demo"),
        "price": None, "candles": 0, "indicators": {},
    }
    if df is None or len(df) < MIN_CANDLES:
        return _empty

    closes      = df["Close"]
    last_close  = float(closes.iloc[-1])
    candle_cnt  = len(df)

    # ── 1. RSI (14) — weight 3.0 ─────────────────────────
    rsi_s  = _rsi(closes)
    rsi    = float(rsi_s.iloc[-1])
    rsi_v  = None
    rsi_lbl = ""
    if not np.isnan(rsi):
        if rsi <= 40:
            rsi_v, rsi_lbl = "BUY",  f"RSI oversold {rsi:.1f}"
        elif rsi >= 60:
            rsi_v, rsi_lbl = "SELL", f"RSI overbought {rsi:.1f}"

    # ── 2. Bollinger Bands (20,2) — weight 3.0 ───────────
    bb_up, _bb_mid, bb_lo = _bollinger(closes)
    bb_u   = float(bb_up.iloc[-1])
    bb_l   = float(bb_lo.iloc[-1])
    bb_v   = None
    bb_lbl = ""
    if not (np.isnan(bb_u) or np.isnan(bb_l)):
        bb_range = bb_u - bb_l
        if bb_range > 0:
            bb_pos = (last_close - bb_l) / bb_range   # 0 = at lower, 1 = at upper
            if bb_pos <= 0.25:
                bb_v, bb_lbl = "BUY",  f"BB lower zone {bb_pos*100:.0f}%"
            elif bb_pos >= 0.75:
                bb_v, bb_lbl = "SELL", f"BB upper zone {bb_pos*100:.0f}%"

    # ── 3. Stochastic (14,3) — weight 2.5 ────────────────
    k_s, d_s = _stochastic(df)
    k_v  = float(k_s.iloc[-1])
    d_v  = float(d_s.iloc[-1])
    st_v = None
    st_lbl = ""
    if not (np.isnan(k_v) or np.isnan(d_v)):
        if k_v < 20:
            st_v, st_lbl = "BUY",  f"Stoch oversold K{k_v:.0f}/D{d_v:.0f}"
        elif k_v > 80:
            st_v, st_lbl = "SELL", f"Stoch overbought K{k_v:.0f}/D{d_v:.0f}"

    # ── 4. OTC Reversal Pattern — weight 2.5 ─────────────
    rev_v   = _reversal_pattern(df)
    rev_lbl = "3-candle reversal" if rev_v else ""

    # ── 5. MACD (12,26,9) — weight 2.0 ───────────────────
    _ml, _sl, hist = _macd(closes)
    h_curr = float(hist.iloc[-1])
    h_prev = float(hist.iloc[-2]) if len(hist) > 1 else 0.0
    mac_v  = None
    mac_lbl = ""
    if not (np.isnan(h_curr) or np.isnan(h_prev)):
        if h_curr > 0 and h_prev <= 0:
            mac_v, mac_lbl = "BUY",  "MACD crossed up"
        elif h_curr < 0 and h_prev >= 0:
            mac_v, mac_lbl = "SELL", "MACD crossed down"
        elif h_curr > 0:
            mac_v, mac_lbl = "BUY",  f"MACD bull hist"
        elif h_curr < 0:
            mac_v, mac_lbl = "SELL", f"MACD bear hist"

    # ── 6. EMA Cross (9/21) — weight 1.5 ─────────────────
    ema9  = float(_ema(closes,  9).iloc[-1])
    ema21 = float(_ema(closes, 21).iloc[-1])
    ema_v = None
    ema_lbl = ""
    if not (np.isnan(ema9) or np.isnan(ema21)):
        if ema9 > ema21:
            ema_v, ema_lbl = "BUY",  "EMA9 > EMA21"
        elif ema9 < ema21:
            ema_v, ema_lbl = "SELL", "EMA9 < EMA21"

    # ── Weighted vote ─────────────────────────────────────
    votes = [
        (rsi_v, 3.0, rsi_lbl),
        (bb_v,  3.0, bb_lbl),
        (st_v,  2.5, st_lbl),
        (rev_v, 2.5, rev_lbl),
        (mac_v, 2.0, mac_lbl),
        (ema_v, 1.5, ema_lbl),
    ]
    bull_w = sum(w for v, w, _ in votes if v == "BUY")
    bear_w = sum(w for v, w, _ in votes if v == "SELL")
    bull_p = round(bull_w / TOTAL_WEIGHT * 100, 1)
    bear_p = round(bear_w / TOTAL_WEIGHT * 100, 1)

    if bull_p >= SIGNAL_THRESH:
        signal, conf = "BUY",  int(bull_p)
    elif bear_p >= SIGNAL_THRESH:
        signal, conf = "SELL", int(bear_p)
    else:
        signal, conf = "WAIT", int(max(bull_p, bear_p))

    # Top-3 reason labels
    active_labels = [lbl for v, _, lbl in votes if v in ("BUY", "SELL") and lbl]
    reason = " | ".join(active_labels[:3]) if active_labels else "Mixed signals"

    return {
        "signal":     signal,
        "confidence": conf,
        "reason":     reason,
        "bull_pct":   bull_p,
        "bear_pct":   bear_p,
        "data_source": data_mode.get(pair_id, "demo"),
        "price":      last_close,
        "candles":    candle_cnt,
        "indicators": {
            "rsi":       round(rsi,   2) if not np.isnan(rsi)   else None,
            "bb_upper":  round(bb_u,  6) if not np.isnan(bb_u)  else None,
            "bb_lower":  round(bb_l,  6) if not np.isnan(bb_l)  else None,
            "stoch_k":   round(k_v,   2) if not np.isnan(k_v)   else None,
            "stoch_d":   round(d_v,   2) if not np.isnan(d_v)   else None,
            "macd_hist": round(h_curr, 8) if not np.isnan(h_curr) else None,
            "ema9":      round(ema9,  6) if not np.isnan(ema9)  else None,
            "ema21":     round(ema21, 6) if not np.isnan(ema21) else None,
        },
    }


# ══════════════════════════════════════════════════════════
# WEBSOCKET CONNECTION MANAGER
# ══════════════════════════════════════════════════════════
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        log.info(f"WS connect  (total={len(self.active)})")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        log.info(f"WS disconnect (total={len(self.active)})")

    async def broadcast(self, message: str):
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


# ══════════════════════════════════════════════════════════
# PAIR SEEDING + REFRESH
# ══════════════════════════════════════════════════════════
def _fetch_pair_sync(pair_id: str, limit: int) -> Optional[pd.DataFrame]:
    """Synchronous fetch — run via executor to avoid blocking the event loop."""
    cfg = PAIRS[pair_id]
    if cfg["source"] == "binance":
        return fetch_binance_candles(cfg["symbol"], limit)
    return fetch_yahoo_candles(cfg["symbol"], limit)


async def _seed_one(pair_id: str) -> bool:
    log.info(f"Seeding {pair_id} …")
    df = await run_in_threadpool(_fetch_pair_sync, pair_id, SEED_CANDLES)
    async with store_lock:
        if df is not None and len(df) >= MIN_CANDLES:
            price_store[pair_id] = df
            data_mode[pair_id]   = "real"
            return True
        else:
            price_store[pair_id] = generate_demo_candles(pair_id, SEED_CANDLES)
            data_mode[pair_id]   = "demo"
            log.warning(f"  ⚠ {pair_id}: real fetch failed, using demo candles")
            return False


async def seed_all():
    log.info("═══ Seeding all 28 pairs … ═══")
    real = 0
    for idx, pid in enumerate(PAIRS):
        ok = await _seed_one(pid)
        if ok:
            real += 1
        # Rate-limiting guard: brief sleep every 5 pairs
        await asyncio.sleep(1.0 if (idx + 1) % 5 == 0 else 0.25)
    log.info(f"═══ Seed complete: {real}/28 real, {28 - real}/28 demo ═══")


async def refresh_all():
    log.info("Refreshing candles …")
    for idx, pid in enumerate(PAIRS):
        df_new = await run_in_threadpool(_fetch_pair_sync, pid, 10)
        if df_new is not None and len(df_new) >= 1:
            async with store_lock:
                existing = price_store[pid]
                if existing is not None:
                    combined        = pd.concat([existing, df_new]).drop_duplicates()
                    price_store[pid] = combined.tail(MAX_CANDLES)
                else:
                    price_store[pid] = df_new.tail(MAX_CANDLES)
                data_mode[pid] = "real"
        await asyncio.sleep(0.8 if (idx + 1) % 5 == 0 else 0.15)
    log.info("Refresh done.")


# ══════════════════════════════════════════════════════════
# SIGNAL PAYLOAD BUILDER
# ══════════════════════════════════════════════════════════
async def build_payload() -> dict:
    now       = datetime.now(timezone.utc)
    countdown = 60 - now.second

    items = []
    async with store_lock:
        for pid, cfg in PAIRS.items():
            df  = price_store[pid]
            sig = generate_signal(pid, df)
            # price change %
            if df is not None and len(df) > 1:
                prev  = float(df["Close"].iloc[-2])
                curr  = sig.get("price") or prev
                chg   = (curr - prev) / prev * 100 if prev else 0.0
            else:
                chg = 0.0
            items.append({
                "pair_id":    pid,
                "pair_name":  cfg["name"],
                "category":   cfg["category"],
                "payout":     cfg["payout"],
                "signal":     sig["signal"],
                "confidence": sig["confidence"],
                "reason":     sig["reason"],
                "bull_pct":   sig["bull_pct"],
                "bear_pct":   sig["bear_pct"],
                "data_source": sig["data_source"],
                "price":      sig.get("price"),
                "change_pct": round(chg, 4),
                "candles":    sig.get("candles", 0),
                "countdown":  countdown,
                "indicators": sig.get("indicators", {}),
                "ts":         now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })

    real_cnt = sum(1 for it in items if it["data_source"] == "real")
    mode     = "REAL" if real_cnt >= 14 else "DEMO"

    return {
        "type":        "signals",
        "mode":        mode,
        "real_pairs":  real_cnt,
        "demo_pairs":  len(items) - real_cnt,
        "data":        items,
        "server_time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "countdown":   countdown,
        "version":     VERSION,
    }


# ══════════════════════════════════════════════════════════
# BACKGROUND TASKS
# ══════════════════════════════════════════════════════════
async def _refresh_loop():
    await asyncio.sleep(REFRESH_SECS + 10)   # wait after startup seed
    while True:
        try:
            await refresh_all()
        except Exception as exc:
            log.error(f"refresh_loop error: {exc}")
        await asyncio.sleep(REFRESH_SECS)


async def _broadcast_loop():
    await asyncio.sleep(4)   # brief startup delay
    while True:
        try:
            if manager.active:
                payload = await build_payload()
                await manager.broadcast(json.dumps(payload, default=str))
        except Exception as exc:
            log.error(f"broadcast_loop error: {exc}")
        await asyncio.sleep(BROADCAST_SECS)


# ══════════════════════════════════════════════════════════
# FASTAPI APPLICATION
# ══════════════════════════════════════════════════════════
app = FastAPI(title="QX OTC Signal Bot", version=VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    log.info(f"QX OTC Signal Bot v{VERSION} — startup")
    asyncio.create_task(seed_all())
    asyncio.create_task(_refresh_loop())
    asyncio.create_task(_broadcast_loop())


@app.get("/")
async def root():
    real = sum(1 for v in data_mode.values() if v == "real")
    return {
        "name":       "QX OTC Signal Bot",
        "version":    VERSION,
        "status":     "running",
        "mode":       "REAL" if real >= 14 else "DEMO",
        "pairs":      len(PAIRS),
        "real_pairs": real,
        "demo_pairs": len(PAIRS) - real,
        "ws_clients": len(manager.active),
        "ws_url":     "/ws",
        "signals_url": "/api/signals",
    }


@app.get("/api/status")
async def api_status():
    real = sum(1 for v in data_mode.values() if v == "real")
    pair_info = {}
    async with store_lock:
        for pid, df in price_store.items():
            pair_info[pid] = {
                "candles": len(df) if df is not None else 0,
                "mode":    data_mode[pid],
                "name":    PAIRS[pid]["name"],
            }
    return {
        "status":     "ok",
        "version":    VERSION,
        "real_pairs": real,
        "demo_pairs": len(PAIRS) - real,
        "ws_clients": len(manager.active),
        "pairs":      pair_info,
    }


@app.get("/api/signals")
async def api_signals():
    """REST endpoint — returns latest signals for all 28 pairs."""
    return await build_payload()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # Immediately send current signals on connect
        payload = await build_payload()
        await ws.send_text(json.dumps(payload, default=str))
        # Keep connection alive; handle ping
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                if msg.strip().lower() == "ping":
                    await ws.send_text("pong")
            except asyncio.TimeoutError:
                pass   # healthy silence — broadcast loop handles updates
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug(f"ws_endpoint exception: {exc}")
    finally:
        manager.disconnect(ws)


# ══════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, log_level="info")
