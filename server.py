import asyncio
import os
import json
import time
import random
from datetime import datetime, timezone
from typing import List, Dict, Optional, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import numpy as np
from collections import deque
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Try Quotex library ────────────────────────────────────────────────────────
QUOTEX_AVAILABLE = False
try:
    from quotexpy.new import Quotex as QuotexClient
    QUOTEX_AVAILABLE = True
    logger.info("✅ quotexpy loaded")
except Exception:
    try:
        from pyquotex.stable_api import Quotex as QuotexClient
        QUOTEX_AVAILABLE = True
        logger.info("✅ pyquotex loaded")
    except Exception:
        logger.warning("⚠️  No Quotex library – running in DEMO mode")

QX_EMAIL    = os.getenv("QX_EMAIL", "")
QX_PASSWORD = os.getenv("QX_PASSWORD", "")
PORT        = int(os.getenv("PORT", 8000))
DEMO_MODE   = not (QUOTEX_AVAILABLE and QX_EMAIL and QX_PASSWORD)

# ── ALL 28 QX OTC PAIRS ───────────────────────────────────────────────────────
OTC_PAIRS = [
    # Forex OTC
    {"id": "EURUSD_otc",  "name": "EUR/USD",  "category": "Forex",     "base_price": 1.08500,  "payout": 80},
    {"id": "GBPUSD_otc",  "name": "GBP/USD",  "category": "Forex",     "base_price": 1.26500,  "payout": 38},
    {"id": "USDJPY_otc",  "name": "USD/JPY",  "category": "Forex",     "base_price": 149.500,  "payout": 93},
    {"id": "AUDUSD_otc",  "name": "AUD/USD",  "category": "Forex",     "base_price": 0.65200,  "payout": 88},
    {"id": "EURJPY_otc",  "name": "EUR/JPY",  "category": "Forex",     "base_price": 161.800,  "payout": 85},
    {"id": "USDCAD_otc",  "name": "USD/CAD",  "category": "Forex",     "base_price": 1.36500,  "payout": 84},
    {"id": "EURGBP_otc",  "name": "EUR/GBP",  "category": "Forex",     "base_price": 0.85500,  "payout": 95},
    {"id": "USDCHF_otc",  "name": "USD/CHF",  "category": "Forex",     "base_price": 0.89500,  "payout": 85},
    {"id": "AUDCAD_otc",  "name": "AUD/CAD",  "category": "Forex",     "base_price": 0.89000,  "payout": 88},
    {"id": "EURAUD_otc",  "name": "EUR/AUD",  "category": "Forex",     "base_price": 1.65000,  "payout": 82},
    {"id": "GBPJPY_otc",  "name": "GBP/JPY",  "category": "Forex",     "base_price": 188.500,  "payout": 90},
    {"id": "CHFJPY_otc",  "name": "CHF/JPY",  "category": "Forex",     "base_price": 167.000,  "payout": 85},
    {"id": "NZDCAD_otc",  "name": "NZD/CAD",  "category": "Forex",     "base_price": 0.82000,  "payout": 87},
    {"id": "NZDCHF_otc",  "name": "NZD/CHF",  "category": "Forex",     "base_price": 0.52000,  "payout": 87},
    {"id": "AUDCHF_otc",  "name": "AUD/CHF",  "category": "Forex",     "base_price": 0.58000,  "payout": 86},
    {"id": "EURCHF_otc",  "name": "EUR/CHF",  "category": "Forex",     "base_price": 0.97000,  "payout": 78},
    {"id": "CADJPY_otc",  "name": "CAD/JPY",  "category": "Forex",     "base_price": 109.500,  "payout": 85},
    {"id": "GBPAUD_otc",  "name": "GBP/AUD",  "category": "Forex",     "base_price": 1.94000,  "payout": 83},
    {"id": "GBPCAD_otc",  "name": "GBP/CAD",  "category": "Forex",     "base_price": 1.73000,  "payout": 82},
    {"id": "EURCAD_otc",  "name": "EUR/CAD",  "category": "Forex",     "base_price": 1.48000,  "payout": 83},
    {"id": "NZDUSD_otc",  "name": "NZD/USD",  "category": "Forex",     "base_price": 0.60500,  "payout": 86},
    {"id": "GBPCHF_otc",  "name": "GBP/CHF",  "category": "Forex",     "base_price": 1.13500,  "payout": 84},
    # Commodities OTC
    {"id": "XAUUSD_otc",  "name": "Gold",     "category": "Commodity", "base_price": 2320.00,  "payout": 87},
    {"id": "XAGUSD_otc",  "name": "Silver",   "category": "Commodity", "base_price": 27.500,   "payout": 93},
    {"id": "UKOIL_otc",   "name": "UK Brent", "category": "Commodity", "base_price": 85.500,   "payout": 93},
    {"id": "USOIL_otc",   "name": "US Crude", "category": "Commodity", "base_price": 80.500,   "payout": 84},
    # Crypto OTC
    {"id": "BTCUSD_otc",  "name": "Bitcoin",  "category": "Crypto",    "base_price": 65000.00, "payout": 80},
    {"id": "ETHUSD_otc",  "name": "Ethereum", "category": "Crypto",    "base_price": 3500.00,  "payout": 66},
]

# ── Signal Engine ─────────────────────────────────────────────────────────────
class SignalEngine:
    def __init__(self, pair_id: str):
        self.pair_id = pair_id
        self.closes  = deque(maxlen=120)
        self.highs   = deque(maxlen=120)
        self.lows    = deque(maxlen=120)
        self.opens   = deque(maxlen=120)

    def add_candle(self, o, h, l, c):
        self.opens.append(o); self.highs.append(h)
        self.lows.append(l);  self.closes.append(c)

    def _ema(self, data, period):
        if not data: return 0.0
        d = list(data)
        k = 2.0 / (period + 1)
        e = d[0]
        for v in d[1:]: e = v * k + e * (1 - k)
        return e

    def calc_rsi(self, period=14):
        c = list(self.closes)
        if len(c) < period + 2: return None
        deltas = np.diff(c[-(period+1):])
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        ag, al = np.mean(gains), np.mean(losses)
        if al == 0: return 100.0
        return 100.0 - (100.0 / (1.0 + ag/al))

    def calc_macd(self):
        c = list(self.closes)
        if len(c) < 35: return None, None, None
        macd = self._ema(c, 12) - self._ema(c, 26)
        signal = macd * 0.85
        return macd, signal, macd - signal

    def calc_bollinger(self, period=20):
        c = list(self.closes)
        if len(c) < period: return None, None, None, None
        sl = c[-period:]
        mid = float(np.mean(sl))
        std = float(np.std(sl))
        upper = mid + 2*std; lower = mid - 2*std
        pct = (c[-1] - lower) / (upper - lower) if (upper-lower) > 0 else 0.5
        return upper, mid, lower, pct

    def calc_stochastic(self, k_period=14):
        c, h, l = list(self.closes), list(self.highs), list(self.lows)
        if len(c) < k_period: return None, None
        hh = max(h[-k_period:]); ll = min(l[-k_period:])
        if hh == ll: return 50.0, 50.0
        k = 100.0 * (c[-1] - ll) / (hh - ll)
        d_vals = []
        for i in range(3):
            idx = -(i+1)
            if abs(idx) > len(c): break
            _hh = max(h[max(-k_period+idx, -len(h)):idx if idx else len(h)])
            _ll = min(l[max(-k_period+idx, -len(l)):idx if idx else len(l)])
            if _hh != _ll: d_vals.append(100.0*(c[idx]-_ll)/(_hh-_ll))
        return k, float(np.mean(d_vals)) if d_vals else k

    def detect_otc_pattern(self):
        c, o = list(self.closes), list(self.opens)
        if len(c) < 5: return 0.0
        dirs = [1 if c[-i] > o[-i] else -1 for i in range(3, 0, -1)]
        if all(d == -1 for d in dirs): return 1.0
        if all(d ==  1 for d in dirs): return -1.0
        if self.highs:
            h_last = list(self.highs)[-1]; l_last = list(self.lows)[-1]
            rng = h_last - l_last
            if rng > 0:
                lw = min(o[-1],c[-1]) - l_last
                uw = h_last - max(o[-1],c[-1])
                if lw/rng > 0.65: return 0.6
                if uw/rng > 0.65: return -0.6
        return 0.0

    def generate_signal(self):
        if len(self.closes) < 28:
            return {"signal": "WAIT", "confidence": 0, "reason": "Collecting data…"}
        votes = []
        rsi = self.calc_rsi()
        if rsi is not None:
            if   rsi <= 25: votes.append((1,  3.0, f"RSI oversold {rsi:.1f}"))
            elif rsi <= 35: votes.append((1,  1.8, f"RSI low {rsi:.1f}"))
            elif rsi >= 75: votes.append((-1, 3.0, f"RSI overbought {rsi:.1f}"))
            elif rsi >= 65: votes.append((-1, 1.8, f"RSI high {rsi:.1f}"))
            else:           votes.append((1 if rsi<50 else -1, 0.5, f"RSI neutral {rsi:.1f}"))
        macd, sig, hist = self.calc_macd()
        if macd is not None:
            if   macd>0 and hist>0: votes.append((1,  2.0, "MACD bullish"))
            elif macd<0 and hist<0: votes.append((-1, 2.0, "MACD bearish"))
            elif hist>0:            votes.append((1,  1.0, "MACD rising"))
            else:                   votes.append((-1, 1.0, "MACD falling"))
        _, _, _, pct_b = self.calc_bollinger()
        if pct_b is not None:
            if   pct_b <= 0.05: votes.append((1,  3.0, "Price at lower BB"))
            elif pct_b <= 0.20: votes.append((1,  1.5, "Near lower BB"))
            elif pct_b >= 0.95: votes.append((-1, 3.0, "Price at upper BB"))
            elif pct_b >= 0.80: votes.append((-1, 1.5, "Near upper BB"))
            else:               votes.append((1 if pct_b<0.5 else -1, 0.4, "BB mid"))
        k, d = self.calc_stochastic()
        if k is not None:
            if   k<20 and d<20: votes.append((1,  2.5, f"Stoch oversold {k:.0f}"))
            elif k>80 and d>80: votes.append((-1, 2.5, f"Stoch overbought {k:.0f}"))
            elif k>d:           votes.append((1,  1.0, "Stoch K>D"))
            else:               votes.append((-1, 1.0, "Stoch K<D"))
        cross = self._ema(list(self.closes), 9) - self._ema(list(self.closes), 21)
        if len(self.closes) >= 21:
            votes.append((1 if cross>0 else -1, 1.5, f"EMA {'bull' if cross>0 else 'bear'}"))
        otc = self.detect_otc_pattern()
        if abs(otc)>0: votes.append((1 if otc>0 else -1, abs(otc)*2.5, "OTC reversal"))
        if not votes: return {"signal":"WAIT","confidence":0,"reason":"No data"}
        bull = sum(w for d,w,_ in votes if d==1)
        bear = sum(w for d,w,_ in votes if d==-1)
        total = bull+bear
        bull_pct = (bull/total)*100 if total else 50
        bear_pct = (bear/total)*100 if total else 50
        reasons = " | ".join(r for _,_,r in sorted(votes,key=lambda x:x[1],reverse=True)[:3])
        if   bull_pct >= 60: return {"signal":"BUY",  "confidence":min(int(bull_pct),95),"reason":reasons}
        elif bear_pct >= 60: return {"signal":"SELL", "confidence":min(int(bear_pct),95),"reason":reasons}
        else:                return {"signal":"WAIT", "confidence":int(max(bull_pct,bear_pct)),"reason":"Mixed signals"}

# ── Price Simulator ───────────────────────────────────────────────────────────
class PriceSimulator:
    def __init__(self, base, pair_id):
        self.price = base; self.pair_id = pair_id
        self.trend = random.choice([-1,1])
        cat = next((p["category"] for p in OTC_PAIRS if p["id"]==pair_id), "Forex")
        if cat == "Crypto":    self.vol_mult = 50.0
        elif cat == "Commodity": self.vol_mult = 0.5
        else: self.vol_mult = 1.0
        self.pip = 0.01 if "JPY" in pair_id else (0.5 if cat=="Commodity" else (10.0 if cat=="Crypto" else 0.0001))
        self.trend_life = random.randint(5,20); self.tick = 0
    def next_candle(self):
        self.tick += 1
        if self.tick%self.trend_life==0:
            self.trend=-self.trend; self.trend_life=random.randint(5,20)
        drift = self.trend * self.pip * self.vol_mult * 0.3
        vol   = self.pip * self.vol_mult * (2+random.random()*6)
        o = self.price; c = o+drift+(random.random()-0.5)*vol
        h = max(o,c)+abs(random.gauss(0,vol*0.4))
        l = min(o,c)-abs(random.gauss(0,vol*0.4))
        self.price = c
        dp = 2 if self.pip>=0.5 else (0 if self.pip>=5 else 5)
        return {k:round(v,dp) for k,v in {"open":o,"high":h,"low":l,"close":c}.items()}

# ── WebSocket Manager ─────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self): self.active: Set[WebSocket] = set()
    async def connect(self, ws):
        await ws.accept(); self.active.add(ws)
        logger.info(f"Client +1 (total {len(self.active)})")
    def disconnect(self, ws):
        self.active.discard(ws)
    async def broadcast(self, data):
        msg = json.dumps(data); dead = set()
        for ws in list(self.active):
            try: await ws.send_text(msg)
            except: dead.add(ws)
        for ws in dead: self.active.discard(ws)

# ── App ───────────────────────────────────────────────────────────────────────
app     = FastAPI(title="QX OTC Signal Bot")
manager = ConnectionManager()
engines: Dict[str,SignalEngine]   = {}
sims:    Dict[str,PriceSimulator] = {}
latest_signals: Dict[str,dict]   = {}
quotex_client = None

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for p in OTC_PAIRS:
    engines[p["id"]] = SignalEngine(p["id"])
    sims[p["id"]]    = PriceSimulator(p["base_price"], p["id"])

async def connect_quotex():
    global quotex_client
    if not QUOTEX_AVAILABLE or not QX_EMAIL or not QX_PASSWORD: return False
    try:
        client = QuotexClient(email=QX_EMAIL, password=QX_PASSWORD, browser=False)
        loop = asyncio.get_event_loop()
        check, reason = await loop.run_in_executor(None, client.connect)
        if check: quotex_client = client; logger.info("✅ QX Broker connected"); return True
        logger.error(f"QX failed: {reason}")
    except Exception as e: logger.error(f"QX error: {e}")
    return False

async def fetch_candles_quotex(pair_id):
    global quotex_client
    if not quotex_client: return None
    try:
        loop = asyncio.get_event_loop()
        candles = await loop.run_in_executor(None, lambda: quotex_client.get_candles(pair_id.upper(), 60, 100))
        if candles:
            result=[]
            for c in (candles if isinstance(candles,list) else []):
                if isinstance(c,dict):
                    result.append({"open":float(c.get("open",c.get("o",0))),"high":float(c.get("max",c.get("high",c.get("h",0)))),"low":float(c.get("min",c.get("low",c.get("l",0)))),"close":float(c.get("close",c.get("c",0)))})
            return result or None
    except Exception as e: logger.warning(f"Candle fetch {pair_id}: {e}")
    return None

async def signal_loop():
    connected = await connect_quotex()
    mode = "LIVE" if connected else "DEMO"
    logger.info(f"Mode: {mode} | Pairs: {len(OTC_PAIRS)}")
    for p in OTC_PAIRS:
        pid = p["id"]
        if connected:
            candles = await fetch_candles_quotex(pid)
            if candles:
                for c in candles: engines[pid].add_candle(c["open"],c["high"],c["low"],c["close"])
                continue
        for _ in range(60):
            c = sims[pid].next_candle()
            engines[pid].add_candle(c["open"],c["high"],c["low"],c["close"])
    logger.info("All engines seeded – broadcasting signals")
    while True:
        now = datetime.now(timezone.utc)
        sec_left = 60 - now.second
        await asyncio.sleep(1)
        payload = []
        for p in OTC_PAIRS:
            pid = p["id"]
            eng = engines[pid]
            if now.second == 0 or len(eng.closes)==0:
                if connected:
                    candles = await fetch_candles_quotex(pid)
                    if candles:
                        c=candles[-1]; eng.add_candle(c["open"],c["high"],c["low"],c["close"])
                else:
                    c=sims[pid].next_candle(); eng.add_candle(c["open"],c["high"],c["low"],c["close"])
            sig = eng.generate_signal()
            current = float(eng.closes[-1]) if eng.closes else p["base_price"]
            rec = {"pair_id":pid,"pair_name":p["name"],"category":p["category"],"payout":p["payout"],"signal":sig["signal"],"confidence":sig["confidence"],"reason":sig["reason"],"price":current,"candles":len(eng.closes),"mode":mode,"countdown":sec_left,"ts":now.isoformat()}
            latest_signals[pid] = rec
            payload.append(rec)
        await manager.broadcast({"type":"signals","mode":mode,"data":payload,"server_time":now.isoformat()})

@app.on_event("startup")
async def startup(): asyncio.create_task(signal_loop())

@app.get("/")
async def root(): return {"status":"ok","mode":"DEMO" if DEMO_MODE else "LIVE","pairs":len(OTC_PAIRS),"version":"2.0"}

@app.get("/api/status")
async def status():
    return {"status":"running","mode":"DEMO" if DEMO_MODE else "LIVE","pairs":[p["name"] for p in OTC_PAIRS],"categories":{"Forex":sum(1 for p in OTC_PAIRS if p["category"]=="Forex"),"Commodity":sum(1 for p in OTC_PAIRS if p["category"]=="Commodity"),"Crypto":sum(1 for p in OTC_PAIRS if p["category"]=="Crypto")},"clients":len(manager.active)}

@app.get("/api/signals")
async def signals(): return {"signals":list(latest_signals.values())}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    if latest_signals:
        await ws.send_text(json.dumps({"type":"signals","mode":"DEMO" if DEMO_MODE else "LIVE","data":list(latest_signals.values()),"server_time":datetime.now(timezone.utc).isoformat()}))
    try:
        while True: await ws.receive_text()
    except (WebSocketDisconnect, Exception): manager.disconnect(ws)

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=False)
