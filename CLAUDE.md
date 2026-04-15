# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

```bash
conda env create -f environment.yml
conda activate prosperity3
```

The `prosperity4btest` package (installed via pip in the env) is the backtesting engine. Backtest logs are written to `backtests/` as timestamped `.log` files. Clear them with:

```bash
python utils.py              # clears backtests/ by default
python utils.py <other_dir>  # clears a custom directory
```

## Architecture

This is an [IMC Prosperity](https://imc-prosperity.com) trading competition codebase. The exchange calls `Trader.run()` once per timestamp and expects orders back.

**Data flow:**

1. Exchange constructs a `TradingState` (defined in `datamodel.py`) containing the current order book, recent trades, current positions, and observations.
2. `Trader.run(state)` processes this and returns a 3-tuple: `(orders, conversions, traderData)`
   - `orders`: `Dict[Symbol, List[Order]]` — orders to submit
   - `conversions`: `int` — conversion requests (product arbitrage between markets)
   - `traderData`: `str` — arbitrary string persisted across timestamps (use for strategy state)

**Key types in `datamodel.py`:**
- `OrderDepth.buy_orders`: `Dict[price, quantity]` — positive quantities
- `OrderDepth.sell_orders`: `Dict[price, quantity]` — **negative** quantities
- `Order(symbol, price, quantity)`: positive quantity = buy, negative = sell
- `TradingState.position`: `Dict[Product, int]` — net position; enforce limits manually
- `ConversionObservation`: external market data (bid/ask, transport fees, tariffs, sunlight index, sugar price) for conversion-eligible products

**Import convention:** Trader files submitted to the competition must use local imports (`from datamodel import ...`), not package-style imports (`from Prosperity3.datamodel import ...`). See `emeralds_based_trader.py` vs `example_trader.py`.

## Local testing

Use `example_trading_state.py` as a template to construct a `TradingState` manually and call your trader:

```python
from example_trading_state import state
from emeralds_based_trader import Trader

result, conversions, traderData = Trader().run(state)
print(result)
```

## Market data

Historical price and trade data lives in `prices/` and `trades/` as semicolon-delimited CSVs. The `price_visualizer.ipynb` notebook loads these and plots mid-prices per product. The notebook previously expected data under `./csv/prices/` — the actual path is `./prices/`.
