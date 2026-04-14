from datamodel import OrderDepth, TradingState, Order
from typing import List

class Trader:

    def run(self, state: TradingState):

        result = {}

        for product in ["EMERALDS"]:

            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            position = state.position.get(product, 0)
            limit = 20

            best_bid = max(order_depth.buy_orders.keys())
            best_ask = min(order_depth.sell_orders.keys())

            buy_price = best_bid
            sell_price = best_ask

            buy_volume = limit - position
            sell_volume = limit + position

            if buy_volume > 0:
                orders.append(Order(product, buy_price, buy_volume))

            if sell_volume > 0:
                orders.append(Order(product, sell_price, -sell_volume))

            result[product] = orders
        

        return result, 0, ""