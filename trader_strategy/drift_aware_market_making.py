from datamodel import OrderDepth, TradingState, Order
from typing import List
import numpy as np

class Trader:

    def __init__(self):
        self.price_history = []

    def run(self, state: TradingState):

        result = {}

        product = "TOMATOES"
        order_depth: OrderDepth = state.order_depths[product]
        orders: List[Order] = []

        position = state.position.get(product, 0)
        limit = 80

        best_bid = max(order_depth.buy_orders.keys())
        best_ask = min(order_depth.sell_orders.keys())

        mid = (best_bid + best_ask) / 2

        self.price_history.append(mid)
        if len(self.price_history) > 20:
            self.price_history.pop(0)

        drift = 0
        if len(self.price_history) > 5:
            drift = mid - np.mean(self.price_history[-5:])

        inventory_penalty = position * 0.2

        fair = mid + 0.7 * drift - inventory_penalty

        spread = 1

        levels = [(1, 10), (2, 5)]

        for offset, volume in levels:

            bid_price = int(fair - offset)
            ask_price = int(fair + offset)

            if position < limit:
                buy_volume = min(volume, limit - position)
                orders.append(Order(product, bid_price, buy_volume))

            if position > -limit:
                sell_volume = min(volume, limit + position)
                orders.append(Order(product, ask_price, -sell_volume))

        result[product] = orders

        return result, 0, ""