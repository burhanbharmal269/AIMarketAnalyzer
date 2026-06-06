# Manual Trade Execution Guide

Step-by-step workflow for using the Indian Options Research Desk to find signals
and execute trades manually through your broker.

---

## Overview

This application is a **research and signal tool** — it does not place orders.
You read the signal, open your broker app, and execute manually.

```
Dashboard → Signal → Broker (manual entry) → Dashboard (journal update) → Telegram alerts
```

All risk sizing is pre-calculated. All you do is read the card and place the order.

---

## Before Market Opens — Setup (by 9:00 AM IST)

**Step 1 — Start the server**

```powershell
.\start.ps1
```

Open your browser and go to `http://localhost:8000`

**Step 2 — Set your risk parameters**

In the **Risk Controls** section:

| Field | What to set | Example |
|---|---|---|
| Account Capital | Your actual trading capital | ₹50,000 |
| Risk Per Trade | % you are willing to lose per trade — keep at 2% | 2% |
| Max Spread | Leave at default (1.5%) | 1.5% |
| Min Option Volume | Leave at default (50,000) | 50,000 |
| Event Block Window | Leave at default (60 min) | 60 min |

> **Do not change Risk % above 3%.** At 2%, a full loss costs ₹1,000 on ₹50,000 capital.
> The system sizes positions to keep every loss within this limit.

**Step 3 — Verify loss streak**

The **Consecutive Losses** field is auto-populated from your journal.
If it shows 0 and you have recent losses, update your journal outcomes first.

---

## Market Open — Running the Scan (9:20 AM IST)

**Step 4 — Click Run Scan**

Wait 15–20 seconds. A progress bar appears at the top while scanning 40 instruments in parallel.

> **Best scan times:** 9:20 AM and again around 11:00 AM if morning was quiet.
> Avoid scanning during 9:15–9:30 (opening chop) and 2:45–3:30 PM (pre-close) — the system blocks signals during these windows automatically.

**Step 5 — Check market conditions first**

Before looking at signals, read the four metric panels at the top:

| Panel | What to look for |
|---|---|
| Market Regime | Bullish = look for BUY signals, Bearish = look for SELL signals |
| Approved Signals | 0 = no trade today (preserve capital) |
| India VIX | Below 15 = calm, 15–22 = normal, above 22 = system blocks all signals |
| Capital Mode | Should say "Protected" — if it says "Restricted" check your drawdown |

**If you see a red error banner** — NSE data is unavailable. Do not trade. Wait 5 minutes and scan again.

---

## Reading a Signal Card

Approved signals appear as green-bordered (BUY CE) or red-bordered (SELL PE) cards.

```
┌─────────────────────────────────────────────────────┐
│  ▲ BUY CE          [Score Badge: 87/100]            │
│  BANKNIFTY 56000 CE  ·  Index trend continuation    │
├─────────────────────────────────────────────────────┤
│  Valid until 11:45 AM  ·  2 lot(s)  ·  RR 1:2.8    │
├─────────────────────────────────────────────────────┤
│  Entry  ₹185    Stop Loss  ₹148                     │
│  T1     ₹222    T2         ₹259    T3  ₹296         │
│  Expiry 12-Jun-2025 (6d)                            │
├─────────────────────────────────────────────────────┤
│  Delta +0.48  Theta -3.2/d  Vega +12.4  IV 24%     │
│  IV Rank [LOW - green]   15m [✓ - green]            │
└─────────────────────────────────────────────────────┘
```

### What each field means

| Field | Meaning | Action |
|---|---|---|
| **▲ BUY CE** | Bullish setup — buy a Call Option | Buy CE in broker |
| **▼ SELL PE** | Bearish setup — buy a Put Option | Buy PE in broker |
| **Score badge** | Confidence out of 100 (min 72 to appear) | 85+ = high conviction |
| **Valid until** | Latest time to enter — do not trade after this | Check before entry |
| **Lots** | Position size calculated for your risk % | Use exactly this many lots |
| **RR** | Risk-to-reward ratio | Higher is better, minimum 1:2 |
| **Entry** | Target buy price for the option premium | Place limit order here |
| **Stop Loss** | Exit price if trade goes wrong | Set in broker immediately |
| **T1 / T2 / T3** | Three profit targets | T2 auto-closes in journal |
| **Expiry** | Option expiry date (DTE = days to expiry) | Match in broker |
| **IV Rank** | Green = cheap IV (good for buyers), Red = expensive | Prefer green |
| **15m ✓** | 15-min EMA confirms the daily trend | Extra confidence |

### Score badge colours

| Colour | Score | What it means |
|---|---|---|
| Green gradient | 85–100 | High conviction — best setups |
| Amber gradient | 72–84 | Acceptable — all gates passed |
| Grey (rejected) | Below 72 | Failed scoring — do not trade |

---

## Executing the Trade in Your Broker

**Step 6 — Find the option in your broker**

1. Open your broker app (Zerodha Kite / Groww / Upstox / etc.)
2. Search for the instrument — e.g. `BANKNIFTY`
3. Go to **Options Chain**
4. Select the expiry shown on the card (e.g. `12 Jun 2025`)
5. Find the strike price shown (e.g. `56000`)
6. Select **CE** for BUY signals or **PE** for SELL signals

**Step 7 — Place the order**

| Setting | Value |
|---|---|
| Order type | **Limit** (not Market — options have wide spreads) |
| Price | Entry price from the card (e.g. ₹185) |
| Quantity | Lots × lot size (e.g. 2 lots × 15 = 30 qty) |
| Product | MIS (intraday) or NRML (positional, if DTE > 1) |

> **Never use Market Order for options.** The bid-ask spread can cause significant slippage.
> If your Limit order does not fill within 2–3 minutes, adjust price by ₹1–2.

**Step 8 — Set Stop Loss in broker immediately**

After your entry fills:
1. Place a **Stop Loss Market order** (SL-M) at the SL price shown on the card
2. This is your safety net — do not rely only on Telegram alerts

---

## After Entry — Update the Journal

**Step 9 — Log the trade in the dashboard**

1. Click **Log Trade** on the signal card — it pre-fills all fields automatically
2. Verify Entry Price matches your actual fill price (adjust if different)
3. Click **Save Trade**
4. Find the trade in the journal table → change status from **paper → open**

This ensures:
- The price monitor starts watching your position
- Your drawdown limits are correctly tracked
- The loss streak gate uses real data

---

## During the Trade — What to Watch

The **Price Monitor** runs every 60 seconds and sends Telegram alerts.

| Alert you receive | What it means | What to do |
|---|---|---|
| `T1 HIT — Trail stop to entry` | First target reached | Move your broker SL up to your entry price (trade is now risk-free) |
| `T2 HIT — Closing as WIN` | Second target reached — journal auto-closes | Close position in broker, verify journal shows win |
| `T3 HIT — Closing as WIN` | Third target reached — journal auto-closes | Close position in broker |
| `STOP LOSS HIT — Closing as LOSS` | SL triggered — journal auto-closes | Verify broker closed your position at SL |

> **Always verify in your broker.** The dashboard tracks paper P&L but does not execute orders.
> If your broker SL fires, close in broker first, then verify journal is updated.

---

## Exit Rules

### Taking profit at T1 (partial exit strategy)
When T1 is hit, you have two choices:
- **Trail and hold** — move SL to entry, hold for T2/T3 (recommended)
- **Exit all** — take the 1R profit and close (safer, lower reward)

The system sends a "Trail stop to entry" alert at T1 — this is the recommended approach.

### Forced exit (time-based)
Exit all open positions by **3:15 PM IST** regardless of P&L.
Do not hold options into the closing auction (3:15–3:30 PM).

### Forced exit (loss-based)
If your daily loss reaches **3% of account capital**, stop trading for the day.
The system will show No Trade Mode on the next scan automatically.

---

## End of Day — Closing the Loop

**Step 10 — Update trade outcomes**

1. Go to the **Journal** section
2. For any trades still showing as "open":
   - Enter your actual **Exit Price**
   - Set **Outcome** to win or loss
   - Enter **P&L (R)** — e.g. `2.0` means you made 2× your risk amount
3. Click Save

**Step 11 — Review analytics**

Check the Analytics bar above the journal table:

| Metric | Healthy range |
|---|---|
| Win Rate | Above 45% (system targets ~55%) |
| Profit Factor | Above 1.5 (system targets ~2.0+) |
| Avg Win / Avg Loss | Avg Win should be at least 1.5× Avg Loss |

**Step 12 — Export if needed**

Click **Export CSV** to download all trades for your own records or tax tracking.

---

## Hard Rules — Never Break These

| Rule | Reason |
|---|---|
| Never enter after the **Valid Until** time | Price has moved, the entry is stale |
| Never trade more **lots** than shown | Each lot over the limit breaks your risk sizing |
| Never trade when a red scan error banner is showing | NSE is offline — the signal has no live data behind it |
| Stop trading when Loss Streak reaches 3 | The system blocks signals automatically — trust it |
| Always set SL in your broker immediately after entry | Dashboard alerts are informational, not a replacement for broker SL |
| Never use Market Order for options | Slippage can be 5–10% of the premium |
| Never hold options past 3:15 PM | Liquidity drops and spreads widen into close |

---

## Quick Reference Card

```
MORNING ROUTINE
  1. .\start.ps1  →  localhost:8000
  2. Set Capital + Risk %
  3. 9:20 AM → Run Scan

SIGNAL CHECK
  4. Score ≥ 72?  Valid Until not passed?  15m ✓?
  5. BUY CE (bullish) or SELL PE (bearish)
  6. Note: Entry · SL · T1 · T2 · T3 · Lots · Expiry

BROKER ENTRY
  7. Find option (symbol + strike + expiry)
  8. Limit order at Entry price
  9. Set SL-M order at Stop Loss price immediately

JOURNAL
  10. Log Trade → Save → Change status paper → open

DURING TRADE
  11. T1 alert → trail SL to entry in broker
  12. T2/T3 alert → close in broker, verify journal
  13. SL alert → exit broker, verify journal

END OF DAY
  14. Update all outcomes + P&L (R)
  15. Review analytics
  16. Export CSV if needed
```

---

## Frequently Asked Questions

**Q: The signal card disappeared after I refreshed. Where did my signal go?**
Signals only appear after a scan. Click Run Scan again — if market conditions changed, the signal may no longer qualify.

**Q: My entry didn't fill at the limit price. What do I do?**
Adjust limit price by ₹1–2 toward the ask. If still not filling after 3 minutes, skip the trade — chasing premium defeats the risk/reward.

**Q: The journal shows P&L (R) but I want to see it in rupees.**
Multiply P&L (R) × Risk Per Trade in rupees. Example: 2.0 R × ₹1,000 risk = ₹2,000 profit.

**Q: Can I take more than 5 trades per day?**
No. The system caps at 5 approved signals per scan. More trades = more exposure, lower edge. Quality over quantity.

**Q: What if NSE is down during market hours?**
The NSE session watchdog will detect this and send a Telegram alert. Do not trade until you get an "NSE reconnected" alert or restart the server.

**Q: Should I trade every signal that appears?**
No. Use your own judgement on market context. The signal passed all algorithmic gates — but you are the final decision maker. If the broader market is in free-fall, it is always acceptable to sit out.
