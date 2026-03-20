"""Shared configuration and utilities for Solana Weekly dashboard pipeline."""

import os
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# Paths
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
TEMPLATES_DIR = ROOT_DIR / "templates"
OUTPUT_DIR = ROOT_DIR / "output"

DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# API Keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GITHUB_PAGES_URL = os.getenv("GITHUB_PAGES_URL", "")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# Default headers for all API requests
DEFAULT_HEADERS = {
    "User-Agent": "SolanaWeeklyDashboard/1.0 (+https://solanaweekly.io)",
    "Accept": "application/json",
}

# Rate limiting delay between API calls (seconds)
API_CALL_DELAY = 1.5

# Watchlist — edit these to match your dashboard
WATCHLIST = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "hyperliquid": "HYPE",
    "zcash": "ZEC",
    "near": "NEAR",
}

# Top chains to track TVL (top 10 + Solana always included)
TOP_CHAINS = [
    "Ethereum", "BSC", "Solana", "Tron", "Bitcoin",
    "Arbitrum", "Base", "Hyperliquid L1", "Polygon", "Avalanche"
]

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

def save_json(data: dict, filename: str) -> Path:
    """Save data to JSON in the data directory."""
    path = DATA_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    return path

def load_json(filename: str) -> dict:
    """Load JSON from the data directory."""
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def api_get(url: str, params: dict = None, headers: dict = None, retries: int = 3) -> dict | list | None:
    """GET request with retry logic, rate limit respect, and inter-call delay."""
    import requests
    log = get_logger("api")

    # Merge default headers with any caller-provided headers
    merged_headers = {**DEFAULT_HEADERS}
    if headers:
        merged_headers.update(headers)

    # Add CoinGecko API key header when calling CoinGecko endpoints
    if "coingecko.com" in url and COINGECKO_API_KEY:
        merged_headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

    for attempt in range(retries):
        try:
            # Rate limiting delay before each request
            time.sleep(API_CALL_DELAY)
            resp = requests.get(url, params=params, headers=merged_headers, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 30))
                log.warning(f"Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"API call failed (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None

def rpc_post(method: str, params: list = None) -> dict | None:
    """Solana RPC JSON-RPC call."""
    import requests
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    try:
        resp = requests.post(SOLANA_RPC_URL, json=payload, headers=DEFAULT_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result")
    except Exception as e:
        get_logger("rpc").error(f"RPC call {method} failed: {e}")
        return None
