"""Persist Claude's daily trade picks and score their performance over time.

On every full run:
  1. Load data/trade_history.json (list of past entries)
  2. For each call from today's narrative.trade_thesis.coins, stamp it with
     today's date + the entry price pulled from market.prices. We only add a
     call for a (date, ticker) combo once, so re-running the pipeline in a
     day doesn't duplicate entries.
  3. For every historical call, compute current PnL from today's price and
     days-held.
  4. Write the enriched history back to disk and return a summary payload
     the dashboard can render (hit rate, average return, best/worst, open
     calls table).

Prices come from the compiled market snapshot — no extra API calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from config import load_json, save_json, get_logger, DATA_DIR

log = get_logger("trade_tracker")

HISTORY_FILE = "trade_history.json"
MAX_TRACKED_DAYS = 90  # prune entries older than this to keep the file tidy


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_between(start_iso: str, end_iso: str) -> int:
    try:
        a = datetime.strptime(start_iso[:10], "%Y-%m-%d")
        b = datetime.strptime(end_iso[:10], "%Y-%m-%d")
        return max(0, (b - a).days)
    except Exception:
        return 0


def _extract_price(prices: dict, ticker: str) -> float | None:
    """Pull a spot price for a ticker out of the compiled market.prices blob.

    market.prices is keyed by upper-case ticker (SOL, BTC, JTO, ...). Return
    None when the ticker wasn't tracked so we don't plant a stale anchor.
    """
    if not ticker:
        return None
    key = ticker.upper().strip()
    entry = (prices or {}).get(key)
    if isinstance(entry, dict):
        p = entry.get("price")
        if isinstance(p, (int, float)) and p > 0:
            return float(p)
    return None


def _prune_stale(history: list, today_iso: str) -> list:
    """Drop entries older than MAX_TRACKED_DAYS so the file doesn't grow forever."""
    kept = []
    for e in history:
        age = _days_between(e.get("date", today_iso), today_iso)
        if age <= MAX_TRACKED_DAYS:
            kept.append(e)
    return kept


def _summarize(history: list, prices: dict, today_iso: str) -> dict:
    """Compute aggregate stats + a list of current-return annotated calls."""
    enriched = []
    pnls = []
    wins = 0
    losses = 0

    for e in history:
        ticker = e.get("ticker", "")
        entry_price = e.get("entry_price")
        if not ticker or not isinstance(entry_price, (int, float)) or entry_price <= 0:
            enriched.append(e)
            continue
        current = _extract_price(prices, ticker)
        pnl_pct = None
        if current is not None:
            pnl_pct = round((current - entry_price) / entry_price * 100, 2)
            pnls.append(pnl_pct)
            if pnl_pct > 0:
                wins += 1
            elif pnl_pct < 0:
                losses += 1
        enriched.append({
            **e,
            "current_price": current,
            "pnl_pct": pnl_pct,
            "days_held": _days_between(e.get("date", today_iso), today_iso),
        })

    total_scored = wins + losses
    hit_rate = round(wins / total_scored * 100, 1) if total_scored else None
    avg_return = round(sum(pnls) / len(pnls), 2) if pnls else None
    best = max(enriched, key=lambda e: e.get("pnl_pct") or -9999, default=None)
    worst = min(enriched, key=lambda e: e.get("pnl_pct") or 9999, default=None)

    return {
        "generated_at": today_iso,
        "total_calls": len(history),
        "scored_calls": total_scored,
        "wins": wins,
        "losses": losses,
        "hit_rate_pct": hit_rate,
        "avg_return_pct": avg_return,
        "best_call": best if best and best.get("pnl_pct") is not None else None,
        "worst_call": worst if worst and worst.get("pnl_pct") is not None else None,
        "calls": enriched,
    }


def run() -> dict:
    """Ingest today's picks, score history, persist and return a summary."""
    narrative = load_json("narrative.json") or {}
    compiled = load_json("compiled.json") or {}
    prices = (compiled.get("market", {}) or {}).get("prices", {}) or {}

    today = _today_iso()
    history_doc = load_json(HISTORY_FILE) or {}
    history: list = history_doc.get("entries", []) if isinstance(history_doc, dict) else []

    # De-dupe key: (date, ticker). Only accept calls with a real entry price.
    existing_keys = {(e.get("date"), e.get("ticker", "").upper()) for e in history}

    trade_thesis = (narrative.get("trade_thesis") or {})
    coins = trade_thesis.get("coins") or []
    added = 0
    for c in coins:
        ticker = (c.get("ticker") or "").upper().strip()
        reason = c.get("reason", "")
        if not ticker:
            continue
        if (today, ticker) in existing_keys:
            continue
        entry_price = _extract_price(prices, ticker)
        if entry_price is None:
            log.info(f"  skip {ticker} — no spot price in market data to anchor")
            continue
        history.append({
            "date": today,
            "ticker": ticker,
            "entry_price": entry_price,
            "reason": reason[:240],
            "conviction": trade_thesis.get("conviction"),
        })
        added += 1

    history = _prune_stale(history, today)
    summary = _summarize(history, prices, today)

    save_json({"entries": history, "summary": summary}, HISTORY_FILE)

    if added:
        log.info(f"  Recorded {added} new trade call(s) for {today}")
    if summary["scored_calls"]:
        log.info(
            f"  Backtest: {summary['scored_calls']} calls scored, "
            f"hit rate {summary['hit_rate_pct']}% , avg return {summary['avg_return_pct']}%"
        )
    else:
        log.info("  No scorable calls in history yet — backtest will populate over time")

    return summary


if __name__ == "__main__":
    run()
