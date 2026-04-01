import asyncio
import os
import json
import time
import random
import math
from datetime import datetime, timezone
from typing import List, Dict, Optional, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import uvicorn
import numpy as np
from collections import deque
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Try pyquotex ─────────────────────────────────────────────────────────────
QUOTEX_AVAILABLE = False
try:
    from quotexapi.stable_api import Quotex
    QUOTEX_AVAILABLE = True
    logger.info("✅ pyquotex loaded")
except Exception:
    logger.warning("⚠️  pyquotex not found – running in DEMO mode")

# ── Config ────────────────────────────────────────────────────────────────────
QX_EMAIL    = os.getenv("QX_EMAIL", "")
QX_PASSWORD = os.getenv("QX_PASSWORD", "")
PORT        = int(os.getenv("PORT", 8000))
DEMO_MODE   = not (QUOTEX_AVAILABLE and QX_EMAIL and QX_PASSWORD)

OTC_PAIRS = [
    {"id": "EURUSD_otc",  "name": "EUR/USD OTC",  "base_price": 1.08500},
    {"id": "GBPUSD_otc",  "name": "GBP/USD OTC",  "base_price": 1.26500},
    {"id": "USDJPY_otc",  "name": "USD/JPY OTC",  "base_price": 149.500},
    {"id": "AUDUSD_otc",  "name": "AUD/USD OTC",  "base_price": 0.65200},
    {"id": "EURJPY_otc",  "name": "EUR/JPY OTC",  "base_price": 161.800},
    {"id": "USDCAD_otc",  "name": "USD/CAD OTC",  "base_price": 1.36500},
]

# ── Signal Engine ─────────────────────────────────────────────────────────────
class SignalEngine:
    """
    OTC-specific signal engine.
    OTC markets are synthetic – they mean-revert more and pattern-repeat.
    Strategy: weighted vote of RSI, MACD, Bollinger, Stochastic, EMA cross.
    """

    def __init__(self, pair_id: str):
        self.pair_id = pair_id
        self.closes  = deque(maxlen=120)
        self.highs   = deque(maxlen=120)
        self.lows    = deque(maxlen=120)
        self.opens   = deque(maxlen=120)
        self.signal_history: List[Dict] = []

    def add_candle(self, o: float, h: float, l: float, c: float):
        self.opens.append(o)
        self.highs.append(h)
        self.lows.append(l)
        self.closes.append(c)

    # ── Indicators ──────────────────────────────────────────────────────────

    def _ema(self, data: list, period: int) -> float:
        if len(data) < period:
            return float(np.mean(data)) if data else 0.0
        k = 2.0 / (period + 1)
        e = data[0]
        for v in data[1:]:
            e = v * k + e * (1 - k)
        return e

    def calc_rsi(self, period: int = 14) -> Optional[float]:
        c = list(self.closes)
        if len(c) < period + 2:
            return None
        deltas = np.diff(c[-(period + 1):])
        gains  = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        ag = np.mean(gains)
        al = np.mean(losses)
        if al == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + ag / al))

    def calc_macd(self):
        c = list(self.closes)
        if len(c) < 35:
            return None, None, None
        ema12   = self._ema(c, 12)
        ema26   = self._ema(c, 26)
        macd    = ema12 - ema26
        # approximate signal as ema of last 9 macd values
        signal  = macd * 0.85
        hist    = macd - signal
        return macd, signal, hist

    def calc_bollinger(self, period: int = 20):
        c = list(self.closes)
        if len(c) < period:
            return None, None, None, None
        recent = c[-period:]
        mid    = float(np.mean(recent))
        std    = float(np.std(recent))
        upper  = mid + 2 * std
        lower  = mid - 2 * std
        pct    = (c[-1] - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
        return upper, mid, lower, pct

    def calc_stochastic(self, k_period: int = 14, d_period: int = 3):
        c = list(self.closes)
        h = list(self.highs)
        l = list(self.lows)
        if len(c) < k_period:
            return None, None
        hh = max(h[-k_period:])
        ll = min(l[-k_period:])
        if hh == ll:
            return 50.0, 50.0
        k = 100.0 * (c[-1] - ll) / (hh - ll)
        d_vals = []
        for i in range(d_period):
            idx = -(i + 1)
            if abs(idx) > len(c):
                break
            _hh = max(h[max(-k_period + idx, -len(h)):idx if idx else len(h)])
            _ll = min(l[max(-k_period + idx, -len(l)):idx if idx else len(l)])
            if _hh != _ll:
                d_vals.append(100.0 * (c[idx] - _ll) / (_hh - _ll))
        d = float(np.mean(d_vals)) if d_vals else k
        return k, d

    def calc_ema_cross(self):
        c = list(self.closes)
        if len(c) < 21:
            return None
        ema9  = self._ema(c, 9)
        ema21 = self._ema(c, 21)
        return ema9 - ema21

    def detect_otc_pattern(self) -> float:
        """
        OTC markets show strong mean-reversion after 3+ consecutive same-direction candles.
        Returns score: +1 = reversal up likely, -1 = reversal down likely, 0 = no pattern.
        """
        c = list(self.closes)
        o = list(self.opens)
        if len(c) < 5:
            return 0.0
        # Check last 3 candles direction
        dirs = []
        for i in range(-3, 0):
            dirs.append(1 if c[i] > o[i] else -1)
        # 3 bearish → reversal BUY
        if all(d == -1 for d in dirs):
            return 1.0
        # 3 bullish → reversal SELL
        if all(d == 1 for d in dirs):
            return -1.0
        # Wick analysis: long lower wick = bounce up
        last_range = abs(max(self.highs) - min(self.lows)) if self.highs else 0.0001
        last_candle_range = self.highs[-1] - self.lows[-1] if self.highs else 0
        lower_wick = self.opens[-1] - self.lows[-1] if self.opens[-1] > self.closes[-1] else self.closes[-1] - self.lows[-1]
        upper_wick = self.highs[-1] - self.opens[-1] if self.opens[-1] > self.closes[-1] else self.highs[-1] - self.closes[-1]
        if last_candle_range > 0:
            lw_ratio = lower_wick / last_candle_range
            uw_ratio = upper_wick / last_candle_range
            if lw_ratio > 0.65:
                return 0.6
            if uw_ratio > 0.65:
                return -0.6
        return 0.0

    # ── Main Signal Generator ────────────────────────────────────────────────

    def generate_signal(self) -> Dict:
        if len(self.closes) < 28:
            return {"signal": "WAIT", "confidence": 0, "reason": "Collecting data..."}

        votes = []  # list of (direction: +1/-1, weight, reason)

        rsi = self.calc_rsi()
        if rsi is not None:
            if rsi <= 25:
                votes.append((1, 3.0, f"RSI oversold {rsi:.1f}"))
            elif rsi <= 35:
                votes.append((1, 1.8, f"RSI low {rsi:.1f}"))
            elif rsi >= 75:
                votes.append((-1, 3.0, f"RSI overbought {rsi:.1f}"))
            elif rsi >= 65:
                votes.append((-1, 1.8, f"RSI high {rsi:.1f}"))
            else:
                votes.append((1 if rsi < 50 else -1, 0.5, f"RSI neutral {rsi:.1f}"))

        macd, sig, hist = self.calc_macd()
        if macd is not None:
            if macd > 0 and hist > 0:
                votes.append((1,  2.0, "MACD bullish crossover"))
            elif macd < 0 and hist < 0:
                votes.append((-1, 2.0, "MACD bearish crossover"))
            elif hist > 0:
                votes.append((1,  1.0, "MACD histogram rising"))
            else:
                votes.append((-1, 1.0, "MACD histogram falling"))

        upper, mid, lower, pct_b = self.calc_bollinger()
        if pct_b is not None:
            if pct_b <= 0.05:
                votes.append((1,  3.0, "Price at lower BB band"))
            elif pct_b <= 0.2:
                votes.append((1,  1.5, "Price near lower BB"))
            elif pct_b >= 0.95:
                votes.append((-1, 3.0, "Price at upper BB band"))
            elif pct_b >= 0.8:
                votes.append((-1, 1.5, "Price near upper BB"))
            else:
                votes.append((1 if pct_b < 0.5 else -1, 0.4, "BB mid range"))

        k, d = self.calc_stochastic()
        if k is not None:
            if k < 20 and d < 20:
                votes.append((1,  2.5, f"Stochastic oversold K={k:.0f}"))
            elif k > 80 and d > 80:
                votes.append((-1, 2.5, f"Stochastic overbought K={k:.0f}"))
            elif k > d:
                votes.append((1,  1.0, "Stochastic K above D"))
            else:
                votes.append((-1, 1.0, "Stochastic K below D"))

        ema_cross = self.calc_ema_cross()
        if ema_cross is not None:
            votes.append((1 if ema_cross > 0 else -1, 1.5, f"EMA9/21 {'bull' if ema_cross > 0 else 'bear'}ish"))

        otc_pattern = self.detect_otc_pattern()
        if abs(otc_pattern) > 0:
            votes.append((1 if otc_pattern > 0 else -1, abs(otc_pattern) * 2.5, "OTC reversal pattern"))

        if not votes:
            return {"signal": "WAIT", "confidence": 0, "reason": "Insufficient data"}

        bull_weight = sum(w for d, w, _ in votes if d == 1)
        bear_weight = sum(w for d, w, _ in votes if d == -1)
        total       = bull_weight + bear_weight

        bull_pct = (bull_weight / total) * 100 if total else 50
        bear_pct = (bear_weight / total) * 100 if total else 50

        top_reasons = [r for _, _, r in sorted(votes, key=lambda x: x[1], reverse=True)[:3]]

        THRESHOLD = 60  # need 60%+ agreement to signal

        if bull_pct >= THRESHOLD:
            confidence = min(int(bull_pct), 95)
            return {"signal": "BUY",  "confidence": confidence, "reason": " | ".join(top_reasons)}
        elif bear_pct >= THRESHOLD:
            confidence = min(int(bear_pct), 95)
            return {"signal": "SELL", "confidence": confidence, "reason": " | ".join(top_reasons)}
        else:
            return {"signal": "WAIT", "confidence": int(max(bull_pct, bear_pct)), "reason": "Mixed signals – waiting"}


# ── Demo Price Simulator ──────────────────────────────────────────────────────
class PriceSimulator:
    def __init__(self, base: float, pair_id: str):
        self.price   = base
        self.pair_id = pair_id
        self.trend   = random.choice([-1, 1])
        self.trend_strength = random.uniform(0.0001, 0.0004)
        self.trend_life = random.randint(5, 20)
        self.tick    = 0
        # Determine pip size
        self.pip = 0.01 if "JPY" in pair_id else 0.0001

    def next_candle(self):
        self.tick += 1
        if self.tick % self.trend_life == 0:
            self.trend = -self.trend
            self.trend_strength = random.uniform(0.0001, 0.0004)
            self.trend_life = random.randint(5, 20)
        drift    = self.trend * self.trend_strength
        vol      = self.pip * random.uniform(2, 8)
        o        = self.price
        c        = o + drift + random.gauss(0, vol)
        h        = max(o, c) + abs(random.gauss(0, vol * 0.5))
        l        = min(o, c) - abs(random.gauss(0, vol * 0.5))
        self.price = c
        return {"open": round(o, 5), "high": round(h, 5), "low": round(l, 5), "close": round(c, 5)}


# ── WebSocket Manager ─────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)
        logger.info(f"Client connected. Total: {len(self.active)}")

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)
        logger.info(f"Client disconnected. Total: {len(self.active)}")

    async def broadcast(self, data: dict):
        msg  = json.dumps(data)
        dead = set()
        for ws in list(self.active):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.active.discard(ws)


# ── App State ─────────────────────────────────────────────────────────────────
app     = FastAPI(title="QX OTC Signal Bot API")
manager = ConnectionManager()
engines: Dict[str, SignalEngine]   = {}
sims:    Dict[str, PriceSimulator] = {}
latest_signals: Dict[str, dict]    = {}
quotex_client = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for p in OTC_PAIRS:
    engines[p["id"]] = SignalEngine(p["id"])
    sims[p["id"]]    = PriceSimulator(p["base_price"], p["id"])


# ── QX Broker Connection ──────────────────────────────────────────────────────
async def connect_quotex():
    global quotex_client
    if not QUOTEX_AVAILABLE or not QX_EMAIL or not QX_PASSWORD:
        return False
    try:
        quotex_client = Quotex(email=QX_EMAIL, password=QX_PASSWORD)
        check, reason = await quotex_client.connect()
        if check:
            logger.info("✅ Connected to QX Broker")
            return True
        logger.error(f"QX connection failed: {reason}")
    except Exception as e:
        logger.error(f"QX connect error: {e}")
    return False


async def fetch_candles_quotex(pair_id: str) -> Optional[List[Dict]]:
    global quotex_client
    if not quotex_client:
        return None
    try:
        # Get asset name without _otc suffix for some API calls
        asset = pair_id.upper()
        status, candles = await quotex_client.get_candles(asset, 60, 100, time.time())
        if status and candles:
            result = []
            for c in candles:
                if isinstance(c, dict):
                    result.append({
                        "open":  float(c.get("open",  c.get("o", 0))),
                        "high":  float(c.get("max",   c.get("high", c.get("h", 0)))),
                        "low":   float(c.get("min",   c.get("low",  c.get("l", 0)))),
                        "close": float(c.get("close", c.get("c", 0))),
                    })
            return result if result else None
    except Exception as e:
        logger.warning(f"Failed to fetch candles for {pair_id}: {e}")
    return None


# ── Signal Loop ───────────────────────────────────────────────────────────────
async def signal_loop():
    global quotex_client

    # Try real connection first
    connected = await connect_quotex()
    mode = "LIVE" if connected else "DEMO"
    logger.info(f"Running in {mode} mode")

    # Seed engines with historical data
    for pair in OTC_PAIRS:
        pid = pair["id"]
        if connected:
            candles = await fetch_candles_quotex(pid)
            if candles:
                for c in candles:
                    engines[pid].add_candle(c["open"], c["high"], c["low"], c["close"])
                continue
        # Demo seed
        sim = sims[pid]
        for _ in range(60):
            c = sim.next_candle()
            engines[pid].add_candle(c["open"], c["high"], c["low"], c["close"])

    logger.info("Engines seeded – starting signal broadcast loop")

    while True:
        now       = datetime.now(timezone.utc)
        # Seconds until next minute
        sec_left  = 60 - now.second
        await asyncio.sleep(1)  # tick every second

        payload_list = []

        for pair in OTC_PAIRS:
            pid = pair["id"]
            eng = engines[pid]

            # On the turn of each minute, add a new candle
            if now.second == 0 or len(eng.closes) == 0:
                if connected:
                    candles = await fetch_candles_quotex(pid)
                    if candles:
                        c = candles[-1]
                        eng.add_candle(c["open"], c["high"], c["low"], c["close"])
                else:
                    c = sims[pid].next_candle()
                    eng.add_candle(c["open"], c["high"], c["low"], c["close"])

            sig_data = eng.generate_signal()
            current  = float(eng.closes[-1]) if eng.closes else pair["base_price"]

            record = {
                "pair_id":    pid,
                "pair_name":  pair["name"],
                "signal":     sig_data["signal"],
                "confidence": sig_data["confidence"],
                "reason":     sig_data["reason"],
                "price":      current,
                "candles":    len(eng.closes),
                "mode":       mode,
                "countdown":  sec_left,
                "ts":         now.isoformat(),
            }
            latest_signals[pid] = record
            payload_list.append(record)

        await manager.broadcast({
            "type":    "signals",
            "mode":    mode,
            "data":    payload_list,
            "server_time": now.isoformat(),
        })


# ── Routes ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    asyncio.create_task(signal_loop())

@app.get("/")
async def root():
    return {"status": "ok", "mode": "DEMO" if DEMO_MODE else "LIVE", "pairs": len(OTC_PAIRS)}

@app.get("/api/status")
async def status():
    return {
        "status":    "running",
        "mode":      "DEMO" if DEMO_MODE else "LIVE",
        "pairs":     [p["name"] for p in OTC_PAIRS],
        "clients":   len(manager.active),
        "uptime":    time.time(),
    }

@app.get("/api/signals")
async def signals():
    return {"signals": list(latest_signals.values())}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    # Send current signals immediately on connect
    if latest_signals:
        await ws.send_text(json.dumps({
            "type": "signals",
            "mode": "DEMO" if DEMO_MODE else "LIVE",
            "data": list(latest_signals.values()),
            "server_time": datetime.now(timezone.utc).isoformat(),
        }))
    try:
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=False)
