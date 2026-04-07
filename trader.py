"""
Pocket Option Algo Trader v1.0.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Connects to Pocket Option via WebSocket using SSID
• Builds 30-second candles and runs signal analysis
• 6-indicator weighted signal: RSI + BB + Stoch + MACD + EMA + Reversal
• Web dashboard: START / STOP + live trade log + stats
• 100% DEMO mode — set TRADE_MODE=REAL to enable real money
• SSID from environment variable: POCKET_SSID
"""

import asyncio
import json
import logging
import os
import time
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

# ══════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("po_trader")

# ══════════════════════════════════════════════════════════
# CONFIG  — change via Railway environment variables
# ══════════════════════════════════════════════════════════
VERSION         = "1.0.0"
POCKET_SSID     = os.environ.get("POCKET_SSID", "")
TRADE_AMOUNT    = float(os.environ.get("TRADE_AMOUNT", "1.0"))   # $ per trade
TRADE_DURATION  = int(os.environ.get("TRADE_DURATION", "30"))    # seconds
CANDLE_PERIOD   = 30          # seconds per candle
MIN_CANDLES     = 50          # min candles before signaling
SIGNAL_THRESH   = 60.0        # % confidence needed to place trade
LOOP_SECS       = 30          # check every N seconds
MAX_TRADES_HOUR = 20          # safety cap
TRADE_MODE      = os.environ.get("TRADE_MODE", "DEMO")           # DEMO or REAL

# Pairs to trade — these are exact Pocket Option OTC asset names
TRADING_PAIRS = [
    "EURUSD_otc",
    "GBPUSD_otc",
    "USDJPY_otc",
    "AUDUSD_otc",
    "GBPJPY_otc",
    "EURJPY_otc",
    "BTCUSD_otc",
    "ETHUSD_otc",
]

# ══════════════════════════════════════════════════════════
# GLOBAL STATE
# ══════════════════════════════════════════════════════════
state = {
    "running":      False,
    "connected":    False,
    "balance":      0.0,
    "mode":         TRADE_MODE,
    "total":        0,
    "won":          0,
    "lost":         0,
    "pending":      0,
    "trades_hour":  0,
    "hour_ts":      time.time(),
    "last_signal":  "",
    "error":        "",
}
trade_log: deque = deque(maxlen=200)   # last 200 trades
ws_clients: List[WebSocket] = []
api_instance = None
api_lock = threading.Lock()
stop_event = threading.Event()

# ══════════════════════════════════════════════════════════
# TECHNICAL INDICATORS  (same engine as signal bot)
# ══════════════════════════════════════════════════════════
def _rsi(closes: pd.Series, period: int = 7) -> pd.Series:
    delta    = closes.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def _bollinger(closes: pd.Series, period: int = 14, n_std: float = 2.0):
    sma = closes.rolling(period).mean()
    std = closes.rolling(period).std(ddof=0)
    return sma + n_std*std, sma, sma - n_std*std

def _stochastic(df: pd.DataFrame, k_period: int = 5, d_period: int = 3):
    lo    = df["Low"].rolling(k_period).min()
    hi    = df["High"].rolling(k_period).max()
    denom = (hi - lo).replace(0, np.nan)
    k     = 100 * (df["Close"] - lo) / denom
    d     = k.rolling(d_period).mean()
    return k, d

def _macd(closes: pd.Series, fast: int = 5, slow: int = 13, signal: int = 4):
    ef = closes.ewm(span=fast,   adjust=False).mean()
    es = closes.ewm(span=slow,   adjust=False).mean()
    ml = ef - es
    sl = ml.ewm(span=signal, adjust=False).mean()
    return ml, sl, ml - sl

def _ema(closes: pd.Series, period: int) -> pd.Series:
    return closes.ewm(span=period, adjust=False).mean()

def _reversal(df: pd.DataFrame) -> Optional[str]:
    if len(df) < 4:
        return None
    tail = df.tail(4)
    dirs = ["bull" if r["Close"] >= r["Open"] else "bear" for _, r in tail.iterrows()]
    if dirs[-3:] == ["bear", "bear", "bear"]: return "BUY"
    if dirs[-3:] == ["bull", "bull", "bull"]: return "SELL"
    return None

def generate_signal(df: pd.DataFrame) -> dict:
    """6-indicator weighted vote. Returns signal dict with direction + confidence."""
    empty = {"signal": "WAIT", "confidence": 0, "reason": "Not enough data"}
    if df is None or len(df) < MIN_CANDLES:
        return empty

    closes    = df["Close"]
    last_close = float(closes.iloc[-1])
    TOTAL_W   = 14.5

    # 1. RSI-7  (weight 3.0)
    rsi_s = _rsi(closes, 7)
    rsi   = float(rsi_s.iloc[-1])
    rsi_v = None
    if not np.isnan(rsi):
        if rsi <= 35:   rsi_v = "BUY"
        elif rsi >= 65: rsi_v = "SELL"

    # 2. Bollinger (14,2)  (weight 3.0)
    bb_up, _, bb_lo = _bollinger(closes, 14)
    bu = float(bb_up.iloc[-1]);  bl = float(bb_lo.iloc[-1])
    bb_v = None
    if not (np.isnan(bu) or np.isnan(bl)):
        rng = bu - bl
        if rng > 0:
            pos = (last_close - bl) / rng
            if pos <= 0.20: bb_v = "BUY"
            elif pos >= 0.80: bb_v = "SELL"

    # 3. Stoch (5,3)  (weight 2.5)
    k_s, _ = _stochastic(df, 5, 3)
    k = float(k_s.iloc[-1])
    st_v = None
    if not np.isnan(k):
        if k < 20: st_v = "BUY"
        elif k > 80: st_v = "SELL"

    # 4. OTC Reversal  (weight 2.5)
    rev_v = _reversal(df)

    # 5. MACD (5,13,4)  (weight 2.0)
    _, _, hist = _macd(closes, 5, 13, 4)
    h1 = float(hist.iloc[-1])
    h2 = float(hist.iloc[-2]) if len(hist) > 1 else 0.0
    mac_v = None
    if not (np.isnan(h1) or np.isnan(h2)):
        if h1 > 0 and h2 <= 0:   mac_v = "BUY"
        elif h1 < 0 and h2 >= 0: mac_v = "SELL"
        elif h1 > h2 and h1 > 0: mac_v = "BUY"
        elif h1 < h2 and h1 < 0: mac_v = "SELL"

    # 6. EMA 3/8  (weight 1.5)
    e3 = float(_ema(closes, 3).iloc[-1])
    e8 = float(_ema(closes, 8).iloc[-1])
    ema_v = None
    if not (np.isnan(e3) or np.isnan(e8)):
        if e3 > e8:   ema_v = "BUY"
        elif e3 < e8: ema_v = "SELL"

    votes  = [(rsi_v,3.0),(bb_v,3.0),(st_v,2.5),(rev_v,2.5),(mac_v,2.0),(ema_v,1.5)]
    bull_w = sum(w for v,w in votes if v == "BUY")
    bear_w = sum(w for v,w in votes if v == "SELL")
    bull_p = round(bull_w / TOTAL_W * 100, 1)
    bear_p = round(bear_w / TOTAL_W * 100, 1)

    labels = []
    if rsi_v:  labels.append(f"RSI {rsi:.0f}")
    if bb_v:   labels.append(f"BB zone")
    if st_v:   labels.append(f"Stoch {k:.0f}")
    if mac_v:  labels.append(f"MACD")
    if ema_v:  labels.append(f"EMA")
    reason = " | ".join(labels[:3]) or "Mixed"

    if bull_p >= SIGNAL_THRESH:
        return {"signal": "BUY",  "confidence": int(bull_p), "reason": reason}
    if bear_p >= SIGNAL_THRESH:
        return {"signal": "SELL", "confidence": int(bear_p), "reason": reason}
    return {"signal": "WAIT", "confidence": int(max(bull_p, bear_p)), "reason": "Mixed signals"}


# ══════════════════════════════════════════════════════════
# POCKET OPTION API WRAPPER
# ══════════════════════════════════════════════════════════
def po_connect(ssid: str) -> bool:
    """Connect to Pocket Option using SSID. Returns True on success."""
    global api_instance
    try:
        from pocketoptionapi.stable_api import PocketOption
        api = PocketOption(ssid)
        check = api.connect()
        if check:
            time.sleep(3)  # wait for WS handshake
            if TRADE_MODE == "DEMO":
                api.change_balance("PRACTICE")
            else:
                api.change_balance("REAL")
            with api_lock:
                api_instance = api
            log.info(f"Connected to Pocket Option ({TRADE_MODE} mode)")
            return True
        else:
            log.error("PocketOption connect() returned False")
            return False
    except ImportError:
        log.error("pocketoptionapi not installed — run: pip install pocketoptionapi")
        state["error"] = "Library not installed. See logs."
        return False
    except Exception as e:
        log.error(f"Connection failed: {e}")
        state["error"] = str(e)
        return False


def po_get_balance() -> float:
    """Get current account balance."""
    try:
        with api_lock:
            api = api_instance
        if api:
            bal = api.get_balance()
            return float(bal) if bal else 0.0
    except Exception as e:
        log.warning(f"get_balance failed: {e}")
    return 0.0


def po_get_candles(asset: str, period: int = 30, count: int = 100) -> Optional[pd.DataFrame]:
    """
    Fetch candle data from Pocket Option.
    Returns DataFrame with Open/High/Low/Close columns.
    """
    try:
        with api_lock:
            api = api_instance
        if not api:
            return None

        end_time = time.time()
        # pocketoptionapi v1 style
        raw = api.get_candles(asset, period, count, end_time)
        if not raw:
            return None

        rows = []
        for c in raw:
            # candles come as dicts with keys: id/from/to/open/close/min/max
            try:
                rows.append({
                    "Open":  float(c.get("open",  c.get("o", 0))),
                    "High":  float(c.get("max",   c.get("h", 0))),
                    "Low":   float(c.get("min",   c.get("l", 0))),
                    "Close": float(c.get("close", c.get("c", 0))),
                })
            except Exception:
                continue

        if len(rows) < MIN_CANDLES:
            return None

        df = pd.DataFrame(rows)
        df = df[(df["Close"] > 0)]
        return df

    except Exception as e:
        log.warning(f"get_candles({asset}) failed: {e}")
        return None


def po_place_trade(asset: str, direction: str, amount: float, duration: int):
    """
    Place a trade on Pocket Option.
    direction: 'call' (BUY/UP) or 'put' (SELL/DOWN)
    Returns: (trade_id, success_bool)
    """
    try:
        with api_lock:
            api = api_instance
        if not api:
            return None, False

        status, trade_id = api.buy(
            amount    = amount,
            active    = asset,
            direction = direction,   # "call" or "put"
            duration  = duration,    # seconds
        )
        return trade_id, bool(status)
    except Exception as e:
        log.error(f"buy({asset}, {direction}) failed: {e}")
        return None, False


def po_check_result(trade_id) -> Optional[str]:
    """
    Check if a trade won or lost.
    Returns: "win", "loss", or None (still pending)
    """
    try:
        with api_lock:
            api = api_instance
        if not api or trade_id is None:
            return None
        result = api.check_win(trade_id)
        if result in ("win", "loss", "loose"):
            return "win" if result == "win" else "loss"
        return None
    except Exception as e:
        log.warning(f"check_win({trade_id}) failed: {e}")
        return None


# ══════════════════════════════════════════════════════════
# TRADING LOOP  — runs in background thread
# ══════════════════════════════════════════════════════════
def _log_event(msg: str, level: str = "info"):
    ts  = datetime.now(timezone.utc).strftime("%H:%M:%S")
    entry = {"ts": ts, "msg": msg, "level": level}
    trade_log.appendleft(entry)
    if level == "error":
        log.error(msg)
    else:
        log.info(msg)


def _reset_hour_counter():
    now = time.time()
    if now - state["hour_ts"] >= 3600:
        state["trades_hour"] = 0
        state["hour_ts"]     = now


def trading_loop():
    """
    Main trading loop — runs continuously in a background thread.
    Every LOOP_SECS seconds, checks each pair for signals and places trades.
    Respects the start/stop flag and hourly trade limit.
    """
    log.info("Trading loop started")
    pending_trades: Dict[str, dict] = {}   # trade_id → {asset, direction, ts, amount}

    while not stop_event.is_set():
        try:
            _reset_hour_counter()

            # ── Check pending trade results ─────────────────
            resolved = []
            for tid, info in list(pending_trades.items()):
                result = po_check_result(tid)
                if result:
                    resolved.append(tid)
                    pnl = info["amount"] if result == "win" else -info["amount"]
                    state["pending"] = max(0, state["pending"] - 1)
                    if result == "win":
                        state["won"] += 1
                        icon = "✅"
                    else:
                        state["lost"] += 1
                        icon = "❌"
                    state["total"] += 1
                    wr = round(state["won"] / state["total"] * 100, 1) if state["total"] else 0
                    _log_event(
                        f"{icon} {info['asset']} {info['direction'].upper()} "
                        f"→ {result.upper()}  (WR: {wr}%)",
                        "win" if result == "win" else "loss"
                    )
                # Expire pending trades older than 5 minutes (safety)
                elif time.time() - info["ts"] > 300:
                    resolved.append(tid)
                    state["pending"] = max(0, state["pending"] - 1)

            for tid in resolved:
                pending_trades.pop(tid, None)

            # Update balance
            bal = po_get_balance()
            if bal > 0:
                state["balance"] = round(bal, 2)

            # ── Only trade when running ─────────────────────
            if not state["running"]:
                time.sleep(LOOP_SECS)
                continue

            if state["trades_hour"] >= MAX_TRADES_HOUR:
                _log_event(f"⚠ Hourly limit ({MAX_TRADES_HOUR}) reached — waiting", "warn")
                time.sleep(LOOP_SECS)
                continue

            # ── Scan each pair ──────────────────────────────
            for asset in TRADING_PAIRS:
                if stop_event.is_set() or not state["running"]:
                    break

                df = po_get_candles(asset, CANDLE_PERIOD, 100)
                sig = generate_signal(df)

                if sig["signal"] == "WAIT":
                    time.sleep(0.5)
                    continue

                direction  = "call" if sig["signal"] == "BUY" else "put"
                conf       = sig["confidence"]
                reason     = sig["reason"]
                pair_clean = asset.replace("_otc", "").replace("USD", "/USD").replace("EUR", "EUR/")

                state["last_signal"] = (
                    f"{asset} {sig['signal']} {conf}% — {reason}"
                )
                _log_event(
                    f"📊 Signal: {asset}  {sig['signal']}  {conf}%  | {reason}",
                    "signal"
                )

                # Place trade
                trade_id, ok = po_place_trade(asset, direction, TRADE_AMOUNT, TRADE_DURATION)

                if ok and trade_id:
                    state["trades_hour"] += 1
                    state["pending"]     += 1
                    pending_trades[trade_id] = {
                        "asset":     asset,
                        "direction": direction,
                        "amount":    TRADE_AMOUNT,
                        "ts":        time.time(),
                        "conf":      conf,
                    }
                    arrow = "▲ CALL" if direction == "call" else "▼ PUT"
                    _log_event(
                        f"🚀 Trade placed: {asset}  {arrow}  ${TRADE_AMOUNT}  "
                        f"({TRADE_DURATION}s)  ID:{trade_id}",
                        "trade"
                    )
                else:
                    _log_event(f"⚠ Trade failed for {asset} — API error", "error")

                time.sleep(1)  # small gap between pair checks

        except Exception as e:
            log.error(f"Trading loop error: {e}", exc_info=True)
            state["error"] = str(e)

        time.sleep(LOOP_SECS)

    log.info("Trading loop stopped")


# ══════════════════════════════════════════════════════════
# FASTAPI APP
# ══════════════════════════════════════════════════════════
app = FastAPI(title="PO Algo Trader", version=VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    """On startup: connect to Pocket Option + launch trading loop."""
    log.info(f"PO Algo Trader v{VERSION} starting …")

    if not POCKET_SSID:
        log.warning("POCKET_SSID not set — running in dashboard-only mode")
        state["error"] = "POCKET_SSID not configured. Set it in Railway environment variables."
        return

    # Connect in background thread (blocking call)
    def _connect():
        ok = po_connect(POCKET_SSID)
        state["connected"] = ok
        if ok:
            bal = po_get_balance()
            state["balance"] = round(bal, 2)
            _log_event(f"✅ Connected to Pocket Option | Balance: ${state['balance']}", "info")
        else:
            _log_event("❌ Failed to connect to Pocket Option — check SSID", "error")

    t = threading.Thread(target=_connect, daemon=True)
    t.start()
    t.join(timeout=20)

    # Start trading loop thread
    loop_thread = threading.Thread(target=trading_loop, daemon=True, name="TradingLoop")
    loop_thread.start()
    log.info("Trading loop thread started")


@app.on_event("shutdown")
async def shutdown():
    stop_event.set()


# ── REST Endpoints ──────────────────────────────────────

@app.post("/api/start")
async def start_trading():
    if not state["connected"] and POCKET_SSID:
        return {"ok": False, "msg": "Not connected to Pocket Option"}
    state["running"] = True
    state["error"]   = ""
    _log_event("▶ Trading STARTED", "info")
    await _broadcast()
    return {"ok": True, "msg": "Trading started"}


@app.post("/api/stop")
async def stop_trading():
    state["running"] = False
    _log_event("⏹ Trading STOPPED", "info")
    await _broadcast()
    return {"ok": True, "msg": "Trading stopped"}


@app.get("/api/status")
async def get_status():
    total = state["total"]
    wr    = round(state["won"] / total * 100, 1) if total > 0 else 0.0
    return {
        "version":     VERSION,
        "running":     state["running"],
        "connected":   state["connected"],
        "mode":        TRADE_MODE,
        "balance":     state["balance"],
        "total":       total,
        "won":         state["won"],
        "lost":        state["lost"],
        "pending":     state["pending"],
        "win_rate":    wr,
        "trades_hour": state["trades_hour"],
        "max_hour":    MAX_TRADES_HOUR,
        "error":       state["error"],
        "last_signal": state["last_signal"],
        "log":         list(trade_log)[:30],
        "pairs":       TRADING_PAIRS,
        "amount":      TRADE_AMOUNT,
        "duration":    TRADE_DURATION,
    }


@app.get("/api/log")
async def get_log():
    return {"log": list(trade_log)}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)


# ── WebSocket for live updates ───────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    try:
        # Send current state immediately on connect
        await ws.send_text(json.dumps(await _build_payload()))
        while True:
            await asyncio.sleep(2)
            await ws.send_text(json.dumps(await _build_payload()))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if ws in ws_clients:
            ws_clients.remove(ws)


async def _build_payload() -> dict:
    total = state["total"]
    wr    = round(state["won"] / total * 100, 1) if total > 0 else 0.0
    return {
        "type":        "update",
        "running":     state["running"],
        "connected":   state["connected"],
        "mode":        TRADE_MODE,
        "balance":     state["balance"],
        "total":       total,
        "won":         state["won"],
        "lost":        state["lost"],
        "pending":     state["pending"],
        "win_rate":    wr,
        "trades_hour": state["trades_hour"],
        "error":       state["error"],
        "last_signal": state["last_signal"],
        "log":         list(trade_log)[:50],
    }


async def _broadcast():
    payload = json.dumps(await _build_payload())
    dead = []
    for ws in ws_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.remove(ws)


# ══════════════════════════════════════════════════════════
# DASHBOARD HTML
# ══════════════════════════════════════════════════════════
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PO Algo Trader</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',sans-serif;min-height:100vh}
  .header{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;display:flex;align-items:center;justify-content:space-between}
  .header h1{font-size:20px;font-weight:700;color:#58a6ff}
  .header h1 span{color:#3fb950;font-size:13px;margin-left:8px;background:#1a4731;padding:2px 8px;border-radius:20px}
  .badge{padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}
  .badge.demo{background:#2d1b69;color:#a78bfa}
  .badge.real{background:#4a1010;color:#f87171}
  .badge.conn{background:#0d2a1f;color:#3fb950}
  .badge.disc{background:#1f1f1f;color:#6e7681}
  .main{max-width:1100px;margin:0 auto;padding:24px}

  /* Stats row */
  .stats{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:24px}
  .stat{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;text-align:center}
  .stat .val{font-size:28px;font-weight:700;margin-bottom:4px}
  .stat .lbl{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px}
  .stat.green .val{color:#3fb950}
  .stat.red .val{color:#f85149}
  .stat.blue .val{color:#58a6ff}
  .stat.purple .val{color:#a78bfa}
  .stat.gold .val{color:#e3b341}

  /* Control panel */
  .controls{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px;margin-bottom:20px}
  .controls h2{font-size:14px;color:#8b949e;margin-bottom:16px;text-transform:uppercase;letter-spacing:.5px}
  .btn-row{display:flex;gap:12px;align-items:center}
  .btn{padding:14px 36px;border:none;border-radius:8px;font-size:16px;font-weight:700;cursor:pointer;transition:all .2s;letter-spacing:.3px}
  .btn-start{background:linear-gradient(135deg,#238636,#2ea043);color:#fff}
  .btn-start:hover{background:linear-gradient(135deg,#2ea043,#3fb950);transform:translateY(-1px)}
  .btn-stop{background:linear-gradient(135deg,#b91c1c,#dc2626);color:#fff}
  .btn-stop:hover{background:linear-gradient(135deg,#dc2626,#ef4444);transform:translateY(-1px)}
  .btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
  .status-pill{padding:10px 16px;border-radius:8px;font-size:14px;font-weight:600}
  .status-pill.on{background:#0d2a1f;color:#3fb950;border:1px solid #238636}
  .status-pill.off{background:#1f2428;color:#6e7681;border:1px solid #30363d}

  /* Config info */
  .config-row{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
  .cfg{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:6px 12px;font-size:12px;color:#8b949e}
  .cfg b{color:#e6edf3}

  /* Win rate bar */
  .wr-bar{height:6px;background:#21262d;border-radius:3px;margin-top:8px;overflow:hidden}
  .wr-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,#f85149,#e3b341,#3fb950);transition:width .5s}

  /* Signal indicator */
  .signal-box{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;margin-bottom:20px}
  .signal-box h2{font-size:12px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px}
  .signal-val{font-size:14px;color:#e6edf3;font-family:monospace}
  .signal-val.buy{color:#3fb950}
  .signal-val.sell{color:#f85149}

  /* Trade log */
  .log-box{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:0;overflow:hidden}
  .log-header{padding:14px 16px;border-bottom:1px solid #30363d;display:flex;justify-content:space-between;align-items:center}
  .log-header h2{font-size:14px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px}
  .log-entries{height:420px;overflow-y:auto;padding:0}
  .log-entry{padding:10px 16px;border-bottom:1px solid #21262d;font-size:13px;display:flex;gap:10px;align-items:flex-start}
  .log-entry:hover{background:#1c2128}
  .log-ts{color:#6e7681;font-family:monospace;min-width:56px;font-size:11px;margin-top:1px}
  .log-msg{flex:1;line-height:1.4}
  .log-entry.win .log-msg{color:#3fb950}
  .log-entry.loss .log-msg{color:#f85149}
  .log-entry.trade .log-msg{color:#58a6ff}
  .log-entry.signal .log-msg{color:#e3b341}
  .log-entry.error .log-msg{color:#f85149}
  .log-entry.warn .log-msg{color:#e3b341}
  .empty-log{padding:40px;text-align:center;color:#6e7681;font-size:14px}

  /* Error banner */
  .error-banner{background:#2d0808;border:1px solid #6e1010;border-radius:8px;padding:12px 16px;color:#f85149;font-size:13px;margin-bottom:16px;display:none}
  .error-banner.show{display:block}

  /* Pairs monitoring */
  .pairs-box{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;margin-bottom:20px}
  .pairs-box h2{font-size:12px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px}
  .pairs-list{display:flex;flex-wrap:wrap;gap:8px}
  .pair-chip{background:#21262d;border-radius:6px;padding:5px 10px;font-size:12px;color:#8b949e;border:1px solid #30363d}

  /* Responsive */
  @media(max-width:700px){.stats{grid-template-columns:repeat(2,1fr)}.btn-row{flex-wrap:wrap}}
</style>
</head>
<body>

<div class="header">
  <h1>🤖 PO Algo Trader <span id="ver">v1.0</span></h1>
  <div style="display:flex;gap:8px;align-items:center">
    <span id="mode-badge" class="badge demo">DEMO</span>
    <span id="conn-badge" class="badge disc">DISCONNECTED</span>
  </div>
</div>

<div class="main">

  <!-- Error Banner -->
  <div id="error-banner" class="error-banner"></div>

  <!-- Stats -->
  <div class="stats">
    <div class="stat blue">
      <div class="val" id="s-balance">$0.00</div>
      <div class="lbl">Balance</div>
    </div>
    <div class="stat green">
      <div class="val" id="s-won">0</div>
      <div class="lbl">Won</div>
      <div class="wr-bar"><div class="wr-fill" id="wr-fill" style="width:0%"></div></div>
    </div>
    <div class="stat red">
      <div class="val" id="s-lost">0</div>
      <div class="lbl">Lost</div>
    </div>
    <div class="stat gold">
      <div class="val" id="s-wr">0%</div>
      <div class="lbl">Win Rate</div>
    </div>
    <div class="stat purple">
      <div class="val" id="s-pending">0</div>
      <div class="lbl">Pending</div>
    </div>
  </div>

  <!-- Controls -->
  <div class="controls">
    <h2>Trading Control</h2>
    <div class="btn-row">
      <button class="btn btn-start" id="btn-start" onclick="startTrading()">▶ START TRADING</button>
      <button class="btn btn-stop" id="btn-stop" onclick="stopTrading()" disabled>⏹ STOP TRADING</button>
      <div class="status-pill off" id="status-pill">STOPPED</div>
    </div>
    <div class="config-row">
      <div class="cfg">Amount: <b id="cfg-amount">$1.00</b></div>
      <div class="cfg">Duration: <b id="cfg-dur">30s</b></div>
      <div class="cfg">Threshold: <b>60%</b></div>
      <div class="cfg">Max/hour: <b id="cfg-hr">0 / 20</b></div>
    </div>
  </div>

  <!-- Last signal -->
  <div class="signal-box">
    <h2>⚡ Last Signal</h2>
    <div class="signal-val" id="last-signal">Waiting for signals…</div>
  </div>

  <!-- Pairs -->
  <div class="pairs-box">
    <h2>📊 Monitoring Pairs</h2>
    <div class="pairs-list" id="pairs-list"></div>
  </div>

  <!-- Trade Log -->
  <div class="log-box">
    <div class="log-header">
      <h2>📋 Live Trade Log</h2>
      <span id="log-count" style="font-size:12px;color:#6e7681">0 events</span>
    </div>
    <div class="log-entries" id="log-entries">
      <div class="empty-log">No activity yet — press START to begin trading</div>
    </div>
  </div>

</div>

<script>
const WS_URL = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws';
let ws = null;
let reconnectTimer = null;

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    console.log('WS connected');
    clearTimeout(reconnectTimer);
  };

  ws.onmessage = (e) => {
    try { render(JSON.parse(e.data)); }
    catch(err) { console.error(err); }
  };

  ws.onclose = () => {
    reconnectTimer = setTimeout(connect, 3000);
  };

  ws.onerror = () => ws.close();
}

function render(d) {
  // Connection badge
  document.getElementById('conn-badge').textContent   = d.connected ? 'CONNECTED' : 'DISCONNECTED';
  document.getElementById('conn-badge').className     = 'badge ' + (d.connected ? 'conn' : 'disc');
  document.getElementById('mode-badge').textContent   = d.mode || 'DEMO';
  document.getElementById('mode-badge').className     = 'badge ' + (d.mode === 'REAL' ? 'real' : 'demo');

  // Stats
  document.getElementById('s-balance').textContent  = '$' + (d.balance || 0).toFixed(2);
  document.getElementById('s-won').textContent      = d.won  || 0;
  document.getElementById('s-lost').textContent     = d.lost || 0;
  document.getElementById('s-wr').textContent       = (d.win_rate || 0) + '%';
  document.getElementById('s-pending').textContent  = d.pending || 0;
  document.getElementById('wr-fill').style.width    = Math.min(100, d.win_rate || 0) + '%';

  // Config row
  document.getElementById('cfg-hr').textContent = (d.trades_hour || 0) + ' / 20';

  // Buttons
  const running = d.running;
  document.getElementById('btn-start').disabled = running;
  document.getElementById('btn-stop').disabled  = !running;
  const pill = document.getElementById('status-pill');
  pill.textContent  = running ? '🟢 RUNNING' : 'STOPPED';
  pill.className    = 'status-pill ' + (running ? 'on' : 'off');

  // Last signal
  const ls = document.getElementById('last-signal');
  ls.textContent = d.last_signal || 'Waiting for signals…';
  ls.className   = 'signal-val' + (
    d.last_signal?.includes('BUY')  ? ' buy'  :
    d.last_signal?.includes('SELL') ? ' sell' : '');

  // Error banner
  const banner = document.getElementById('error-banner');
  if (d.error) {
    banner.textContent = '⚠ ' + d.error;
    banner.classList.add('show');
  } else {
    banner.classList.remove('show');
  }

  // Trade log
  const entries = d.log || [];
  document.getElementById('log-count').textContent = entries.length + ' events';
  if (entries.length === 0) return;

  const container = document.getElementById('log-entries');
  container.innerHTML = entries.map(e => `
    <div class="log-entry ${e.level || ''}">
      <span class="log-ts">${e.ts}</span>
      <span class="log-msg">${e.msg}</span>
    </div>
  `).join('');
}

async function startTrading() {
  document.getElementById('btn-start').disabled = true;
  const r = await fetch('/api/start', {method:'POST'});
  const j = await r.json();
  if (!j.ok) {
    alert('Error: ' + j.msg);
    document.getElementById('btn-start').disabled = false;
  }
}

async function stopTrading() {
  document.getElementById('btn-stop').disabled = true;
  await fetch('/api/stop', {method:'POST'});
}

// Init pairs display from status
fetch('/api/status').then(r=>r.json()).then(d => {
  const pl = document.getElementById('pairs-list');
  (d.pairs||[]).forEach(p => {
    const chip = document.createElement('div');
    chip.className = 'pair-chip';
    chip.textContent = p.replace('_otc','').replace(/([A-Z]{3})([A-Z]{3})/,'$1/$2') + ' OTC';
    pl.appendChild(chip);
  });
  document.getElementById('cfg-amount').textContent = '$' + (d.amount||1).toFixed(2);
  document.getElementById('cfg-dur').textContent    = (d.duration||30) + 's';
});

connect();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("trader:app", host="0.0.0.0", port=port, reload=False)
