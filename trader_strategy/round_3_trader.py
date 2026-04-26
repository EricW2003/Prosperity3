from datamodel import TradingState, Order
from typing import List
import json
import math
import os

# ── Option constants ──────────────────────────────────────────────────────────

OPTION_SYMBOLS = [
    'VEV_4000', 'VEV_4500', 'VEV_5000', 'VEV_5100', 'VEV_5200',
    'VEV_5300', 'VEV_5400', 'VEV_5500', 'VEV_6000', 'VEV_6500',
]
OPTION_UNDERLYING_SYMBOL = 'VELVETFRUIT_EXTRACT'
OPTION_LIMIT = 300
UNDERLYING_LIMIT = 200
WALL_THRESHOLD = 5

DAYS_PER_YEAR = 365
T_EXPIRY_DAYS = 7  # days TTE at start of day 0

# Smile polynomial: IV = a*m^2 + b*m + c, m = log(K/S)/sqrt(TTE)
# Fitted from round-3 data (R^2 = 0.984)
SMILE_COEFFS = [0.1425027, -0.00202036, 0.23569422]

# EMA windows (in timestamps; 10 000 timestamps per day)
THEO_NORM_WINDOW = 500
IV_SCALPING_WINDOW = 200
underlying_mean_reversion_window = 200
options_mean_reversion_window = 500

# Trading thresholds (price units)
underlying_mean_reversion_thr = 8
options_mean_reversion_thr = 8
IV_SCALPING_THR = 0.5   # minimum switch-mean before we trade
THR_OPEN = 3.0          # theo-diff deviation to open a position
THR_CLOSE = 1.0         # theo-diff deviation to close a position
LOW_VEGA_THR_ADJ = 4.0  # extra cushion for low-vega options


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


# ── Hydrogel helpers (unchanged) ──────────────────────────────────────────────

def grab_mispriced_orders(order_depth, fair_value, position, limit, product, orders):
    for ask_price, ask_qty in sorted(order_depth.sell_orders.items()):
        if ask_price < fair_value:
            buy_qty = min(-ask_qty, limit - position)
            if buy_qty > 0:
                orders.append(Order(product, ask_price, buy_qty))
                position += buy_qty
        else:
            break
    for bid_price, bid_qty in sorted(order_depth.buy_orders.items(), reverse=True):
        if bid_price > fair_value:
            sell_qty = min(bid_qty, limit + position)
            if sell_qty > 0:
                orders.append(Order(product, bid_price, -sell_qty))
                position -= sell_qty
        else:
            break
    return position


def trade_hydrogel_pack(orders, order_depth, limit, delta, skew_factor, position, join_offset=0):
    product = "HYDROGEL_PACK"
    wall_threshold = 5
    bid_walls = [p for p, v in order_depth.buy_orders.items() if v >= wall_threshold]
    ask_walls = [p for p, v in order_depth.sell_orders.items() if -v >= wall_threshold]
    if bid_walls and ask_walls:
        fair_value = (max(bid_walls) + min(ask_walls)) / 2
    else:
        fair_value = 9990

    position = grab_mispriced_orders(order_depth, fair_value, position, limit, product, orders)

    best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
    best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None
    skew = int(round(position * skew_factor))
    bid_cap = math.floor(fair_value) - 1 - skew
    ask_cap = math.ceil(fair_value) + 1 - skew
    buy_price  = min(best_bid + join_offset, bid_cap) if best_bid is not None else int(fair_value - delta) - skew
    sell_price = max(best_ask - join_offset, ask_cap) if best_ask is not None else int(fair_value + delta) - skew

    soft_limit = 150
    buy_volume  = max(0, soft_limit - position)
    sell_volume = max(0, soft_limit + position)
    if buy_volume  > 0: orders.append(Order(product, buy_price,   buy_volume))
    if sell_volume > 0: orders.append(Order(product, sell_price, -sell_volume))
    return position


# ── ProductTrader ─────────────────────────────────────────────────────────────

class ProductTrader:
    def __init__(self, symbol, state, _prints, last_traderData, product_group='DEFAULT'):
        self.name = symbol
        self.state = state
        self.last_traderData = last_traderData
        self.orders: List[Order] = []

        if product_group == 'OPTION':
            limit = OPTION_LIMIT
        elif product_group == 'UNDERLYING':
            limit = UNDERLYING_LIMIT
        else:
            limit = UNDERLYING_LIMIT
        self.initial_position = state.position.get(symbol, 0)
        self.max_allowed_buy_volume  = limit - self.initial_position
        self.max_allowed_sell_volume = limit + self.initial_position

        od = state.order_depths.get(symbol)
        if od and od.buy_orders:
            self.best_bid = max(od.buy_orders)
        else:
            self.best_bid = None
        if od and od.sell_orders:
            self.best_ask = min(od.sell_orders)
        else:
            self.best_ask = None

        bid_walls = [p for p, v in od.buy_orders.items() if v >= WALL_THRESHOLD] if od else []
        ask_walls = [p for p, v in od.sell_orders.items() if -v >= WALL_THRESHOLD] if od else []
        self.bid_wall = max(bid_walls) if bid_walls else None
        self.ask_wall = min(ask_walls) if ask_walls else None
        if self.bid_wall is not None and self.ask_wall is not None:
            self.wall_mid = (self.bid_wall + self.ask_wall) / 2
        else:
            self.wall_mid = None

    def bid(self, price, qty):
        qty = min(qty, self.max_allowed_buy_volume)
        if qty > 0:
            self.orders.append(Order(self.name, price, qty))
            self.max_allowed_buy_volume -= qty

    def ask(self, price, qty):
        qty = min(qty, self.max_allowed_sell_volume)
        if qty > 0:
            self.orders.append(Order(self.name, price, -qty))
            self.max_allowed_sell_volume -= qty


# ── OptionTrader ──────────────────────────────────────────────────────────────

class OptionTrader:
    def __init__(self, state, prints, new_trader_data, last_trader_data):

        self.options = [
            ProductTrader(s, state, prints, last_trader_data, product_group='OPTION')
            for s in OPTION_SYMBOLS
        ]
        self.underlying = ProductTrader(
            OPTION_UNDERLYING_SYMBOL, state, prints, last_trader_data, product_group='UNDERLYING'
        )

        self.state = state
        self.last_traderData = last_trader_data
        self.new_trader_data = new_trader_data

        # Determine current competition day from traderData
        prev_ts = last_trader_data.get('prev_ts', -1)
        self.day = last_trader_data.get('day', 0)
        if state.timestamp < prev_ts:       # timestamp reset → new day
            self.day += 1
        new_trader_data['day'] = self.day
        new_trader_data['prev_ts'] = state.timestamp

        self.indicators = self.calculate_indicators()

    # ── Black-Scholes + smile IV ──────────────────────────────────────────────

    def get_option_values(self, S, K, TTE):

        def bs_call(S, K, TTE, s, r=0):
            d1 = (math.log(S / K) + (r + 0.5 * s**2) * TTE) / (s * TTE**0.5)
            d2 = d1 - s * TTE**0.5
            return S * _norm_cdf(d1) - K * math.exp(-r * TTE) * _norm_cdf(d2), _norm_cdf(d1)

        def bs_vega(S, K, TTE, s, r=0):
            d1 = (math.log(S / K) + (r + 0.5 * s**2) * TTE) / (s * TTE**0.5)
            return S * _norm_pdf(d1) * TTE**0.5

        def get_iv(St, K, TTE):
            m_t_k = math.log(K / St) / TTE**0.5
            a, b, c = SMILE_COEFFS
            iv = a * m_t_k**2 + b * m_t_k + c
            return max(iv, 0.01)    # floor to avoid degenerate BS inputs

        iv = get_iv(S, K, TTE)
        bs_call_value, delta = bs_call(S, K, TTE, iv)
        vega = bs_vega(S, K, TTE, iv)
        return bs_call_value, delta, vega

    # ── EMA helper ────────────────────────────────────────────────────────────

    def calculate_ema(self, td_key, window, value):
        old_mean = self.last_traderData.get(td_key, 0)
        alpha = 2 / (window + 1)
        new_mean = alpha * value + (1 - alpha) * old_mean
        self.new_trader_data[td_key] = new_mean
        return new_mean

    # ── Indicators ────────────────────────────────────────────────────────────

    def calculate_indicators(self):
        indicators = {
            'ema_u_dev': None,
            'ema_o_dev': None,
            'mean_theo_diffs': {},
            'current_theo_diffs': {},
            'switch_means': {},
            'deltas': {},
            'vegas': {},
        }

        if self.underlying.wall_mid is None:
            return indicators

        new_mean_u = self.calculate_ema('ema_u', underlying_mean_reversion_window, self.underlying.wall_mid)
        indicators['ema_u_dev'] = self.underlying.wall_mid - new_mean_u

        new_mean_o = self.calculate_ema('ema_o', options_mean_reversion_window, self.underlying.wall_mid)
        indicators['ema_o_dev'] = self.underlying.wall_mid - new_mean_o

        for option in self.options:
            k = int(option.name.split('_')[-1])

            # Fallback mid when wall_mid is missing
            if option.wall_mid is None:
                if option.ask_wall is not None:
                    option.wall_mid = option.ask_wall - 0.5
                    option.bid_wall = option.ask_wall - 1
                    option.best_bid = option.ask_wall - 1
                elif option.bid_wall is not None:
                    option.wall_mid = option.bid_wall + 0.5
                    option.ask_wall = option.bid_wall + 1
                    option.best_ask = option.bid_wall + 1

            if option.wall_mid is None:
                continue

            tte = (T_EXPIRY_DAYS - self.day - self.state.timestamp / 1_000_000) / DAYS_PER_YEAR
            if tte <= 0:
                continue

            underlying_mid = None
            if self.underlying.best_bid is not None and self.underlying.best_ask is not None:
                underlying_mid = 0.5 * self.underlying.best_bid + 0.5 * self.underlying.best_ask
            elif self.underlying.wall_mid is not None:
                underlying_mid = self.underlying.wall_mid

            if underlying_mid is None:
                continue

            option_theo, option_delta, option_vega = self.get_option_values(underlying_mid, k, tte)
            option_theo_diff = option.wall_mid - option_theo

            indicators['current_theo_diffs'][option.name] = option_theo_diff
            indicators['deltas'][option.name] = option_delta
            indicators['vegas'][option.name] = option_vega

            new_mean_diff = self.calculate_ema(f'{option.name}_theo_diff', THEO_NORM_WINDOW, option_theo_diff)
            indicators['mean_theo_diffs'][option.name] = new_mean_diff

            new_mean_avg_dev = self.calculate_ema(
                f'{option.name}_avg_devs', IV_SCALPING_WINDOW, abs(option_theo_diff - new_mean_diff)
            )
            indicators['switch_means'][option.name] = new_mean_avg_dev

        return indicators

    # ── IV scalping orders ────────────────────────────────────────────────────

    def get_iv_scalping_orders(self, options):
        out = {}
        for option in options:
            name = option.name
            if (name not in self.indicators['mean_theo_diffs']
                    or name not in self.indicators['current_theo_diffs']
                    or name not in self.indicators['switch_means']):
                out[name] = option.orders
                continue

            switch_mean = self.indicators['switch_means'][name]

            if switch_mean >= IV_SCALPING_THR:
                current_theo_diff = self.indicators['current_theo_diffs'][name]
                mean_theo_diff    = self.indicators['mean_theo_diffs'][name]

                low_vega_adj = LOW_VEGA_THR_ADJ if self.indicators['vegas'].get(name, 1) <= 1 else 0

                # best_bid - theo > mean + THR_OPEN  →  bid is expensive, sell it
                if (option.best_bid is not None
                        and current_theo_diff - option.wall_mid + option.best_bid - mean_theo_diff
                            >= THR_OPEN + low_vega_adj
                        and option.max_allowed_sell_volume > 0):
                    option.ask(option.best_bid, option.max_allowed_sell_volume)

                # close long when bid is above mean + THR_CLOSE
                if (option.best_bid is not None
                        and current_theo_diff - option.wall_mid + option.best_bid - mean_theo_diff
                            >= THR_CLOSE
                        and option.initial_position > 0):
                    option.ask(option.best_bid, option.initial_position)

                # best_ask - theo < mean - THR_OPEN  →  ask is cheap, buy it
                elif (option.best_ask is not None
                        and current_theo_diff - option.wall_mid + option.best_ask - mean_theo_diff
                            <= -(THR_OPEN + low_vega_adj)
                        and option.max_allowed_buy_volume > 0):
                    option.bid(option.best_ask, option.max_allowed_buy_volume)

                # close short when ask is below mean - THR_CLOSE
                if (option.best_ask is not None
                        and current_theo_diff - option.wall_mid + option.best_ask - mean_theo_diff
                            <= -THR_CLOSE
                        and option.initial_position < 0):
                    option.bid(option.best_ask, -option.initial_position)

            else:
                # low-volatility regime: flatten any open position
                if option.initial_position > 0 and option.best_bid is not None:
                    option.ask(option.best_bid, option.initial_position)
                elif option.initial_position < 0 and option.best_ask is not None:
                    option.bid(option.best_ask, -option.initial_position)

            out[name] = option.orders
        return out

    # ── Mean-reversion orders ─────────────────────────────────────────────────

    def get_mr_orders(self, options):
        out = {}
        for option in options:
            name = option.name
            if (name not in self.indicators['current_theo_diffs']
                    or name not in self.indicators['mean_theo_diffs']
                    or self.indicators.get('ema_o_dev') is None):
                out[name] = option.orders
                continue

            current_deviation = self.indicators['ema_o_dev']
            iv_deviation = (self.indicators['current_theo_diffs'][name]
                            - self.indicators['mean_theo_diffs'][name])
            current_deviation += iv_deviation

            if (current_deviation > options_mean_reversion_thr
                    and option.best_bid is not None
                    and option.max_allowed_sell_volume > 0):
                option.ask(option.best_bid, option.max_allowed_sell_volume)
            elif (current_deviation < -options_mean_reversion_thr
                    and option.best_ask is not None
                    and option.max_allowed_buy_volume > 0):
                option.bid(option.best_ask, option.max_allowed_buy_volume)

            out[name] = option.orders
        return out

    # ── Option entry point ────────────────────────────────────────────────────

    def get_option_orders(self):
        warmup = min(THEO_NORM_WINDOW, underlying_mean_reversion_window, options_mean_reversion_window)
        if self.state.timestamp / 100 < warmup:
            return {}

        # NTM options (4500–5500): IV scalping
        iv_scalping_options = [
            o for o in self.options
            if 4500 <= int(o.name.split('_')[-1]) <= 5500
        ]
        # Deep OTM/ITM (4000, 6000, 6500): mean-reversion only
        mr_options = [
            o for o in self.options
            if int(o.name.split('_')[-1]) in (4000, 6000, 6500)
        ]

        return {
            **self.get_iv_scalping_orders(iv_scalping_options),
            **self.get_mr_orders(mr_options),
        }

    # ── Underlying entry point ────────────────────────────────────────────────

    def get_underlying_orders(self):
        if self.state.timestamp / 100 < underlying_mean_reversion_window:
            return {}

        if self.indicators.get('ema_u_dev') is not None:
            current_deviation = self.indicators['ema_o_dev']  # use slow EMA as signal
            if current_deviation > underlying_mean_reversion_thr and self.underlying.max_allowed_sell_volume > 0:
                if self.underlying.bid_wall is not None:
                    self.underlying.ask(self.underlying.bid_wall + 1, self.underlying.max_allowed_sell_volume)
            elif current_deviation < -underlying_mean_reversion_thr and self.underlying.max_allowed_buy_volume > 0:
                if self.underlying.ask_wall is not None:
                    self.underlying.bid(self.underlying.ask_wall - 1, self.underlying.max_allowed_buy_volume)

        return {self.underlying.name: self.underlying.orders}

    def get_orders(self):
        return {
            **self.get_option_orders(),
            **self.get_underlying_orders(),
        }


# ── Trader ────────────────────────────────────────────────────────────────────

class Trader:

    def run(self, state: TradingState):

        last_trader_data: dict = json.loads(state.traderData) if state.traderData else {}
        new_trader_data: dict = {}
        result = {}

        # ── HYDROGEL PACK ─────────────────────────────────────────────────────
        product = "HYDROGEL_PACK"
        orders: List[Order] = []
        position = state.position.get(product, 0)
        order_depth = state.order_depths.get(product)
        if order_depth:
            trade_hydrogel_pack(
                orders, order_depth,
                limit=200,
                delta=float(os.environ.get("DELTA", "20")),
                skew_factor=float(os.environ.get("SKEW_FACTOR", "0.13")),
                position=position,
                join_offset=int(float(os.environ.get("JOIN_OFFSET", "0"))),
            )
        result[product] = orders

        # ── VEV OPTIONS ───────────────────────────────────────────────────────
        option_trader = OptionTrader(state, None, new_trader_data, last_trader_data)
        for sym, sym_orders in option_trader.get_orders().items():
            result[sym] = sym_orders

        return result, 0, json.dumps(new_trader_data)
