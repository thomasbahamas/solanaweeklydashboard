"""Fetch Solana ecosystem data: TVL, DEX volumes, protocol rankings, network stats."""

import statistics

from config import (
    api_get, rpc_post, save_json, get_logger, now_utc,
    TOP_CHAINS,
)

log = get_logger("fetch_solana")


def _compute_chain_delta(historical: list, days: int) -> float | None:
    """Given a historicalChainTvl series, compute pct change over the last N days.

    Series items look like {"date": unix_sec, "tvl": usd}. Uses the last point
    as "now" and the point roughly `days` back as the baseline.
    """
    if not historical or len(historical) < days + 1:
        return None
    current = historical[-1].get("tvl")
    baseline = historical[-1 - days].get("tvl")
    if not current or not baseline:
        return None
    try:
        return round((current - baseline) / baseline * 100, 1)
    except ZeroDivisionError:
        return None


def fetch_chain_tvls() -> list:
    """DeFiLlama TVL by chain — top 10 by TVL plus Solana.

    DeFiLlama's `/v2/chains` endpoint only returns current TVL (no deltas),
    so we compute 1d/7d deltas by pulling `historicalChainTvl` for each top
    chain. One request per chain — ~11 calls, fine for a daily run.
    """
    data = api_get("https://api.llama.fi/v2/chains")
    if not data:
        return []

    chains = [{"name": c.get("name", ""), "tvl": c.get("tvl", 0)} for c in data]
    chains.sort(key=lambda x: x["tvl"], reverse=True)

    top = chains[:10]
    if not any(c["name"] == "Solana" for c in top):
        sol = next((c for c in chains if c["name"] == "Solana"), None)
        if sol:
            top.append(sol)

    # Compute deltas for each top chain from historical TVL
    for entry in top:
        name = entry["name"]
        if not name:
            entry["change_1d"] = None
            entry["change_7d"] = None
            continue
        hist = api_get(f"https://api.llama.fi/v2/historicalChainTvl/{name}")
        if isinstance(hist, list) and hist:
            entry["change_1d"] = _compute_chain_delta(hist, 1)
            entry["change_7d"] = _compute_chain_delta(hist, 7)
        else:
            entry["change_1d"] = None
            entry["change_7d"] = None

    return top


def fetch_solana_tvl() -> dict:
    """DeFiLlama Solana-specific TVL with history."""
    data = api_get("https://api.llama.fi/v2/historicalChainTvl/Solana")
    if not data or not isinstance(data, list):
        return {"current": 0, "change_1d": 0}

    if len(data) >= 2:
        current = data[-1].get("tvl", 0)
        yesterday = data[-2].get("tvl", 1)
        change = round((current - yesterday) / yesterday * 100, 1) if yesterday else 0
        return {"current": current, "change_1d": change}
    return {"current": data[-1].get("tvl", 0) if data else 0, "change_1d": 0}


def _fetch_perp_protocols_fallback() -> list:
    """Fallback: when DeFiLlama's /overview/derivatives endpoint is gated on
    the Pro tier (returns 402), fetch Solana perp protocols from the free
    /protocols endpoint and rank by TVL. TVL is not volume, but it's the
    best we can get from the free tier.
    """
    data = api_get("https://api.llama.fi/protocols")
    if not data:
        return []
    perps = []
    for p in data:
        if p.get("category") != "Derivatives":
            continue
        chains = p.get("chains", [])
        if "Solana" not in chains:
            continue
        chain_tvls = p.get("chainTvls", {})
        sol_tvl = chain_tvls.get("Solana", 0)
        if not isinstance(sol_tvl, (int, float)) or sol_tvl <= 0:
            continue
        perps.append({
            "name": p.get("name", "Unknown"),
            "volume_24h": sol_tvl,  # label reused; see `perp_source` flag below
            "change_1d": round(p.get("change_1d") or 0, 1),
        })
    perps.sort(key=lambda x: x["volume_24h"], reverse=True)
    return perps[:10]


def fetch_dex_volumes() -> dict:
    """DeFiLlama DEX volumes — Solana spot + perp.

    Note on perps: DeFiLlama has moved /overview/derivatives/* behind their
    Pro subscription and it now returns 402 Payment Required. We try the
    endpoint first and fall back to a TVL-based ranking from /protocols if
    it fails. The `perp_source` key is set to "volume" or "tvl" so the
    dashboard renderer knows which label to show.
    """
    # Overall DEX volumes
    data = api_get("https://api.llama.fi/overview/dexs/solana?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyVolume")

    spot_volume = 0
    spot_change = 0
    top_spot = []

    if data:
        spot_volume = data.get("totalDataChart", [[0, 0]])
        spot_volume = data.get("total24h", 0)
        spot_change = round(data.get("change_1d", 0), 1)

        protocols = data.get("protocols", [])
        protocols.sort(key=lambda x: x.get("total24h") or 0, reverse=True)
        for p in protocols[:15]:
            top_spot.append({
                "name": p.get("name", "Unknown"),
                "volume_24h": p.get("total24h") or 0,
                "change_1d": round(p.get("change_1d") or 0, 1),
            })

    # Perp DEX volumes
    perp_data = api_get("https://api.llama.fi/overview/derivatives/solana?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyVolume")

    perp_volume = 0
    top_perps = []
    perp_source = "volume"

    if perp_data:
        perp_volume = perp_data.get("total24h", 0)
        protocols = perp_data.get("protocols", [])
        protocols.sort(key=lambda x: x.get("total24h") or 0, reverse=True)
        for p in protocols[:10]:
            vol = p.get("total24h") or 0
            # Drop dead perp venues — they're noise and make the table look padded
            if vol <= 0:
                continue
            top_perps.append({
                "name": p.get("name", "Unknown"),
                "volume_24h": vol,
                "change_1d": round(p.get("change_1d") or 0, 1),
            })

    # Fallback: DeFiLlama gated /overview/derivatives behind Pro — rank by TVL
    if not top_perps:
        log.warning("Derivatives volume endpoint unavailable — falling back to TVL-based perp ranking")
        top_perps = _fetch_perp_protocols_fallback()
        if top_perps:
            perp_source = "tvl"
            perp_volume = 0  # We don't have volume in this fallback

    result = {
        "spot_24h": spot_volume,
        "spot_change_1d": spot_change,
        "perp_24h": perp_volume,
        "combined_24h": spot_volume + perp_volume,
        "top_spot": top_spot,
        "top_perps": top_perps,
        "perp_source": perp_source,
    }

    # Coverage percentages — capped at 100% because DeFiLlama double-counts
    # aggregator routing (e.g., Jupiter routes through Raydium/Orca), so the
    # sum of individual DEX volumes can exceed the reported total.
    if spot_volume and top_spot:
        coverage = sum(d.get("volume_24h", 0) for d in top_spot)
        result["spot_coverage_pct"] = min(round(coverage / spot_volume * 100, 1), 100.0)
    if perp_volume and top_perps:
        coverage = sum(d.get("volume_24h", 0) for d in top_perps)
        result["perp_coverage_pct"] = min(round(coverage / perp_volume * 100, 1), 100.0)

    return result


def fetch_protocol_rankings() -> list:
    """DeFiLlama top Solana protocols by TVL.

    Excludes CEXes (Binance, OKX, etc.) — those represent exchange-held SOL,
    not on-chain DeFi activity, and they dominate rank-1 without adding signal
    to a Solana DeFi dashboard.
    """
    data = api_get("https://api.llama.fi/protocols")
    if not data:
        return []

    # Categories that aren't DeFi and should not appear in a DeFi ranking
    EXCLUDED_CATEGORIES = {"CEX", "Chain"}

    solana_protocols = []
    for p in data:
        chains = p.get("chains", [])
        if "Solana" not in chains:
            continue
        if p.get("category") in EXCLUDED_CATEGORIES:
            continue
        # Get Solana-specific TVL if available
        chain_tvls = p.get("chainTvls", {})
        sol_tvl_raw = chain_tvls.get("Solana", None)
        if isinstance(sol_tvl_raw, (int, float)):
            sol_tvl = sol_tvl_raw
        elif isinstance(sol_tvl_raw, dict):
            sol_tvl = sol_tvl_raw.get("tvl", 0)
            # tvl field itself may be a list of time-series entries
            if isinstance(sol_tvl, list) and sol_tvl:
                sol_tvl = sol_tvl[-1].get("totalLiquidityUSD", 0) if isinstance(sol_tvl[-1], dict) else 0
        else:
            sol_tvl = p.get("tvl", 0)

        solana_protocols.append({
            "name": p.get("name", "Unknown"),
            "category": p.get("category", "Unknown"),
            "tvl": sol_tvl if isinstance(sol_tvl, (int, float)) else p.get("tvl", 0),
            "change_1d": round(p.get("change_1d", 0) or 0, 1),
            "change_7d": round(p.get("change_7d", 0) or 0, 1),
        })

    solana_protocols.sort(key=lambda x: x["tvl"], reverse=True)
    return solana_protocols[:20]


def fetch_fees() -> dict:
    """DeFiLlama Solana fees."""
    data = api_get("https://api.llama.fi/overview/fees/solana?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyFees")
    if not data:
        return {"total_24h": 0}
    return {
        "total_24h": data.get("total24h", 0),
        "change_1d": round(data.get("change_1d", 0), 1),
    }


def fetch_network_stats() -> dict:
    """Solana RPC network statistics."""
    stats = {}

    # Recent performance (TPS)
    perf = rpc_post("getRecentPerformanceSamples", [5])
    if perf and isinstance(perf, list):
        total_txs = sum(s.get("numTransactions", 0) for s in perf)
        total_slots = sum(s.get("numSlots", 0) for s in perf)
        total_seconds = sum(s.get("samplePeriodSecs", 60) for s in perf)
        non_vote = sum(s.get("numNonVoteTransactions", 0) for s in perf)

        stats["tps_total"] = round(total_txs / total_seconds, 1) if total_seconds else 0
        stats["tps_non_vote"] = round(non_vote / total_seconds, 1) if total_seconds else 0
        stats["vote_pct"] = round((1 - non_vote / total_txs) * 100, 1) if total_txs else 0

    # Estimated daily transactions
    if stats.get("tps_total"):
        stats["daily_transactions_est"] = round(stats["tps_total"] * 86400)

    # Epoch info
    epoch = rpc_post("getEpochInfo")
    if epoch:
        stats["epoch"] = epoch.get("epoch")
        stats["slot_index"] = epoch.get("slotIndex")
        stats["slots_in_epoch"] = epoch.get("slotsInEpoch")

    return stats


def fetch_stablecoin_data() -> dict:
    """DeFiLlama stablecoins on Solana."""
    data = api_get("https://stablecoins.llama.fi/stablecoins?includePrices=false")
    if not data or "peggedAssets" not in data:
        return {"total": 0, "breakdown": []}

    solana_stables = []
    total = 0
    for asset in data["peggedAssets"]:
        chains = asset.get("chainCirculating", {})
        if "Solana" in chains:
            sol_data = chains["Solana"]
            # peggedUSD is the key for USD-pegged stablecoins
            amount = 0
            for peg_type in sol_data.values():
                if isinstance(peg_type, (int, float)):
                    amount += peg_type
                elif isinstance(peg_type, dict):
                    amount += sum(v for v in peg_type.values() if isinstance(v, (int, float)))

            if amount > 1_000_000:  # Only show > $1M
                solana_stables.append({
                    "name": asset.get("name", "Unknown"),
                    "symbol": asset.get("symbol", "?"),
                    "amount": amount,
                })
                total += amount

    solana_stables.sort(key=lambda x: x["amount"], reverse=True)
    return {
        "total": total,
        "breakdown": solana_stables[:10],
    }


def fetch_defi_yields() -> dict:
    """DeFiLlama top Solana DeFi yields by TVL."""
    data = api_get("https://yields.llama.fi/pools")
    if not data or "data" not in data:
        return {"top_yields": [], "top_stablecoin_yields": [], "summary": {}}

    pools = data["data"]

    # Filter for Solana pools with >= $1M TVL
    solana_pools = []
    for p in pools:
        if p.get("chain") != "Solana":
            continue
        tvl = p.get("tvlUsd") or 0
        if tvl < 1_000_000:
            continue
        solana_pools.append({
            "pool": p.get("pool"),
            "project": p.get("project"),
            "symbol": p.get("symbol"),
            "tvlUsd": tvl,
            "apy": p.get("apy") or 0,
            "apyBase": p.get("apyBase") or 0,
            "apyReward": p.get("apyReward") or 0,
            "stablecoin": bool(p.get("stablecoin")),
        })

    # Sort by TVL descending
    solana_pools.sort(key=lambda x: x["tvlUsd"], reverse=True)

    top_yields = solana_pools[:20]

    # Top stablecoin pools by APY
    stable_pools = [p for p in solana_pools if p["stablecoin"]]
    stable_pools.sort(key=lambda x: x["apy"], reverse=True)
    top_stablecoin_yields = stable_pools[:10]

    total_tvl = sum(p["tvlUsd"] for p in solana_pools)
    apys = [p["apy"] for p in solana_pools]
    avg_apy = round(sum(apys) / len(apys), 2) if apys else 0
    best_stable_apy = top_stablecoin_yields[0]["apy"] if top_stablecoin_yields else 0

    return {
        "top_yields": top_yields,
        "top_stablecoin_yields": top_stablecoin_yields,
        "summary": {
            "total_pools": len(solana_pools),
            "total_tvl": total_tvl,
            "avg_apy": avg_apy,
            "best_stable_apy": best_stable_apy,
        },
    }


def fetch_sector_breakdown() -> dict:
    """Break down Solana protocols by category/sector for sector rotation analysis."""
    data = api_get("https://api.llama.fi/protocols")
    if not data or not isinstance(data, list):
        return {"sectors": [], "depin": []}

    # Same exclusion list as fetch_protocol_rankings — CEXes represent
    # exchange-held SOL, not on-chain DeFi activity, so they don't belong
    # in a Solana DeFi sector rotation view.
    EXCLUDED_CATEGORIES = {"CEX", "Chain"}

    sectors = {}  # category -> {tvl, count, top_protocol, change_1d}
    depin_protocols = []

    for p in data:
        chains = p.get("chains", [])
        if "Solana" not in chains:
            continue
        if p.get("category") in EXCLUDED_CATEGORIES:
            continue

        # Get Solana-specific TVL
        chain_tvls = p.get("chainTvls", {})
        sol_tvl = chain_tvls.get("Solana", 0)
        if sol_tvl == 0:
            if chains == ["Solana"]:
                sol_tvl = p.get("tvl", 0)
            else:
                continue
        if sol_tvl < 100000:  # skip tiny protocols
            continue

        category = p.get("category", "Other")
        change_1d = p.get("change_1d") or 0
        change_7d = p.get("change_7d") or 0
        name = p.get("name", "Unknown")

        if category not in sectors:
            sectors[category] = {"tvl": 0, "count": 0, "protocols": [], "change_1d_weighted": 0}
        sectors[category]["tvl"] += sol_tvl
        sectors[category]["count"] += 1
        sectors[category]["protocols"].append({"name": name, "tvl": sol_tvl, "change_1d": change_1d})
        sectors[category]["change_1d_weighted"] += sol_tvl * change_1d

        # DePIN / infrastructure protocols
        depin_keywords = ["depin", "helium", "render", "hivemapper", "geodnet", "io.net",
                          "nosana", "shadow", "grass", "teleport", "srcful", "natix"]
        if any(kw in name.lower() for kw in depin_keywords) or category in ("Infrastructure", "Services"):
            depin_protocols.append({
                "name": name, "category": category, "tvl": sol_tvl,
                "change_1d": round(change_1d, 1), "change_7d": round(change_7d, 1),
            })

    # Build sorted sector list
    sector_list = []
    for cat, data_dict in sectors.items():
        tvl = data_dict["tvl"]
        avg_change = (data_dict["change_1d_weighted"] / tvl) if tvl else 0
        top = sorted(data_dict["protocols"], key=lambda x: x["tvl"], reverse=True)[0]
        sector_list.append({
            "sector": cat,
            "tvl": tvl,
            "protocol_count": data_dict["count"],
            "change_1d": round(avg_change, 1),
            "top_protocol": top["name"],
        })
    sector_list.sort(key=lambda x: x["tvl"], reverse=True)

    depin_protocols.sort(key=lambda x: x["tvl"], reverse=True)

    return {
        "sectors": sector_list[:15],
        "depin": depin_protocols[:10],
    }


def fetch_tx_economics() -> dict:
    """Solana transaction fee economics from RPC priority fee data."""
    # Query with popular writable account keys to get real priority fees
    # (global query without accounts returns 0 for all slots)
    popular_accounts = [
        "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",   # Jupiter v6
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",   # Orca Whirlpool
    ]
    samples = rpc_post("getRecentPrioritizationFees", [popular_accounts])
    if not samples or not isinstance(samples, list):
        return {
            "base_fee_lamports": 5000,
            "base_fee_sol": 0.000005,
            "priority_fees": {
                "median": 0,
                "p75": 0,
                "p90": 0,
                "min": 0,
                "max": 0,
                "sample_count": 0,
            },
        }

    fees = [s.get("prioritizationFee", 0) for s in samples]
    fees.sort()

    median_value = int(statistics.median(fees))
    p75_value = int(statistics.quantiles(fees, n=100)[74]) if len(fees) >= 2 else fees[-1]
    p90_value = int(statistics.quantiles(fees, n=100)[89]) if len(fees) >= 2 else fees[-1]
    min_value = fees[0]
    max_value = fees[-1]

    return {
        "base_fee_lamports": 5000,
        "base_fee_sol": 0.000005,
        "priority_fees": {
            "median": median_value,
            "p75": p75_value,
            "p90": p90_value,
            "min": min_value,
            "max": max_value,
            "sample_count": len(samples),
        },
    }


def run() -> dict:
    log.info("Fetching Solana ecosystem data...")

    chain_tvls = fetch_chain_tvls()
    log.info(f"  Chain TVLs: {len(chain_tvls)} chains")

    solana_tvl = fetch_solana_tvl()
    log.info(f"  Solana TVL: ${solana_tvl['current']/1e9:.2f}B")

    dex_volumes = fetch_dex_volumes()
    log.info(f"  DEX Volume: ${dex_volumes['combined_24h']/1e9:.2f}B combined")

    protocols = fetch_protocol_rankings()
    log.info(f"  Protocols: {len(protocols)} ranked")

    fees = fetch_fees()
    log.info(f"  Fees: ${fees['total_24h']/1e6:.1f}M")

    network = fetch_network_stats()
    log.info(f"  Network: {network.get('tps_total', 'N/A')} TPS")

    stablecoins = fetch_stablecoin_data()
    log.info(f"  Stablecoins: ${stablecoins['total']/1e9:.2f}B")

    sectors = fetch_sector_breakdown()
    log.info(f"  Sectors: {len(sectors.get('sectors', []))} categories, {len(sectors.get('depin', []))} DePIN protocols")

    defi_yields = fetch_defi_yields()
    log.info(f"  DeFi Yields: {defi_yields.get('summary', {}).get('total_pools', 0)} pools")

    tx_economics = fetch_tx_economics()
    log.info(f"  Tx Economics: {tx_economics['priority_fees']['sample_count']} fee samples")

    result = {
        "timestamp": now_utc(),
        "chain_tvls": chain_tvls,
        "solana_tvl": solana_tvl,
        "dex_volumes": dex_volumes,
        "protocol_rankings": protocols,
        "fees": fees,
        "network": network,
        "stablecoins": stablecoins,
        "sectors": sectors,
        "defi_yields": defi_yields,
        "tx_economics": tx_economics,
    }

    save_json(result, "solana.json")
    log.info("Solana data saved.")
    return result


if __name__ == "__main__":
    run()
