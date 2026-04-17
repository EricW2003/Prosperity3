from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import json
import math

class Trader:
    def __init__(self):
        self.mid=[]
        self.spread=[]
    def bid(self):
        return 15
    
    def run(self, state: TradingState):

        result = {}
        orders: List[Order] = []

        product="ASH_COATED_OSMIUM"
        limit = 80 
        delta = 20 # TO CHOOSE WISELY

        position = state.position.get(product, 0)

        order_depth: OrderDepth = state.order_depths[product]
        if not order_depth:
            return {}, 0, ""

        # Dynamic fair: midpoint of largest-volume ("wall") levels on each side
        wall_threshold = 5
        bid_walls = [p for p, v in order_depth.buy_orders.items() if v >= wall_threshold]
        ask_walls = [p for p, v in order_depth.sell_orders.items() if -v >= wall_threshold]
        if bid_walls and ask_walls:
            mid = (max(bid_walls) + min(ask_walls)) / 2
            self.mid.append(mid)

            spread = max(1, min(ask_walls) - max(bid_walls))
            self.spread.append(spread)
            if len(self.mid)>50:
                self.mid.pop(0)
            if len(self.spread)>2:
                self.spread.pop(0)
        elif self.mid!=[] and self.spread!=[]:
            mid = self.mid[-1]
            spread = self.spread[-1]
        else:
            return {}, 0, ""
        #Market taking

        # Taking

        # # Buy everything below fair value
        # for ask_price, ask_qty in sorted(order_depth.sell_orders.items()):  # ascending
        #     if ask_price < fair_value:
        #         buy_qty = min(-ask_qty, limit - position)  # ask_qty is negative
        #         if buy_qty > 0:
        #             orders.append(Order(product, ask_price, buy_qty))
        #             position += buy_qty
        #     else:
        #         break  # asks are sorted ascending, no point continuing

        # # Sell everything above fair value
        # for bid_price, bid_qty in sorted(order_depth.buy_orders.items(), reverse=True):  # descending
        #     if bid_price > fair_value:
        #         sell_qty = min(bid_qty, limit + position)  # bid_qty is positive
        #         if sell_qty > 0:
        #             orders.append(Order(product, bid_price, -sell_qty))
        #             position -= sell_qty
        #     else:
        #         break

        if len(self.mid)>=50:
            drift = self.mid[-1] - self.mid[-50] # to choose wisely
        else:
            drift = 5
        fair_value = mid + 4* drift #TO CHOOSE WISELY
        #Market making — always join the inside of the book, skewed by inventory
        best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
        best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

        target_inventory = 40 #TO CHOOSE WISELY
        # target_inventory = max(-40, min(40, target_inventory))      
        skew_offsetted = int(round((position - target_inventory) / 9))  # ±2 at full position (±80)

        directional_skew = int(drift * 7)
        skew_offsetted += directional_skew

        bid_cap = math.floor(fair_value) - 1 - skew_offsetted   # never quote >= floor(fair)
        ask_cap = math.ceil(fair_value) + 1 - skew_offsetted    # never quote <= ceil(fair)

        if best_bid is not None:
            buy_price = min(bid_cap - spread//2, best_bid + 2)
        else:
            buy_price = int(fair_value - delta) - skew_offsetted

        if best_ask is not None:
            sell_price = max(ask_cap + spread//2, best_ask - 2)        
        else:
            sell_price = int(fair_value + 1.5*delta) - skew_offsetted #TO CHOOSE WISELY

    
        buy_volume = min(limit - position, 30)
        sell_volume = min(limit + position, 30)

        if buy_volume > 0:
            orders.append(Order(product, buy_price, buy_volume))

        if sell_volume > 0:
            orders.append(Order(product, sell_price, -sell_volume))

        result[product] = orders

        return result, 0, ""
    
# fair = mid + 0.5 * drift

# buy_levels = [(2, 15), (4, 10)]
# sell_levels = [(6, 10), (8, 5)]