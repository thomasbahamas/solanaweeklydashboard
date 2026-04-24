"""Fetch Hyperliquid perp market data — top volume coins + new listing detection.

Hyperliquid is a high-volume perp DEX with its own L1. It's not a Solana
protocol, but volume and listing activity there are a strong cross-market
signal: new listings often front-run broader market attention, and the
volume mix shows where leveraged flow is concentrated.

Data source: https://api.hyperliquid.xyz/info (public, unauthenticated).

  POST {"type": "metaAndAssetCtxs"}
    -> [{"universe": [{"name": "BTC", ...}, ...]},
        [{"dayNtlVlm": "...", "markPx": "...", "prevDayPx": "...",
          "openInterest": "...", "funding": "...", ...}, ...]]

The universe array and the contexts array are index-aligned.

New listing detection: we persist the set of previously-seen coin names in
`data/hyperliquid_known.json` and diff each run. That file is cached across
GitHub Actions runs alongside `previous.json` (see .github/workflows/daily.yml).
"""

from __future__ import annotations

import json
import requests
from config import save_json, get_logger, now_utc, DATA_DIR, DEFAULT_HEADERS

log = get_logger("fetch_hyperliquid")

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
KNOWN_FILE = DATA_DIR / "hyperliquid_known.json"

# Known stock/ETF tickers on Hyperliquid (HIP-3 stock perps).
# Used alongside a maxLeverage heuristic to separate stocks from crypto.
STOCK_TICKERS = {
    # Mega-cap tech
    "AAPL", "MSFT", "GOOG", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "NFLX",
    # Semiconductors
    "AMD", "INTC", "AVGO", "QCOM", "TSM", "MU",
    # Crypto-adjacent
    "COIN", "MSTR", "HOOD", "SQ", "RIOT", "MARA",
    # Meme stocks
    "GME", "AMC",
    # Growth
    "PLTR", "SOFI", "RIVN", "SNOW", "SHOP", "UBER", "ABNB", "RBLX",
    # ETFs / indices
    "SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "USO",
    # Financials
    "JPM", "GS", "MS", "BAC", "WFC", "V", "MA",
    # Energy
    "XOM", "CVX", "COP", "OXY",
    # Defense
    "BA", "LMT", "RTX", "NOC",
    # Healthcare
    "LLY", "UNH", "PFE", "ABBV", "MRK",
    # Consumer
    "NKE", "MCD", "KO", "PEP", "WMT", "COST", "DIS",
    # Enterprise
    "CRM", "ORCL", "ADBE",
}

# Minimum 24h notional volume to qualify as a "top mover"
MOVER_MIN_VOLUME = 100_000


def _post(body: dict) -> list | dict | None:
    """POST JSON to Hyperliquid info endpoint with retries."""
    for attempt in range(3):
        try:
            resp = requests.post(
                HL_INFO_URL,
                json=body,
                headers={**DEFAULT_HEADERS, "Content-Type": "application/json"},
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"Hyperliquid POST {body.get('type')} failed (attempt {attempt+1}): {e}")
    return None


def _to_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _is_stock(name: str, max_leverage) -> bool:
    """Identify if a perp is a stock/ETF rather than crypto.

    Only matches against the curated STOCK_TICKERS set. The maxLeverage
    heuristic was removed because many small-cap crypto perps also have
    low leverage limits (3-5x).
    """
    return name.upper() in STOCK_TICKERS


def _load_known() -> set:
    """Load the set of coin names seen in prior runs."""
    if not KNOWN_FILE.exists():
        return set()
    try:
        with open(KNOWN_FILE) as f:
            data = json.load(f)
        return set(data.get("coins", []))
    except Exception as e:
        log.warning(f"Could not read {KNOWN_FILE.name}: {e}")
        return set()


def _save_known(coins: set, added: list):
    """Persist the union of prior + current coin names."""
    payload = {
        "updated_at": now_utc(),
        "coins": sorted(coins),
        "last_added": added,
    }
    with open(KNOWN_FILE, "w") as f:
        json.dump(payload, f, indent=2)


def fetch_perps() -> dict:
    """Fetch perp market contexts and rank by 24h notional volume."""
    data = _post({"type": "metaAndAssetCtxs"})
    if not data or not isinstance(data, list) or len(data) < 2:
        log.error("  metaAndAssetCtxs returned unexpected shape")
        return {}

    universe = data[0].get("universe", []) if isinstance(data[0], dict) else []
    ctxs = data[1] if isinstance(data[1], list) else []

    if len(universe) != len(ctxs):
        log.warning(f"  universe/ctxs length mismatch: {len(universe)} vs {len(ctxs)}")

    coins = []
    for i, meta in enumerate(universe):
        if i >= len(ctxs):
            break
        ctx = ctxs[i] or {}
        name = meta.get("name", "?")
        # Hyperliquid marks delisted markets with isDelisted=true. Skip them.
        if meta.get("isDelisted"):
            continue
        mark = _to_float(ctx.get("markPx"))
        prev = _to_float(ctx.get("prevDayPx"))
        change_24h = None
        if prev > 0:
            change_24h = round((mark - prev) / prev * 100, 2)
        max_lev = meta.get("maxLeverage")
        coins.append({
            "name": name,
            "mark_px": mark,
            "prev_day_px": prev,
            "change_24h": change_24h,
            "day_ntl_vlm": _to_float(ctx.get("dayNtlVlm")),
            "day_base_vlm": _to_float(ctx.get("dayBaseVlm")),
            "open_interest": _to_float(ctx.get("openInterest")),
            "funding": _to_float(ctx.get("funding")),
            "max_leverage": max_lev,
            "is_stock": _is_stock(name, max_lev),
        })

    coins.sort(key=lambda c: c["day_ntl_vlm"], reverse=True)

    total_vlm = sum(c["day_ntl_vlm"] for c in coins)
    total_oi_notional = sum(c["open_interest"] * c["mark_px"] for c in coins)

    return {
        "coins": coins,
        "total_vlm_24h": total_vlm,
        "total_oi_notional": total_oi_notional,
        "num_markets": len(coins),
    }


def detect_new_listings(current_names: set) -> dict:
    """Diff current universe against persisted known set; update known set."""
    known = _load_known()
    is_first_run = len(known) == 0

    added = sorted(current_names - known) if not is_first_run else []
    removed = sorted(known - current_names) if not is_first_run else []

    # Union so once-seen coins stay known even after temporary delisting.
    _save_known(known | current_names, added)

    return {
        "added": added,
        "removed": removed,
        "first_run": is_first_run,
        "known_count": len(known | current_names),
    }


def run() -> dict:
    log.info("Fetching Hyperliquid perp markets...")

    perps = fetch_perps()
    coins = perps.get("coins", [])
    if not coins:
        log.warning("  No Hyperliquid perp data returned")
        result = {
            "timestamp": now_utc(),
            "source": "https://api.hyperliquid.xyz/info",
            "market_type": "perps",
            "top_coins": [],
            "total_vlm_24h": 0,
            "total_oi_notional": 0,
            "num_markets": 0,
            "new_listings": {"added": [], "removed": [], "first_run": False, "known_count": 0},
        }
        save_json(result, "hyperliquid.json")
        return result

    top_10 = coins[:10]
    log.info(f"  {perps['num_markets']} markets, 24h volume: ${perps['total_vlm_24h']/1e9:.2f}B")
    log.info(f"  Top coin: {top_10[0]['name']} @ ${top_10[0]['day_ntl_vlm']/1e9:.2f}B")

    current_names = {c["name"] for c in coins}
    listings = detect_new_listings(current_names)
    if listings["first_run"]:
        log.info(f"  First run — seeded known set with {listings['known_count']} coins")
    elif listings["added"]:
        log.info(f"  NEW LISTINGS: {', '.join(listings['added'])}")
    else:
        log.info("  No new listings since last run")

    # --- Stock perps (HIP-3) ---
    stock_perps = sorted(
        [c for c in coins if c.get("is_stock")],
        key=lambda c: c["day_ntl_vlm"], reverse=True,
    )
    stock_names = {c["name"] for c in stock_perps}
    new_stock_listings = [n for n in listings.get("added", []) if n in stock_names]
    log.info(f"  Stock perps: {len(stock_perps)} active")
    if new_stock_listings:
        log.info(f"  NEW STOCK LISTINGS: {', '.join(new_stock_listings)}")

    # --- Top movers (all perps with meaningful volume) ---
    active = [c for c in coins if c["day_ntl_vlm"] > MOVER_MIN_VOLUME and c["change_24h"] is not None]
    gainers = sorted([c for c in active if c["change_24h"] > 0], key=lambda c: c["change_24h"], reverse=True)[:5]
    losers = sorted([c for c in active if c["change_24h"] < 0], key=lambda c: c["change_24h"])[:5]
    if gainers:
        log.info(f"  Top gainer: {gainers[0]['name']} {gainers[0]['change_24h']:+.1f}%")
    if losers:
        log.info(f"  Top loser: {losers[0]['name']} {losers[0]['change_24h']:+.1f}%")

    result = {
        "timestamp": now_utc(),
        "source": "https://api.hyperliquid.xyz/info",
        "market_type": "perps",
        "top_coins": top_10,
        "total_vlm_24h": perps["total_vlm_24h"],
        "total_oi_notional": perps["total_oi_notional"],
        "num_markets": perps["num_markets"],
        "new_listings": listings,
        "stock_perps": stock_perps[:15],
        "new_stock_listings": new_stock_listings,
        "top_movers": {
            "gainers": gainers,
            "losers": losers,
        },
    }

    save_json(result, "hyperliquid.json")
    log.info("Hyperliquid data saved.")
    return result


if __name__ == "__main__":
    run()
