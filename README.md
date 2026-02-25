# IDX Volume-Spike Stock Screener

Automated screener for Indonesian (IDX) stocks that detects volume spikes, generates entry/exit signals, and sends alerts to Telegram — running entirely free on GitHub Actions.

## How It Works

1. **Screen** all IDX stocks for unusual volume spikes (Relative Volume >= threshold).
2. **Filter** by price, liquidity, candle confirmation, and trend direction.
3. **Signal** entries when price retraces to the pre-spike level and reclaims its EMA, confirmed by Money Flow Index.
4. **Exit** via breakout target, MA breakdown, or trailing stop (configurable).
5. **Notify** via Telegram — daily reports at night + intraday alerts every 15 minutes.
6. **Backtest** and optimise all parameters with built-in parameter grid search.

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USER/stock-vspike.git
cd tradingView
pip install -r requirements.txt
```

### 2. Configure Telegram

Create a `.env` file (or set environment variables):

```bash
cp .env.example .env
# Edit .env with your Telegram bot token and chat ID
```

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token (from @BotFather) |
| `TELEGRAM_CHAT_ID` | Target chat/group ID |

### 3. Run locally

```bash
# Daily scan (sends spike report to Telegram)
python scripts/run_daily.py

# Intraday scan (checks entry/exit signals)
python scripts/run_intraday.py

# Backtest a single ticker
python scripts/run_backtest.py BBCA

# Backtest multiple tickers
python scripts/run_backtest.py BBCA TLKM BMRI --days 365

# Compare all 3 take-profit modes
python scripts/run_backtest.py BBCA --compare

# Show Trades
python scripts/run_backtest.py BBCA --trades
```

### 4. Deploy to GitHub Actions (free)

1. Push this repo to GitHub.
2. Go to **Settings > Secrets and Variables > Actions**.
3. Add two repository secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. The workflows will run automatically:
   - **Daily scan**: Mon–Fri at 19:00 WIB (12:00 UTC)
   - **Intraday scan**: every 15 min during market hours (09:15–15:00 WIB)

You can also trigger any workflow manually from the Actions tab.

## Signal Algorithm

### Screening Filters (all must be true)

| # | Filter | Default |
|---|---|---|
| 1 | Close >= minimum price | IDR 100 |
| 2 | 20-day avg transaction value >= threshold | IDR 1M |
| 3 | Relative Volume (RVOL) >= threshold | 5x |
| 4 | Green candle (close > open) | — |
| 5 | Close in upper 50% of day's range | 0.5 |
| 6 | Close > previous close | — |

### Entry Criteria

- Price retraces to within X% of pre-spike close (default: 3%)
- Price crosses above EMA (default: EMA 10) — "MA reclaim"
- Money Flow Index >= minimum (default: 40)

### Exit Modes

| Mode | Description |
|---|---|
| 1 – Breakout | Exit when price exceeds spike-day high |
| 2 – MA Breakdown | Exit when close drops below EMA while in profit |
| 3 – Trailing Stop | Trail with X% stop after entering profit (default: 2.5%) |

### Stop Loss

`max(sl_pct%, 1 x ATR)` below the pre-spike close. Adapts to each stock's volatility.

## Backtesting & Parameter Optimisation

The optimiser tests combinations of:

| Parameter | Range | Default |
|---|---|---|
| `rvol_threshold` | 3–10 | 5 |
| `retrace_pct` | 1–8% | 3% |
| `ema_period` | 5, 10, 20 | 10 |
| `sl_pct` | 2–5% | 3% |
| `tp_mode` | 1, 2, 3 | 2 |
| `trailing_pct` | 1–5% | 3% |
| `mfi_min` | 20–60 | 20 |
| `vol_window` | 10–30 | 20 |

Output includes: Win Rate, Return %, Max Drawdown, Sharpe Ratio, Expectancy, and number of trades.

```bash
# Full optimisation (random sample of ~300 combinations)
python scripts/run_backtest.py BBCA --days 730

# Compare TP modes side by side
python scripts/run_backtest.py BBCA BMRI --compare
```

## Configuration

All defaults live in `src/config.py`. Override them by editing the file or via the backtest optimizer to find optimal values for your target stocks.

## Project Structure

```
├── .github/workflows/
│   ├── daily_scan.yml          # Cron: 19:00 WIB, Mon-Fri
│   └── intraday_scan.yml       # Cron: every 15 min, market hours
├── src/
│   ├── config.py               # All configurable parameters
│   ├── data/
│   │   ├── ticker_list.py      # IDX ticker list fetcher + CSV cache
│   │   └── market_data.py      # yfinance OHLCV wrapper
│   ├── screener/
│   │   ├── volume_spike.py     # RVOL detection + filters
│   │   └── signal_generator.py # Entry / TP / SL logic
│   ├── backtest/
│   │   ├── strategy.py         # backtesting.py Strategy class
│   │   └── optimizer.py        # Parameter grid search
│   └── notify/
│       └── telegram.py         # Telegram message formatting + send
├── scripts/
│   ├── run_daily.py            # Daily scan entry point
│   ├── run_intraday.py         # Intraday scan entry point
│   └── run_backtest.py         # Backtest runner
├── data/                       # Runtime data (CSV cache, SQLite DB)
├── requirements.txt
└── .env.example
```

## Cost

| Component | Cost |
|---|---|
| yfinance | Free |
| GitHub Actions | Free (public repo) / 2,000 min/month (private) |
| Telegram Bot API | Free |
| **Total** | **$0** |
