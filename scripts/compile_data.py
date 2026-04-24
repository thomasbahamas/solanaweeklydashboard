"""Compile all fetched data into a single payload for dashboard + AI generation."""

import json
from pathlib import Path
from config import load_json, save_json, get_logger, now_utc, DATA_DIR

log = get_logger("compile")


def load_previous() -> dict:
    """Load previous day's compiled data for WoW comparisons."""
    path = DATA_DIR / "previous.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def save_previous(compiled: dict):
    """Save current compiled data as previous for next run's WoW."""
    path = DATA_DIR / "previous.json"
    with open(path, "w") as f:
        json.dump(compiled, f, indent=2, default=str)


def get_run_number() -> int:
    """Increment and return run counter."""
    meta_path = DATA_DIR / "meta.json"
    meta = {}
    if meta_path.exists():
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except Exception:
            pass
    run_num = meta.get("run_number", 0) + 1
    meta["run_number"] = run_num
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    return run_num


def compute_wow(current: dict, previous: dict) -> dict:
    """Compute week-over-week deltas for key metrics."""
    if not previous:
        return {}

    wow = {}

    # SOL price
    try:
        cur_sol = current["market"]["prices"]["SOL"]["price"]
        prev_sol = previous["market"]["prices"]["SOL"]["price"]
        if prev_sol:
            wow["sol_price"] = round((cur_sol - prev_sol) / prev_sol * 100, 1)
    except (KeyError, TypeError, ZeroDivisionError):
        pass

    # SOL TVL
    try:
        cur_tvl = current["solana"]["solana_tvl"]["current"]
        prev_tvl = previous["solana"]["solana_tvl"]["current"]
        if prev_tvl:
            wow["sol_tvl"] = round((cur_tvl - prev_tvl) / prev_tvl * 100, 1)
    except (KeyError, TypeError, ZeroDivisionError):
        pass

    # DEX volume
    try:
        cur_dex = current["solana"]["dex_volumes"]["combined_24h"]
        prev_dex = previous["solana"]["dex_volumes"]["combined_24h"]
        if prev_dex:
            wow["dex_volume"] = round((cur_dex - prev_dex) / prev_dex * 100, 1)
    except (KeyError, TypeError, ZeroDivisionError):
        pass

    # Total market cap
    try:
        cur_mc = current["market"]["global"]["total_market_cap"]
        prev_mc = previous["market"]["global"]["total_market_cap"]
        if prev_mc:
            wow["total_market_cap"] = round((cur_mc - prev_mc) / prev_mc * 100, 1)
    except (KeyError, TypeError, ZeroDivisionError):
        pass

    # BTC dominance delta (absolute)
    try:
        cur_dom = current["market"]["global"]["btc_dominance"]
        prev_dom = previous["market"]["global"]["btc_dominance"]
        wow["btc_dominance"] = round(cur_dom - prev_dom, 2)
    except (KeyError, TypeError):
        pass

    # SOL dominance delta (absolute)
    try:
        cur_dom = current["market"]["global"]["sol_dominance"]
        prev_dom = previous["market"]["global"]["sol_dominance"]
        wow["sol_dominance"] = round(cur_dom - prev_dom, 3)
    except (KeyError, TypeError):
        pass

    # TPS
    try:
        cur_tps = current["solana"]["network"]["tps_total"]
        prev_tps = previous["solana"]["network"]["tps_total"]
        if prev_tps:
            wow["tps"] = round((cur_tps - prev_tps) / prev_tps * 100, 1)
    except (KeyError, TypeError, ZeroDivisionError):
        pass

    # Fees 24h
    try:
        cur_fees = current["solana"]["fees"]["total_24h"]
        prev_fees = previous["solana"]["fees"]["total_24h"]
        if prev_fees:
            wow["fees_24h"] = round((cur_fees - prev_fees) / prev_fees * 100, 1)
    except (KeyError, TypeError, ZeroDivisionError):
        pass

    # Stablecoins on Solana
    try:
        cur_stables = current["solana"]["stablecoins"]["total"]
        prev_stables = previous["solana"]["stablecoins"]["total"]
        if prev_stables:
            wow["stables_tvl"] = round((cur_stables - prev_stables) / prev_stables * 100, 1)
    except (KeyError, TypeError, ZeroDivisionError):
        pass

    return wow


def run(save_baseline: bool = True) -> dict:
    """Compile data and compute WoW deltas against the last full run.

    Pass `save_baseline=False` from market-only refreshes so WoW stays
    anchored to the daily full-pipeline snapshot instead of slipping
    to "delta since 4 hours ago".
    """
    log.info("Compiling data...")

    previous = load_previous()
    if previous:
        log.info(f"  Loaded previous run from {previous.get('generated_at', '?')} (run #{previous.get('run_number', '?')})")
    else:
        log.warning("  No previous.json found — cache may not be restoring; WoW will be empty this run")
    run_number = get_run_number()

    market = load_json("market.json")
    solana = load_json("solana.json")
    news = load_json("news.json")
    whales = load_json("whales.json")
    upgrades = load_json("upgrades.json")
    hyperliquid = load_json("hyperliquid.json")
    stocks = load_json("stocks.json")
    treasuries = load_json("treasuries.json")

    compiled = {
        "generated_at": now_utc(),
        "run_number": run_number,
        "market": market,
        "solana": solana,
        "news": news,
        "whales": whales,
        "upgrades": upgrades,
        "hyperliquid": hyperliquid,
        "stocks": stocks,
        "treasuries": treasuries,
    }

    # Compute WoW deltas
    wow = compute_wow(compiled, previous)
    compiled["wow"] = wow
    if wow:
        log.info(f"  WoW deltas: {wow}")
    else:
        log.info("  No previous data for WoW comparison (first run or missing)")

    save_json(compiled, "compiled.json")

    # Save as previous for next DAILY run. Market-only refreshes pass
    # save_baseline=False so a 4h cron doesn't overwrite the 24h anchor.
    if save_baseline:
        save_previous(compiled)
        log.info(f"Compiled data saved (Run #{run_number}) — baseline refreshed.")
    else:
        log.info(f"Compiled data saved (Run #{run_number}) — baseline preserved (market-only refresh).")
    return compiled


if __name__ == "__main__":
    run()
