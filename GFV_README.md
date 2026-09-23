# GFV Donchian Ensemble on freqtrade (F1 Finance Corp)

Status: **backtested, NOT live.** Next step is dry-run (paper trading). Not financial advice.

## Files
- `user_data/strategies/GFVDonchianEnsemble.py`: the strategy
- `user_data/config_gfv_donchian.json`: dry-run config (Kraken spot, BTC/USD, $1,000 paper wallet, no API keys)

## Strategy
- Daily BTC trend-following, long-only
- 4 Donchian breakout models (20/55/100/200 days): buy on a close above the N-day high, sell on a close below the N/2-day low
- Position size = average of the 4 models × volatility scaler (40% annual vol target, never above 100% of the wallet)
- Rebalances only when the target moves more than 10% of the wallet
- No leverage, no stoploss (the Donchian channel is the exit)
- Rules were chosen before testing. They were NOT optimized on this data.

## Backtest (freqtrade, BTC/USD daily, Feb 2016 – Sep 2026, $1,000 start)
- 0.1%/side fees: $22,877 final, CAGR 34.3%, max drawdown 30.6%, Sharpe 1.36
- 0.4%/side fees (Kraken low-volume taker): $19,997, CAGR 32.6%, max drawdown 31.7%, Sharpe 1.31
- Buy & hold, same period: CAGR ~64%, max drawdown ~84%, Sharpe ~1.07
- 2016–2020: CAGR 59.9%, Sharpe 1.90
- 2021 – mid-2023 (includes the 2022 bear): CAGR 0.7% (flat), max drawdown 32%
- Mid-2023 – Sep 2026: CAGR 22.1% vs BTC +182% total, max drawdown 17.9%
- 19 trades, p-value 0.11 → NOT statistically significant yet
- An independent Python re-implementation agreed (CAGR 37.7%, DD 36%, Sharpe 1.32)

## What it is / isn't
- The edge is smaller drawdowns, not beating buy & hold. It lags badly in strong bull runs.
- **Shorts tested negative in every period** (-8% CAGR on the short side alone), so `ENABLE_SHORT = False`.
- Data: Coinbase BTC-USD daily candles. One bad tick was fixed: 2017-04-15 low of $0.06 was replaced with min(open, close).

## Run it (Python 3.11+, or use Docker)
```
pip install -e .
# put BTC/USD 1d data in user_data/data/kraken/  (or: freqtrade download-data -c user_data/config_gfv_donchian.json --timerange 20150101- -t 1d)
freqtrade backtesting -c user_data/config_gfv_donchian.json --timerange 20160101- --fee 0.004 --breakdown year
freqtrade trade -c user_data/config_gfv_donchian.json      # dry-run (paper) — dry_run is true
```

## Before any live money
1. Dry-run for at least 60-90 days. It trades rarely (~2 trades/yr), so the main check is that signals and sizing match the backtest.
2. Pick the live venue:
   - Kraken: freqtrade supports it natively and it's open to US users (verify eligibility for your entity)
   - Or use freqtrade signals to place trades via the Robinhood MCP
3. Add Telegram alerts and set API-server secrets (32+ characters) before exposing FreqUI.
4. Never commit exchange API keys to git.
