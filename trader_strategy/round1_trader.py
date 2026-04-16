from datamodel import OrderDepth, TradingState, Order
from typing import List
import numpy as np


class Trader:

    def __init__(self):
        self.price_history = {"INTARIAN_PEPPER_ROOT": []}

    def run(self, state: TradingState):

        result = {}

        result["ASH_COATED_OSMIUM"] = self.trade_osmium(state)
        result["INTARIAN_PEPPER_ROOT"] = self.trade_pepper(state)

        return result, 0, ""

    # ---------------- EMERALDS ----------------

    def trade_osmium(self, state):

        product = "ASH_COATED_OSMIUM"
        orders = []

        order_depth = state.order_depths[product]

        position = state.position.get(product, 0)
        limit = 80

        best_bid, best_ask = self.get_best_bid_ask(order_depth)

        buy_price = best_bid + 1
        sell_price = best_ask - 1

        buy_volume = limit - position
        sell_volume = limit + position

        if buy_volume > 0:
            orders.append(Order(product, buy_price, buy_volume))

        if sell_volume > 0:
            orders.append(Order(product, sell_price, -sell_volume))

        return orders

    # ---------------- TOMATOES ----------------

    def trade_pepper(self, state):

        product = "INTARIAN_PEPPER_ROOT"
        orders = []

        order_depth = state.order_depths[product]

        position = state.position.get(product, 0)
        limit = 20

        mid = self.get_mid_price(order_depth)

        self.update_price_history(product, mid)

        drift = self.compute_drift(product, mid)

        fair = mid + 0.5 * drift

        levels = [(4, 10), (5, 5)]

        for offset, volume in levels:

            bid_price = int(fair - offset)
            ask_price = int(fair + offset)

            if position < limit:
                buy_volume = min(volume, limit - position)
                orders.append(Order(product, bid_price, buy_volume))

            if position > -limit:
                sell_volume = min(volume, limit + position)
                orders.append(Order(product, ask_price, -sell_volume))

        return orders

    # ---------------- UTILITIES ----------------

    def get_best_bid_ask(self, order_depth):

        best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders.keys() is not None else None
        best_ask = min(order_depth.sell_orders.keys()) if order_depth.buy_orders.keys() is not None else None

        return best_bid, best_ask
    
    def get_mid_price(self, order_depth):

        best_bid, best_ask = self.get_best_bid_ask(order_depth)

        return (best_bid + best_ask) / 2
    
    def get_spread(self, order_depth):

        best_bid, best_ask = self.get_best_bid_ask(order_depth)

        return best_ask - best_bid

    def update_price_history(self, product, price):

        self.price_history[product].append(price)

        if len(self.price_history[product]) > 20:
            self.price_history[product].pop(0)

    def compute_drift(self, product, mid):

        history = self.price_history[product]

        if len(history) > 5:
            return mid - np.mean(history[-5:])

        return 0

    def detect_wall(self, order_dict):

        for price, volume in order_dict.items():

            if abs(volume) > 15:
                return price

        return None
    
    def moving_average(self, product, window):

        history = self.price_history[product]

        if len(history) < window:
            return history[-1]

        return np.mean(history[-window:])
    
    def compute_volatility(self, product):

        history = self.price_history[product]

        if len(history) < 5:
            return 0

        return np.std(history[-10:])
    
    def place_order(self, orders, product, price, volume):

        orders.append(Order(product, int(price), int(volume)))

    def place_market_making(self, orders, product, fair, offset, volume):

        orders.append(Order(product, fair - offset, volume))
        orders.append(Order(product, fair + offset, -volume))
    
    def inventory_skew(self, position, limit):

        return position / limit


