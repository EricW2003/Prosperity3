from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import json
import math

def grab_mispriced_orders(order_depth: OrderDepth, 
                            fair_value: int, 
                            position: int,
                            limit: int,
                            product: str,
                            orders: List[Order],
                            ):
    """Market taking. Takes orders before anything else happens for the product"""

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

    return position

def trade_ash_coated_osmium(orders: List[Order],
                            order_depth: OrderDepth, 
                            limit: int,
                            delta: int,
                            position: int
                            ):
    # Dynamic fair: midpoint of largest-volume ("wall") levels on each side
    product = "ASH_COATED_OSMIUM"
    wall_threshold = 5
    bid_walls = [p for p, v in order_depth.buy_orders.items() if v >= wall_threshold]
    ask_walls = [p for p, v in order_depth.sell_orders.items() if -v >= wall_threshold]
    if bid_walls and ask_walls:
        fair_value = (max(bid_walls) + min(ask_walls)) / 2
    else:
        fair_value = 10000

    position = grab_mispriced_orders(order_depth, fair_value, position, limit, product, orders)

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

    return position

def trade_intarian_pepper_root(orders: List[Order],
                            order_depth: OrderDepth, 
                            limit: int,
                            position: int,
                            mids, 
                            alpha: int,
                            skew_factor: int,
                            half_spread:int
                            ):
    
    product="INTARIAN_PEPPER_ROOT"
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
        return position, orders

    # ── Fair value: wall_mid shifted up for positive drift ──
    fair_value = wall_mid + alpha

    # ── Market taking: grab mispriced orders ──
    position = grab_mispriced_orders(order_depth, fair_value, position, limit, product, orders)

    # ── Inventory skew: positive position → lower both prices ──
    skew = position * skew_factor

    # ── Market making: passive quotes around fair value ──
    buy_price = math.floor(fair_value - half_spread - skew)
    sell_price = math.ceil(fair_value + half_spread - skew)

    buy_volume = limit - position
    sell_volume = limit + position

    if buy_volume > 0:
        orders.append(Order(product, buy_price, buy_volume))
    if sell_volume > 0:
        orders.append(Order(product, sell_price, -sell_volume))

    return position

class Trader:

    def bid(self):
        return 15
    
    def run(self, state: TradingState):

        result = {}

        # ASH COATED OSMIUM
        product="ASH_COATED_OSMIUM"
        orders: List[Order] = []
        limit = 80
        DELTA = 20
        position = state.position.get(product, 0)
        order_depth = state.order_depths.get(product)

        if order_depth:
            position = trade_ash_coated_osmium(
                orders,
                order_depth, 
                limit=limit, 
                delta=DELTA, 
                position=position
            )
        
        result[product] = orders

        #INTARIAN PEPPER ROOT
        product = "INTARIAN_PEPPER_ROOT"
        orders: List[Order] = []
        limit = 80
        ALPHA = 3.25
        HALF_SPREAD = 7
        SKEW_FACTOR = 0.03
        position = state.position.get(product, 0)
        order_depth = state.order_depths.get(product)
        mids = json.loads(state.traderData) if state.traderData else []         # Restore mids history from traderData

        if order_depth:
            position = trade_intarian_pepper_root(
                orders,
                order_depth, 
                limit=limit, 
                position=position,
                mids=mids,
                alpha=ALPHA, 
                skew_factor=SKEW_FACTOR,
                half_spread=HALF_SPREAD
            )

        result[product] = orders

        return result, 0, json.dumps(mids)
