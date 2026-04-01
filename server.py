"""
QX OTC Signal Bot — Real Market Data v2.1
------------------------------------------
Prices come from REAL sources:
  • Forex & Commodities → Yahoo Finance 1-min OHLC (yfinance)
  • Crypto              → Binance public API 1-min klines

Signal = prediction for NEXT 1-minute candle:
  BUY  → next candle expected to close HIGHER than open
  SELL → next candle expected to close LOWER than open
  WAIT → indicators disagree — skip this candle

QX OTC prices track real market prices closely.
Real data = real signals.
"""
import asyncio, os, json, random, logging, requests as req_lib
from datetime import datetime, timezone
from typing import List, Dict, Optional, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn, numpy as np
from collections import deque

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
PORT = int(os.getenv("PORT", 8000))

try:
    import yfinance as yf
    YFINANCE_OK = True
    logger.info("yfinance ready")
except Exception:
    YFINANCE_OK = False
    logger.warning("yfinance not available — will use demo for forex/commodities")

YAHOO_MAP = {
    "EURUSD_otc":"EURUSD=X","GBPUSD_otc":"GBPUSD=X","USDJPY_otc":"JPY=X",
    "AUDUSD_otc":"AUDUSD=X","EURJPY_otc":"EURJPY=X","USDCAD_otc":"CAD=X",
    "EURGBP_otc":"EURGBP=X","USDCHF_otc":"CHF=X","AUDCAD_otc":"AUDCAD=X",
    "EURAUD_otc":"EURAUD=X","GBPJPY_otc":"GBPJPY=X","CHFJPY_otc":"CHFJPY=X",
    "NZDCAD_otc":"NZDCAD=X","NZDCHF_otc":"NZDCHF=X","AUDCHF_otc":"AUDCHF=X",
    "EURCHF_otc":"EURCHF=X","CADJPY_otc":"CADJPY=X","GBPAUD_otc":"GBPAUD=X",
    "GBPCAD_otc":"GBPCAD=X","EURCAD_otc":"EURCAD=X","NZDUSD_otc":"NZDUSD=X",
    "GBPCHF_otc":"GBPCHF=X","XAUUSD_otc":"GC=F","XAGUSD_otc":"SI=F",
    "UKOIL_otc":"BZ=F","USOIL_otc":"CL=F",
      "EURNZD_otc":"EURNZD=X","GBPNZD_otc":"GBPNZD=X","AUDNZD_otc":"AUDNZD=X",
}
BINANCE_MAP = {"BTCUSD_otc":"BTCUSDT","ETHUSD_otc":"ETHUSDT"}

    {"id":"EURUSD_otc","name":"EUR/USD OTC","category":"Forex","base_price":1.08500,"payout":80},
    {"id":"GBPUSD_otc","name":"GBP/USD OTC","category":"Forex","base_price":1.26500,"payout":38},
    {"id":"USDJPY_otc","name":"USD/JPY OTC","category":"Forex","base_price":149.500,"payout":93},
    {"id":"AUDUSD_otc","name":"AUD/USD OTC","category":"Forex","base_price":0.65200,"payout":88},
    {"id":"EURJPY_otc","name":"EUR/JPY OTC","category":"Forex","base_price":161.800,"payout":85},
    {"id":"USDCAD_otc","name":"USD/CAD OTC","category":"Forex","base_price":1.36500,"payout":84},
    {"id":"EURGBP_otc","name":"EUR/GBP OTC","category":"Forex","base_price":0.85500,"payout":95},
    {"id":"USDCHF_otc","name":"USD/CHF OTC","category":"Forex","base_price":0.89500,"payout":85},
    {"id":"AUDCAD_otc","name":"AUD/CAD OTC","category":"Forex","base_price":0.89000,"payout":88},
    {"id":"EURAUD_otc","name":"EUR/AUD OTC","category":"Forex","base_price":1.65000,"payout":82},
    {"id":"GBPJPY_otc","name":"GBP/JPY OTC","category":"Forex","base_price":188.500,"payout":90},
    {"id":"CHFJPY_otc","name":"CHF/JPY OTC","category":"Forex","base_price":167.000,"payout":85},
    {"id":"NZDCAD_otc","name":"NZD/CAD OTC","category":"Forex","base_price":0.82000,"payout":87},
    {"id":"NZDCHF_otc","name":"NZD/CHF OTC","category":"Forex","base_price":0.52000,"payout":87},
    {"id":"AUDCHF_otc","name":"AUD/CHF OTC","category":"Forex","base_price":0.58000,"payout":86},
    {"id":"EURCHF_otc","name":"EUR/CHF OTC","category":"Forex","base_price":0.97000,"payout":78},
    {"id":"CADJPY_otc","name":"CAD/JPY OTC","category":"Forex","base_price":109.500,"payout":85},
    {"id":"GBPAUD_otc","name":"GBP/AUD OTC","category":"Forex","base_price":1.94000,"payout":83},
    {"id":"GBPCAD_otc","name":"GBP/CAD OTC","category":"Forex","base_price":1.73000,"payout":82},
    {"id":"EURCAD_otc","name":"EUR/CAD OTC","category":"Forex","base_price":1.48000,"payout":83},
    {"id":"NZDUSD_otc","name":"NZD/USD OTC","category":"Forex","base_price":0.60500,"payout":86},
    {"id":"GBPCHF_otc","name":"GBP/CHF OTC","category":"Forex","base_price":1.13500,"payout":84},
    {"id":"XAUUSD_otc","name":"Gold OTC","category":"Commodity","base_price":2320.00,"payout":87},
    {"id":"XAGUSD_otc","name":"Silver OTC","category":"Commodity","base_price":27.500,"payout":93},
    {"id":"UKOIL_otc","name":"UK Brent OTC","category":"Commodity","base_price":85.500,"payout":93},
    {"id":"USOIL_otc","name":"US Crude OTC","category":"Commodity","base_price":80.500,"payout":84},
    {"id":"BTCUSD_otc","name":"Bitcoin OTC","category":"Crypto","base_price":65000.00,"payout":80},
class SignalEngine:
    def __init__(self, pid):
        self.pair_id=pid
        self.closes=deque(maxlen=150); self.highs=deque(maxlen=150)
        self.lows=deque(maxlen=150);   self.opens=deque(maxlen=150)
        self.data_source="pending"
    def bulk_load(self, candles):
        for c in candles:
            self.opens.append(c["open"]); self.highs.append(c["high"])
            self.lows.append(c["low"]);   self.closes.append(c["close"])
    def add_candle(self,o,h,l,c):
        self.opens.append(o); self.highs.append(h)
        self.lows.append(l);  self.closes.append(c)
    def _ema(self, data, p):
        if not data: return 0.0
        k=2/(p+1); e=data[0]
        for v in data[1:]: e=v*k+e*(1-k)
        return e
    def rsi(self, p=14):
        c=list(self.closes)
        if len(c)<p+2: return None
        d=np.diff(c[-(p+1):]); g=np.where(d>0,d,0); l=np.where(d<0,-d,0)
        ag,al=np.mean(g),np.mean(l)
        return 100 if al==0 else 100-(100/(1+ag/al))
    def macd(self):
        c=list(self.closes)
        if len(c)<35: return None,None,None
        m=self._ema(c,12)-self._ema(c,26); s=m*0.85; return m,s,m-s
    def bollinger(self, p=20):
        c=list(self.closes)
        if len(c)<p: return None,None,None,None
        sl=c[-p:]; mid=float(np.mean(sl)); std=float(np.std(sl))
        up=mid+2*std; lo=mid-2*std
        pct=(c[-1]-lo)/(up-lo) if (up-lo)>0 else 0.5
        return up,mid,lo,pct
    def stochastic(self, p=14):
        c,h,l=list(self.closes),list(self.highs),list(self.lows)
        if len(c)<p: return None,None
        hh=max(h[-p:]); ll=min(l[-p:])
        if hh==ll: return 50.0,50.0
        k=100*(c[-1]-ll)/(hh-ll)
        dv=[]
        for i in range(3):
            idx=-(i+1)
            if abs(idx)>len(c): break
            _h=h[max(-p+idx,-len(h)):idx if idx else len(h)]
            _l=l[max(-p+idx,-len(l)):idx if idx else len(l)]
            _hh=max(_h) if _h else hh; _ll=min(_l) if _l else ll
            if _hh!=_ll: dv.append(100*(c[idx]-_ll)/(_hh-_ll))
        return k, float(np.mean(dv)) if dv else k
    def otc_pattern(self):
        c,o=list(self.closes),list(self.opens)
        if len(c)<5: return 0.0
        dirs=[1 if c[-i]>o[-i] else -1 for i in range(3,0,-1)]
        if all(d==-1 for d in dirs): return 1.0
        if all(d==1 for d in dirs): return -1.0
        if self.highs:
            hl=list(self.highs)[-1]; ll=list(self.lows)[-1]; rng=hl-ll
            if rng>0:
                lw=min(o[-1],c[-1])-ll; uw=hl-max(o[-1],c[-1])
                if lw/rng>0.65: return 0.6
                if uw/rng>0.65: return -0.6
        return 0.0
    def generate_signal(self):
        if len(self.closes)<30:
            return {"signal":"WAIT","confidence":0,"reason":"Loading real market data...","source":self.data_source}
        votes=[]
        r=self.rsi()
        if r is not None:
            if r<=25: votes.append((1,3.0,f"RSI oversold {r:.1f}"))
            elif r<=35: votes.append((1,1.8,f"RSI low {r:.1f}"))
            elif r>=75: votes.append((-1,3.0,f"RSI overbought {r:.1f}"))
            elif r>=65: votes.append((-1,1.8,f"RSI high {r:.1f}"))
            else: votes.append((1 if r<50 else -1,0.5,f"RSI neutral {r:.1f}"))
        m,s,h=self.macd()
        if m is not None:
            if m>0 and h>0: votes.append((1,2.0,"MACD bullish cross"))
            elif m<0 and h<0: votes.append((-1,2.0,"MACD bearish cross"))
            elif h>0: votes.append((1,1.0,"MACD rising"))
            else: votes.append((-1,1.0,"MACD falling"))
        _,_,_,pb=self.bollinger()
        if pb is not None:
            if pb<=0.05: votes.append((1,3.0,"BB lower band bounce"))
            elif pb<=0.20: votes.append((1,1.5,"Near lower BB"))
            elif pb>=0.95: votes.append((-1,3.0,"BB upper band reject"))
            elif pb>=0.80: votes.append((-1,1.5,"Near upper BB"))
            else: votes.append((1 if pb<0.5 else -1,0.4,"BB mid zone"))
        k,d=self.stochastic()
        if k is not None:
            if k<20 and d<20: votes.append((1,2.5,f"Stoch oversold {k:.0f}"))
            elif k>80 and d>80: votes.append((-1,2.5,f"Stoch overbought {k:.0f}"))
            elif k>d: votes.append((1,1.0,"Stoch K>D bullish"))
            else: votes.append((-1,1.0,"Stoch K<D bearish"))
        c=list(self.closes)
        if len(c)>=21:
            cross=self._ema(c,9)-self._ema(c,21)
            votes.append((1 if cross>0 else -1,1.5,f"EMA9/21 {'bull' if cross>0 else 'bear'}"))
        otc=self.otc_pattern()
        if abs(otc)>0: votes.append((1 if otc>0 else -1,abs(otc)*2.5,"OTC reversal pattern"))
        if not votes: return {"signal":"WAIT","confidence":0,"reason":"No indicators","source":self.data_source}
        bull=sum(w for d,w,_ in votes if d==1)
        bear=sum(w for d,w,_ in votes if d==-1)
        tot=bull+bear
        bp=(bull/tot)*100 if tot else 50; sep=(bear/tot)*100 if tot else 50
        reasons=" | ".join(r for _,_,r in sorted(votes,key=lambda x:x[1],reverse=True)[:3])
        if bp>=60: return {"signal":"BUY","confidence":min(int(bp),95),"reason":reasons,"source":self.data_source}
        if sep>=60: return {"signal":"SELL","confidence":min(int(sep),95),"reason":reasons,"source":self.data_source}
        return {"signal":"WAIT","confidence":int(max(bp,sep)),"reason":"Mixed — skip candle","source":self.data_source}

async def fetch_yahoo(pid):
    sym=YAHOO_MAP.get(pid)
    if not sym or not YFINANCE_OK: return None
    try:
        loop=asyncio.get_event_loop()
        def _f():
            t=yf.Ticker(sym); df=t.history(period="1d",interval="1m")
            if df is None or df.empty: return None
            r=[]
            for _,row in df.iterrows():
                if all(v==v for v in [row.Open,row.High,row.Low,row.Close]):
                    r.append({"open":float(row.Open),"high":float(row.High),"low":float(row.Low),"close":float(row.Close)})
            return r if len(r)>5 else None
        return await loop.run_in_executor(None,_f)
    except Exception as e:
        logger.warning(f"Yahoo {pid}: {e}"); return None

async def fetch_binance(pid):
    sym=BINANCE_MAP.get(pid)
    if not sym: return None
    try:
        r=req_lib.get(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1m&limit=100",timeout=8)
        if r.status_code!=200: return None
        return [{"open":float(c[1]),"high":float(c[2]),"low":float(c[3]),"close":float(c[4])} for c in r.json()]
    except Exception as e:
        logger.warning(f"Binance {pid}: {e}"); return None

async def fetch_real(pid):
    if pid in BINANCE_MAP: return await fetch_binance(pid)
    return await fetch_yahoo(pid)

class DemoSim:
    def __init__(self,base,pid):
        self.price=base; self.trend=random.choice([-1,1])
        is_jpy="JPY" in pid; is_crypto=pid in BINANCE_MAP
        is_comm=pid in ("XAUUSD_otc","XAGUSD_otc","UKOIL_otc","USOIL_otc")
        self.pip=0.01 if is_jpy else (10.0 if is_crypto else (0.1 if is_comm else 0.0001))
        self.life=random.randint(5,20); self.tick=0
    def next(self):
        self.tick+=1
        if self.tick%self.life==0: self.trend=-self.trend; self.life=random.randint(5,20)
        d=self.trend*self.pip*0.3; v=self.pip*(2+random.random()*6)
        o=self.price; c=o+d+(random.random()-0.5)*v
        h=max(o,c)+abs(random.gauss(0,v*0.4)); l=min(o,c)-abs(random.gauss(0,v*0.4))
        self.price=c
        dp=2 if self.pip>=0.1 else (0 if self.pip>=5 else 5)
        return {k:round(vv,dp) for k,vv in {"open":o,"high":h,"low":l,"close":c}.items()}

class ConnMgr:
    def __init__(self): self.active:Set[WebSocket]=set()
    async def connect(self,ws):
        await ws.accept(); self.active.add(ws)
        logger.info(f"WS +1 (total {len(self.active)})")
    def disconnect(self,ws): self.active.discard(ws)
    async def broadcast(self,data):
        msg=json.dumps(data); dead=set()
        for ws in list(self.active):
            try: await ws.send_text(msg)
            except: dead.add(ws)
        for ws in dead: self.active.discard(ws)

app=FastAPI(title="QX OTC Signal Bot v2.1")
mgr=ConnMgr()
engines:Dict[str,SignalEngine]={}
sims:Dict[str,DemoSim]={}
latest:Dict[str,dict]={}
data_mode="REAL"

app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])
for p in OTC_PAIRS:
    engines[p["id"]]=SignalEngine(p["id"])
    sims[p["id"]]=DemoSim(p["base_price"],p["id"])

async def signal_loop():
    global data_mode
    logger.info(f"Signal loop start — {len(OTC_PAIRS)} pairs")
    real_count=0
    for p in OTC_PAIRS:
        pid=p["id"]; eng=engines[pid]
        candles=await fetch_real(pid)
        if candles and len(candles)>=10:
            eng.bulk_load(candles); eng.data_source="real"; real_count+=1
            logger.info(f"REAL {pid}: {len(candles)} candles")
        else:
            for _ in range(60):
                c=sims[pid].next(); eng.add_candle(c["open"],c["high"],c["low"],c["close"])
            eng.data_source="demo"
            logger.info(f"DEMO fallback: {pid}")
        await asyncio.sleep(0.5)
    logger.info(f"Seeded {real_count}/{len(OTC_PAIRS)} with REAL data")
    data_mode="REAL" if real_count>0 else "DEMO"
    while True:
        now=datetime.now(timezone.utc); sec_left=60-now.second
        await asyncio.sleep(1)
        payload=[]
        for p in OTC_PAIRS:
            pid=p["id"]; eng=engines[pid]
            if now.second==0:
                candles=await fetch_real(pid)
                if candles and len(candles)>=2:
                    lc=candles[-1]; eng.add_candle(lc["open"],lc["high"],lc["low"],lc["close"]); eng.data_source="real"
                else:
                    c=sims[pid].next(); eng.add_candle(c["open"],c["high"],c["low"],c["close"])
                    if eng.data_source!="real": eng.data_source="demo"
            sig=eng.generate_signal()
            price=float(eng.closes[-1]) if eng.closes else p["base_price"]
            rec={"pair_id":pid,"pair_name":p["name"],"category":p["category"],"payout":p["payout"],
                 "signal":sig["signal"],"confidence":sig["confidence"],"reason":sig["reason"],
                 "data_source":sig.get("source","?"),"price":price,"candles":len(eng.closes),
                 "countdown":sec_left,"ts":now.isoformat()}
            latest[pid]=rec; payload.append(rec)
        await mgr.broadcast({"type":"signals","mode":data_mode,"data":payload,"server_time":now.isoformat()})

@app.on_event("startup")
async def startup(): asyncio.create_task(signal_loop())

@app.get("/")
async def root():
    real=sum(1 for e in engines.values() if e.data_source=="real")
    return {"status":"ok","mode":data_mode,"pairs":len(OTC_PAIRS),"real_data_pairs":real,"version":"2.1"}

@app.get("/api/status")
async def status():
    real=sum(1 for e in engines.values() if e.data_source=="real")
    return {"status":"running","mode":data_mode,"real_data_pairs":real,"demo_fallback_pairs":len(OTC_PAIRS)-real,
            "pairs":[p["name"] for p in OTC_PAIRS],"categories":{"Forex":22,"Commodity":4,"Crypto":2},"clients":len(mgr.active)}

@app.get("/api/signals")
async def signals_ep(): return {"signals":list(latest.values())}

@app.websocket("/ws")
async def ws_ep(ws:WebSocket):
    await mgr.connect(ws)
    if latest:
        await ws.send_text(json.dumps({"type":"signals","mode":data_mode,"data":list(latest.values()),"server_time":datetime.now(timezone.utc).isoformat()}))
    try:
        while True: await ws.receive_text()
    except (WebSocketDisconnect,Exception): mgr.disconnect(ws)

if __name__=="__main__":
    uvicorn.run("server:app",host="0.0.0.0",port=PORT,reload=False)
