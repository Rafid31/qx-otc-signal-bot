# QX OTC Signal Bot

Real-time BUY/SELL signals for OTC pairs on QX Broker.  
Backend: Python/FastAPI on **Railway** | Frontend: Static files on **Cloudflare Pages**

---

## Architecture

```
Frontend (Cloudflare Pages — FREE)
         ↓ WebSocket
Python Backend (Railway — FREE tier)
         ↓ pyquotex
QX Broker WebSocket
```

---

## Files

| File | Purpose |
|---|---|
| `server.py` | Python FastAPI backend — signal generation + WebSocket |
| `requirements.txt` | Python dependencies |
| `Procfile` | Railway run command |
| `runtime.txt` | Python version |
| `index.html` | Frontend dashboard |
| `style.css` | Dark trading theme |
| `engine.js` | OTC signal engine (JS mirror of Python) |
| `app.js` | WebSocket client + UI controller |

---

## OTC Pairs Tracked

- EUR/USD OTC
- GBP/USD OTC
- USD/JPY OTC
- AUD/USD OTC
- EUR/JPY OTC
- USD/CAD OTC

---

## Signal Algorithm

Weighted voting system (OTC-optimised):

| Indicator | Weight | OTC Note |
|---|---|---|
| RSI (14) | 3.0 | Mean reversion primary |
| Bollinger Bands (20,2) | 3.0 | OTC bounces off bands |
| Stochastic (14,3) | 2.5 | Momentum confirmation |
| OTC Reversal Pattern | 2.5 | 3-candle streak + wick |
| MACD (12,26,9) | 2.0 | Trend filter |
| EMA Cross (9,21) | 1.5 | Direction bias |

Signal fires when **≥60% weighted consensus** is reached.

---

## Deploy Backend to Railway

1. Go to [railway.app](https://railway.app) → **New Project**
2. **Deploy from GitHub repo** → select `Rafid31/qx-otc-signal-bot`
3. Railway auto-detects `Procfile` and runs the server
4. Go to **Variables** tab → add:
   ```
   QX_EMAIL    = your_qx_email@example.com
   QX_PASSWORD = your_qx_password
   ```
5. Go to **Settings → Networking** → click **Generate Domain**
6. Copy your Railway URL (e.g. `https://qx-bot.up.railway.app`)

> Without credentials, the bot runs in **DEMO mode** with simulated price data.

---

## Deploy Frontend to Cloudflare Pages

1. Go to [pages.cloudflare.com](https://pages.cloudflare.com)
2. **Create a project** → Connect to GitHub → select this repo
3. Build settings:
   - Build command: *(leave empty)*
   - Output directory: `/` (root)
4. Click **Save and Deploy**
5. Your site is live at `https://qx-otc-signal-bot.pages.dev`

---

## Using the Dashboard

1. Open your Cloudflare Pages URL
2. Enter your Railway backend URL in the setup modal
3. Click **Connect & Start**
4. Signals appear in real-time — no reload needed
5. No backend yet? Click **Run in Demo Mode** to preview

---

## Environment Variables

```env
QX_EMAIL=your_qx_broker_email@example.com
QX_PASSWORD=your_qx_broker_password
PORT=8000
```
