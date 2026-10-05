# Gold intraday brief

This builds a fresh plan for gold each time you run it. It reads the chart, the US dollar index, WTI crude, and dollar and oil headlines. It then keeps a journal. Approaches that keep working get more trust. Approaches that stop working get pushed out. Old lessons fade, so a plan cannot become permanent.

It does not use a fixed entry rule such as a moving-average cross. It does not send orders to a broker.

This is a research journal, not financial advice. Intraday gold can move through a stop. You can lose the amount at risk.

## What a run looks at

- Gold price structure on 5-minute bars: trend or range, where price sits in the UTC-day range, VWAP, ATR, and the 1-hour structure.
- The US dollar index. Gold usually moves against it. A quiet dollar is not treated as a dollar trade.
- WTI crude. Oil leads only when oil itself is the shock, for example a supply cut. A drifting oil price stays in the background.
- Headlines about the dollar, the Fed, yields, OPEC, and oil inventories.
- The journal. Similar recent sessions count more. A result loses half its pull after 5 days (change `half_life_days` in `config.yaml`).

Five approaches are scored on every run:

1. Trade with the dollar.
2. Trade the oil shock.
3. Trade with the intraday trend.
4. Fade the session range.
5. Stand aside.

A trade is allowed only when its adjusted score is at least 0.50.

`adjusted = how the setup looks right now x how that approach has done on similar days`

If the chart and the dollar want opposite trades and neither is clearly ahead, the plan is to stand aside.

## Setup

From this folder, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py demo
```

The demo uses two fixed sessions, not live prices. It shows the same chart producing a different plan after that approach has lost four similar sessions.

Then, for a live brief:

```powershell
python main.py brief
```

That writes:

- `reports/latest.txt`
- `reports/latest.html`
- `reports/latest.json`
- `reports/latest.png` when matplotlib is installed

`python main.py review` prints the graded journal.

## How it learns

1. `brief` reads the market and writes one plan.
2. The plan stays open until price hits the invalidation, hits the target, or 120 minutes pass.
3. The next `brief` grades that plan from the price path and updates trust.
4. Running it again before the window ends replaces the open plan and does not grade it. A refresh is not a lesson.

You do not label wins and losses by hand. The price path does that.

Gold data is `XAUUSD=X` when Yahoo has it, otherwise COMEX futures `GC=F`. Futures prices are not spot prices. The brief says so when it has to use futures. The dollar index falls back from `DX-Y.NYB` to `DX=F`. Oil is WTI `CL=F`.

## MetaTrader 5 demo trading

1. Install MetaTrader 5 from [metatrader5.com](https://www.metatrader5.com/en/download) and let the installer finish.
2. In MT5: File, then Open an Account. Pick MetaQuotes-Demo (or your broker's demo server), choose a demo account, and set the deposit to 100 USD.
3. Click the **Algo Trading** button in the MT5 toolbar so it turns green.
4. Run `python main.py mt5`. It should say `DEMO account ...` and `Orders: allowed`.

With `broker.enabled: true` in `config.yaml`, each scheduled pass:

- reads XAUUSD 5-minute bars from MT5, so levels match the broker's prices;
- sizes from MT5 equity and the symbol's lot size: 5 USD on normal trades, 15% of equity on the high tier, checked again with the broker's own profit calculator;
- places a limit order at the near edge of the entry zone, or a market order if price is already inside it, always with a stop loss and take profit;
- cancels the bot's pending orders and closes its positions when the 120-minute window ends, and before the Friday close;
- sends no orders to a real-money account while `demo_only: true`.

The US economic calendar (CPI, payrolls, FOMC and other high-impact USD releases) refreshes hourly. From 30 minutes before to 15 minutes after a high-impact release, no new orders are sent and pending orders are cancelled. Headlines refresh on every pass.

Only orders with the bot's magic number are touched. Manual trades in the same account are left alone.

It runs on this PC because MetaTrader 5 needs Windows, and there is no free Windows cloud host. Keep the PC awake and logged in during trading hours. To move it to a Windows VPS later, copy this folder there, install MT5 and Python, and run `python main.py schedule on`.

## Running automatically

```powershell
python main.py schedule on        # every 15 minutes via Windows Task Scheduler
python main.py schedule status
python main.py schedule off
```

Each scheduled pass skips weekends, the daily 21:00-22:00 UTC pause, and stale prices. A trade plan stays in charge until its invalidation, its target, or 120 minutes. Scheduled passes do not replace it early, so every plan gets graded. A stand-aside plan only gives way to a new trade plan.

You get a Windows notification when a trade plan opens and when a plan is graded. Every pass is written to `reports/auto.log`. The latest plan is in `reports/latest.html`.

The task runs only while you are logged in and the PC is awake. `python main.py watch` does the same thing in an open window instead.

## Risk settings

In `config.yaml` (account 100 USD by default):

- Normal trades lose at most `max_risk_usd` (5 USD) at the invalidation. Targets are capped at 1:`max_reward_ratio` (1:4).
- Above `high_confidence` (90%), the plan risks `high_confidence_risk_percent` (15%) of `account_usd`, and targets follow structure instead of the 1:4 cap.
- Confidence above 0.88 needs a proven record: at least 4 recent wins for that approach on similar sessions, trust of at least 1.30, and a strong setup. A good-looking chart alone cannot unlock 15% risk.
- Risk is measured from the worst fill in the entry zone. If the stop is too wide for the smallest size, the zone is tightened toward the invalidation. If that still does not fit, there is no trade.
- `min_units` and `unit_step` are 1, meaning 1 oz = 0.01 lot of XAUUSD on most brokers. Check your broker's contract size.

`python main.py review` shows a paper result: R multiple times dollars at risk for each graded trade plan.

The size line is not an order. Nothing here connects to a broker.

## Useful times

Gold is most liquid in the London / New York overlap, about 12:00 to 17:00 UTC. Run a brief at the London open, again at the overlap, and again after the horizon if a plan is still open. Run an extra brief when a Fed, jobs, CPI, or oil-inventory headline hits. The new brief replaces the old one.
