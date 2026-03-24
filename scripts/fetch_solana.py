"""Fetch Solana ecosystem data: TVL, DEX volumes, protocol rankings, network stats."""

import statistics

from config import (
    api_get, rpc_post, save_json, get_logger, now_utc,
    TOP_CHAINS,
)

log = get_logger("fetch_solana")


def fetch_chain_tvls() -> list:
    """DeFiLlama TVL by chain — returns top chains."""
    data = api_get("https://api.llama.fi/v2/chains")
    if not data:
        return []

    chains = []
    for chain in data:
        name = chain.get("name", "")
        tvl = chain.get("tvl", 0)
        chains.append({
            "name": name,
            "tvl": tvl,
            "change_1d": chain.get("change_1d"),
            "change_7d": chain.get("change_7d"),
        })

    # Sort by TVL, return top 10 + ensure Solana is included
    chains.sort(key=lambda x: x["tvl"], reverse=True)
    top = chains[:10]
    solana_in_top = any(c["name"] == "Solana" for c in top)
    if not solana_in_top:
        sol = next((c for c in chains if c["name"] == "Solana"), None)
        if sol:
            top.append(sol)
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


def fetch_dex_volumes() -> dict:
    """DeFiLlama DEX volumes — Solana spot + perp."""
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

    if perp_data:
        perp_volume = perp_data.get("total24h", 0)
        protocols = perp_data.get("protocols", [])
        protocols.sort(key=lambda x: x.get("total24h") or 0, reverse=True)
        for p in protocols[:10]:
            top_perps.append({
                "name": p.get("name", "Unknown"),
                "volume_24h": p.get("total24h") or 0,
                "change_1d": round(p.get("change_1d") or 0, 1),
            })

    result = {
        "spot_24h": spot_volume,
        "spot_change_1d": spot_change,
        "perp_24h": perp_volume,
        "combined_24h": spot_volume + perp_volume,
        "top_spot": top_spot,
        "top_perps": top_perps,
    }

    # Coverage percentages
    if spot_volume and top_spot:
        coverage = sum(d.get("volume_24h", 0) for d in top_spot)
        result["spot_coverage_pct"] = round(coverage / spot_volume * 100, 1) if spot_volume else 0
    if perp_volume and top_perps:
        coverage = sum(d.get("volume_24h", 0) for d in top_perps)
        result["perp_coverage_pct"] = round(coverage / perp_volume * 100, 1) if perp_volume else 0

    return result


def fetch_protocol_rankings() -> list:
    """DeFiLlama top Solana protocols by TVL."""
    data = api_get("https://api.llama.fi/protocols")
    if not data:
        return []

    solana_protocols = []
    for p in data:
        chains = p.get("chains", [])
        if "Solana" not in chains:
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


def fetch_tx_economics() -> dict:
    """Solana transaction fee economics from RPC priority fee data."""
    samples = rpc_post("getRecentPrioritizationFees")
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
        "defi_yields": defi_yields,
        "tx_economics": tx_economics,
    }

    save_json(result, "solana.json")
    log.info("Solana data saved.")
    return result


if __name__ == "__main__":
    run()
