# Solana Floor Daily Dashboard

Automated daily intelligence dashboard for Solana Floor content production.

## Architecture

```
GitHub Actions (cron: 6am PT daily)
  ├── fetch_market.py      → CoinGecko, Alt.me F&G, market overview
  ├── fetch_solana.py       → DeFiLlama, Solana RPC, TVL, DEX volume
  ├── fetch_news.py         → CryptoPanic API + RSS feeds
  ├── fetch_whales.py       → On-chain whale tracking
  ├── compile_data.py       → Merge all JSON into single payload
  ├── generate_signal.py    → Claude API → The Signal + story pitches
  ├── generate_dashboard.py → HTML dashboard from compiled data
  └── deploy.py             → Push to GitHub Pages + Telegram
```

## Data Sources (Free / Low-Cost)

| Source | Data | Cost |
|--------|------|------|
| CoinGecko API | Prices, market cap, dominance, trending | Free (30 calls/min) |
| Alternative.me | Fear & Greed Index | Free |
| DeFiLlama | TVL, DEX volumes, protocol rankings | Free |
| Solana RPC | TPS, transactions, active addresses | Free (public RPC) |
| CryptoPanic | Crypto news aggregation | Free (basic) / $49/mo (pro) |
| Claude API | Narrative generation, story pitches | ~$0.10-0.30/run |

## Setup

1. Clone this repo
2. Copy `.env.example` to `.env` and fill in API keys
3. Set GitHub Secrets for Actions deployment

### Required Secrets
- `ANTHROPIC_API_KEY` — Claude API for narrative generation
- `CRYPTOPANIC_API_KEY` — News aggregation
- `TELEGRAM_BOT_TOKEN` — Dashboard delivery (optional)
- `TELEGRAM_CHAT_ID` — Your chat ID (optional)

## Local Development

```bash
pip install -r requirements.txt
python scripts/run_pipeline.py
```

## Cost Estimate

- GitHub Actions: Free (2,000 min/mo on private repos)
- Claude API: ~$3-9/month (one Sonnet call/day)
- CryptoPanic: Free tier or $49/mo for pro
- **Total: $3-58/month vs. current Perplexity burn**
