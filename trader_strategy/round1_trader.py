from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import json
import math

class Trader:

    def bid(self):
        return 15
    
    def run(self, state: TradingState):

        result = {}
        orders: List[Order] = []

        product="ASH_COATED_OSMIUM"
        limit = 80
        delta = 20

        position = state.position.get(product, 0)

        order_depth: OrderDepth = state.order_depths[product]
        if not order_depth:
            return {}, 0, ""

        # Dynamic fair: midpoint of largest-volume ("wall") levels on each side
        wall_threshold = 5
        bid_walls = [p for p, v in order_depth.buy_orders.items() if v >= wall_threshold]
        ask_walls = [p for p, v in order_depth.sell_orders.items() if -v >= wall_threshold]
        if bid_walls and ask_walls:
            fair_value = (max(bid_walls) + min(ask_walls)) / 2
        else:
            fair_value = 10000

        #Market taking

        # Buy everything below fair value
        for ask_price, ask_qty in sorted(order_depth.sell_orders.items()):  # ascending
            if ask_price < fair_value:
                buy_qty = min(-ask_qty, limit - position)  # ask_qty is negative
                if buy_qty > 0:
                    orders.append(Order(product, ask_price, buy_qty))
                    position += buy_qty
            else:
                break  # asks are sorted ascending, no point continuing

        # Sell everything above fair value
        for bid_price, bid_qty in sorted(order_depth.buy_orders.items(), reverse=True):  # descending
            if bid_price > fair_value:
                sell_qty = min(bid_qty, limit + position)  # bid_qty is positive
                if sell_qty > 0:
                    orders.append(Order(product, bid_price, -sell_qty))
                    position -= sell_qty
            else:
                break

        #Market making — always join the inside of the book, skewed by inventory
        best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
        best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

        skew = int(round(position / 9))  # ±2 at full position (±80)

        bid_cap = math.floor(fair_value) - 1 - skew   # never quote >= floor(fair)
        ask_cap = math.ceil(fair_value) + 1 - skew    # never quote <= ceil(fair)

        if best_bid is not None:
            buy_price = min(best_bid + 1, bid_cap)
        else:
            buy_price = int(fair_value - delta) - skew

        if best_ask is not None:
            sell_price = max(best_ask - 1, ask_cap)
        else:
            sell_price = int(fair_value + delta) - skew

        buy_volume = limit - position
        sell_volume = limit + position

        if buy_volume > 0:
            orders.append(Order(product, buy_price, buy_volume))

        if sell_volume > 0:
            orders.append(Order(product, sell_price, -sell_volume))

        result[product] = orders
        
        product = "INTARIAN_PEPPER_ROOT"
        limit = 80

        # Tunable parameters (env vars for sweep_param.py)
        ALPHA = 3.25
        HALF_SPREAD = 7
        SKEW_FACTOR = 0.03

        # Restore mids history from traderData
        mids = json.loads(state.traderData) if state.traderData else []

        position = state.position.get(product, 0)
        order_depth = state.order_depths.get(product)
        if not order_depth:
            return {}, 0, json.dumps(mids)

        orders: List[Order] = []

        # ── Wall-mid: midpoint of best wall levels on each side ──
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

        # ── Fair value: wall_mid shifted up for positive drift ──
        fair_value = wall_mid + ALPHA

        # ── Market taking: grab mispriced orders ──
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

        # ── Inventory skew: positive position → lower both prices ──
        skew = position * SKEW_FACTOR

        # ── Market making: passive quotes around fair value ──
        buy_price = math.floor(fair_value - HALF_SPREAD - skew)
        sell_price = math.ceil(fair_value + HALF_SPREAD - skew)

        buy_volume = limit - position
        sell_volume = limit + position

        if buy_volume > 0:
            orders.append(Order(product, buy_price, buy_volume))
        if sell_volume > 0:
            orders.append(Order(product, sell_price, -sell_volume))

        result[product] = orders

        return result, 0, json.dumps(mids)
