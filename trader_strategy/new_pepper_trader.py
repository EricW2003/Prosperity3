from datamodel import OrderDepth, TradingState, Order
from typing import List
import math


class Trader:
    def __init__(self):
        self.mid = []

    def run(self, state: TradingState):

        product = "INTARIAN_PEPPER_ROOT"
        limit = 80
        orders: List[Order] = []

        order_depth: OrderDepth = state.order_depths[product]
        position = state.position.get(product, 0)

        best_bid = max(order_depth.buy_orders) if order_depth.buy_orders else None
        best_ask = min(order_depth.sell_orders) if order_depth.sell_orders else None

        # ----------------------------
        # 1. MID PRICE
        # ----------------------------
        if best_bid is None or best_ask is None:
            return {}, 0, ""

        mid = (best_bid + best_ask) / 2
        self.mid.append(mid)

        if len(self.mid) > 50:
            self.mid.pop(0)

        # ----------------------------
        # 2. DRIFT (simple trend)
        # ----------------------------
        if len(self.mid) >= 20:
            drift = (self.mid[-1] - self.mid[-20]) / 20
        else:
            drift = 0

        # ----------------------------
        # 3. FAIR PRICE
        # ----------------------------
        fair = mid + 80 * drift

        # ----------------------------
        # 4. INVENTORY SKEW
        # ----------------------------
        skew = position / 40  # simple linear risk control

        # ----------------------------
        # 5. QUOTES (market making)
        # ----------------------------
        spread = 7  # half of ~10 spread

        bid_price = math.floor(fair - spread - skew)
        ask_price = math.ceil(fair + spread - skew)

        # ----------------------------
        # 6. CAP TO AVOID CROSSING BADLY
        # ----------------------------
        if best_bid is not None:
            bid_price = min(bid_price, best_bid + 1)

        if best_ask is not None:
            ask_price = max(ask_price, best_ask - 1)

        # ----------------------------
        # 7. ORDER SIZE
        # ----------------------------
        buy_qty = limit - position
        sell_qty = limit + position

        if buy_qty > 0:
            orders.append(Order(product, bid_price, buy_qty))

        if sell_qty > 0:
            orders.append(Order(product, ask_price, -sell_qty))

        return {product: orders}, 0, ""