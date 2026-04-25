"""Run the full Solana Weekly dashboard pipeline.

Usage:
    python scripts/run_pipeline.py              # Full pipeline (fetch + AI + dashboard)
    python scripts/run_pipeline.py --data-only  # Data fetch only (no AI, no dashboard)
    python scripts/run_pipeline.py --no-ai      # Data + dashboard, skip AI narrative
"""

import sys
import time
import argparse
from config import get_logger

log = get_logger("pipeline")


def run_step(name: str, module_name: str) -> dict:
    """Import and run a step, returning its result."""
    log.info(f"{'='*50}")
    log.info(f"STEP: {name}")
    log.info(f"{'='*50}")
    start = time.time()

    try:
        mod = __import__(module_name)
        result = mod.run()
        elapsed = time.time() - start
        log.info(f"  ✓ {name} completed in {elapsed:.1f}s")
        return result
    except Exception as e:
        elapsed = time.time() - start
        log.error(f"  ✗ {name} failed after {elapsed:.1f}s: {e}")
        import traceback
        traceback.print_exc()
        return {}


def main():
    parser = argparse.ArgumentParser(description="Solana Weekly Dashboard Pipeline")
    parser.add_argument("--data-only", action="store_true", help="Only fetch data, skip AI and dashboard")
    parser.add_argument("--no-ai", action="store_true", help="Skip AI narrative generation")
    parser.add_argument("--market-only", action="store_true", help="Quick refresh: fetch market data only, recompile, regenerate dashboard")
    parser.add_argument("--skip-delivery", action="store_true", help="Build newsletter draft but do not send it")
    args = parser.parse_args()

    log.info("=" * 60)
    total_start = time.time()

    # Quick market-only refresh: fetch prices, recompile (without
    # clobbering the WoW baseline), regenerate dashboard.
    if args.market_only:
        log.info("MARKET-ONLY REFRESH")
        log.info("=" * 60)
        run_step("Fetch Market Data", "fetch_market")
        # Can't use run_step's __import__().run() here because we need a kwarg.
        try:
            import compile_data as _cd
            _cd.run(save_baseline=False)
        except Exception as e:
            log.error(f"  ✗ Compile Data failed: {e}")
            raise SystemExit(1)
        verification = run_step("Verify Data Quality", "verify_data")
        if verification.get("status") == "FAIL":
            log.error("Market-only refresh refused to publish partial dashboard")
            raise SystemExit(1)
        run_step("Generate Dashboard", "generate_dashboard")
        total_elapsed = time.time() - total_start
        log.info("")
        log.info("=" * 60)
        log.info(f"MARKET REFRESH COMPLETE — {total_elapsed:.1f}s total")
        log.info("=" * 60)
        return

    log.info("SOLANA WEEKLY DASHBOARD PIPELINE")
    log.info("=" * 60)

    # Step 1: Fetch all data
    run_step("Fetch Market Data", "fetch_market")
    time.sleep(1)  # Rate limit buffer

    run_step("Fetch Solana Ecosystem", "fetch_solana")
    time.sleep(1)

    run_step("Fetch News", "fetch_news")
    time.sleep(1)

    run_step("Fetch Whale Intelligence", "fetch_whales")
    time.sleep(1)

    run_step("Fetch Hyperliquid Markets", "fetch_hyperliquid")
    time.sleep(1)

    run_step("Fetch Tokenized Stocks", "fetch_stocks")
    time.sleep(1)

    run_step("Fetch Network Upgrades", "fetch_upgrades")
    time.sleep(1)

    run_step("Fetch BTC Treasuries", "fetch_treasuries")

    # Step 2: Compile
    run_step("Compile Data", "compile_data")

    # Step 2.5: Verify data quality
    verification = run_step("Verify Data Quality", "verify_data")
    if verification.get("status") == "FAIL":
        log.warning("Data quality check FAILED — continuing but dashboard may have issues")

    if args.data_only:
        log.info("Data-only mode — stopping here.")
        return

    # Step 3: AI Narrative
    if not args.no_ai:
        run_step("Generate Signal (Claude API)", "generate_signal")
    else:
        log.info("Skipping AI narrative (--no-ai flag)")

    # Step 3.5: Track and score trade picks over time (no extra API calls —
    # uses compiled market prices to anchor entries and score history).
    run_step("Track Trade Picks", "track_trades")

    # Step 4: Generate dashboard
    run_step("Generate Dashboard", "generate_dashboard")

    # Step 4.5: Generate OG share card PNG (reads narrative.json + compiled.json)
    run_step("Generate OG Image", "generate_og_image")

    # Step 5: Generate newsletter draft
    run_step("Generate Newsletter", "generate_newsletter")

    # Step 6: Send newsletter via Kit
    if args.skip_delivery:
        log.info("Skipping newsletter delivery (--skip-delivery flag)")
    else:
        run_step("Deliver Newsletter", "deliver_newsletter")

    total_elapsed = time.time() - total_start
    log.info("")
    log.info("=" * 60)
    log.info(f"PIPELINE COMPLETE — {total_elapsed:.1f}s total")
    log.info(f"Output: output/index.html")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
