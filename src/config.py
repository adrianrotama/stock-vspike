import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# --- Screening thresholds (defaults; override via backtest optimizer) ---
MIN_PRICE = 100                     # IDR – filter penny stocks
MIN_AVG_TXN_VALUE = 1_000_000       # IDR 1 M average daily transaction
VOLUME_SMA_WINDOW = 10              # days for RVOL baseline
RVOL_THRESHOLD = 4.0                # relative‑volume spike multiplier
PRICE_POSITION_MIN = 0.5            # close must be in upper half of day range

# --- Entry ---
RETRACE_PCT = 3.0                   # max % retracement from pre‑spike close
EMA_PERIOD = 10                     # EMA period for MA‑reclaim confirmation
MFI_PERIOD = 14                     # Money Flow Index look‑back
MFI_MIN = 20                        # minimum MFI at entry

# --- Exit ---
SL_PCT = 5.0                        # stop‑loss % below pre‑spike close
ATR_PERIOD = 14                     # ATR look‑back for adaptive SL
TRAILING_STOP_PCT = 2.5             # trailing‑stop distance (TP mode 3)

# --- Take‑Profit modes ---
# 1 = breakout (spike‑day high), 2 = MA breakdown, 3 = trailing stop
TP_MODE = 2

# --- Data ---
TICKER_CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "idx_tickers.csv")
SIGNALS_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "signals.db")
HISTORY_DAYS = 120                  # how far back to fetch daily OHLCV
