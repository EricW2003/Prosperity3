from datamodel import TradingState, Order
from typing import List
import json
import math
import os


class Trader:
    """
    Drift-aware market maker for INTARIAN_PEPPER_ROOT.

    The product drifts up ~0.1 per tick consistently. Strategy:
      1. Wall-mid: robust fair value from largest liquidity levels
      2. Fair value = wall_mid + ALPHA (upward bias for positive drift)
      3. Market taking: buy asks below fair, sell bids above fair
      4. Market making: passive quotes at fair Â± HALF_SPREAD, skewed by inventory
      5. Inventory skew: position * SKEW_FACTOR pushes quotes down when long
    """

    def run(self, state: TradingState):
        product = "INTARIAN_PEPPER_ROOT"
        limit = 80

        # Tunable parameters (env vars for sweep_param.py)
        ALPHA = float(os.environ.get("ALPHA", "3.25"))
        HALF_SPREAD = float(os.environ.get("HALF_SPREAD", "7"))
        SKEW_FACTOR = float(os.environ.get("SKEW_FACTOR", "0.03"))

        # Restore mids history from traderData
        mids = json.loads(state.traderData) if state.traderData else []

        position = state.position.get(product, 0)
        order_depth = state.order_depths.get(product)
        if not order_depth:
            return {}, 0, json.dumps(mids)

        orders: List[Order] = []

        # â”€â”€ Wall-mid: midpoint of best wall levels on each side â”€â”€
        wall_threshold = 5
        bid_walls = [p for p, v in order_depth.buy_orders.items()
                     if v >= wall_threshold]
        ask_walls = [p for p, v in order_depth.sell_orders.items()
                     if -v >= wall_threshold]

        if bid_walls and ask_walls:
            wall_mid = (max(bid_walls) + min(ask_walls)) / 2
            mids.append(wall_mid)
            if len(mids) > 100:
                mids.pop(0)
        elif mids:
            wall_mid = mids[-1]
        else:
            return {product: []}, 0, json.dumps(mids)

        # â”€â”€ Fair value: wall_mid shifted up for positive drift â”€â”€
        fair_value = wall_mid + ALPHA

        # â”€â”€ Market taking: grab mispriced orders â”€â”€
        for ask_price in sorted(order_depth.sell_orders.keys()):
            if ask_price < fair_value and position < limit:
                ask_qty = -order_depth.sell_orders[ask_price]
                buy_qty = min(ask_qty, limit - position)
                if buy_qty > 0:
                    orders.append(Order(product, ask_price, buy_qty))
                    position += buy_qty
            else:
                break

        for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
            if bid_price > fair_value and position > -limit:
                bid_qty = order_depth.buy_orders[bid_price]
                sell_qty = min(bid_qty, limit + position)
                if sell_qty > 0:
                    orders.append(Order(product, bid_price, -sell_qty))
                    position -= sell_qty
            else:
                break

        # â”€â”€ Inventory skew: positive position â†’ lower both prices â”€â”€
        skew = position * SKEW_FACTOR

        # â”€â”€ Market making: passive quotes around fair value â”€â”€
        buy_price = math.floor(fair_value - HALF_SPREAD - skew)
        sell_price = math.ceil(fair_value + HALF_SPREAD - skew)

        buy_volume = limit - position
        sell_volume = limit + position

        if buy_volume > 0:
            orders.append(Order(product, buy_price, buy_volume))
        if sell_volume > 0:
            orders.append(Order(product, sell_price, -sell_volume))

        return {product: orders}, 0, ""