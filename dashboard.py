"""
Prosperity Trading Dashboard
=============================
Interactive order book visualization built with Streamlit + Plotly.

Run:
    streamlit run dashboard.py

Data layout expected under the working directory:
    prices/<round>/*.csv   — order book snapshots (semicolon-delimited)
    trades/<round>/*.csv   — market trades      (semicolon-delimited)

To adapt to a different dataset format, change the CONFIG section below.
"""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import sys
sys.path.insert(0, str(Path(__file__).parent))
from utils import calculate_autocorrelation



# ── CONFIG ─────────────────────────────────────────────────────────────────────

# In your CONFIG section or at the start of main()
PLOTLY_CONFIG = {
    'displayModeBar': True,
    'responsive': True,
    'scrollZoom': True,
}

# Filesystem roots (relative to the directory where you run streamlit)
PRICES_ROOT    = "prices"
TRADES_ROOT    = "trades"
BACKTEST_DIR   = "backtests"

# Backtester uses 1_000_000 per-day offset; dashboard uses DAY_OFFSET_MULTIPLIER
BACKTEST_DAY_OFFSET = 1_000_000

# Delimiters
PRICES_SEP = ";"
TRADES_SEP = ";"

# Prices column names — update here if IMC changes the CSV schema
PRICES_COLS: dict = {
    "day": "day",
    "timestamp": "timestamp",
    "product": "product",
    "mid_price": "mid_price",
    "pnl": "profit_and_loss",
    "bid_price": ["bid_price_1", "bid_price_2", "bid_price_3"],
    "bid_vol":   ["bid_volume_1", "bid_volume_2", "bid_volume_3"],
    "ask_price": ["ask_price_1", "ask_price_2", "ask_price_3"],
    "ask_vol":   ["ask_volume_1", "ask_volume_2", "ask_volume_3"],
}

# Trades column names
TRADES_COLS: dict = {
    "timestamp": "timestamp",
    "buyer":     "buyer",
    "seller":    "seller",
    "symbol":    "symbol",
    "price":     "price",
    "quantity":  "quantity",
}

# Gap inserted between consecutive days on the time axis
# Must be larger than the maximum timestamp in a single day (~999 900)
DAY_OFFSET_MULTIPLIER = 1_000_100

# Visual constants
BID_COLOR       = "#4C8EDA"
ASK_COLOR       = "#E05A5A"
MID_COLOR       = "#888888"
CLEAN_MID_COLOR = "#B06EE0"
PNL_COLOR       = "#56C786"
SPREAD_COLOR    = "#F5A623"
TRADE_COLOR     = "#FFD700"
OWN_BUY_COLOR   = "#00E676"   # green  — our buys
OWN_SELL_COLOR  = "#FF6B6B"   # red    — our sells

MARKER_BASE_SIZE  = 6     # px, minimum dot size
VOLUME_SCALE      = 0.35  # additional px per unit of volume
MARKER_MAX_SIZE   = 22    # px, cap so large-volume dots stay readable

# Regex that extracts the integer day from a filename like prices_round_1_day_-1.csv
DAY_PATTERN = re.compile(r"day_(-?\d+)")


# ── SECTION 2: DATA DISCOVERY ──────────────────────────────────────────────────

def discover_rounds(root: str = PRICES_ROOT) -> list[str]:
    """Return sorted list of subdirectory names under *root*."""
    p = Path(root)
    if not p.exists():
        return []
    dirs = sorted(
        d.name for d in p.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
    return dirs


def _discover_csv_files(directory: Path) -> dict[str, Path]:
    """
    Scan *directory* for .csv files and return an OrderedDict
    ``{label: path}`` sorted by the day integer embedded in the filename.
    Label format: "day -1", "day 0", etc.
    """
    result: dict[int, tuple[str, Path]] = {}
    for f in directory.glob("*.csv"):
        m = DAY_PATTERN.search(f.name)
        if m:
            day_int = int(m.group(1))
            label = f"day {day_int}"
            result[day_int] = (label, f)
    return {label: path for _, (label, path) in sorted(result.items())}


def discover_price_files(round_name: str) -> dict[str, Path]:
    return _discover_csv_files(Path(PRICES_ROOT) / round_name)


def discover_trade_files(round_name: str) -> dict[str, Path]:
    return _discover_csv_files(Path(TRADES_ROOT) / round_name)


def discover_backtest_logs() -> dict[str, Path]:
    """Return {filename_stem: path} for all .log files in BACKTEST_DIR, newest first."""
    p = Path(BACKTEST_DIR)
    if not p.exists():
        return {}
    logs = sorted(p.glob("*.log"), reverse=True)
    return {f.stem: f for f in logs}


def load_backtest_log(path: Path) -> pd.DataFrame:
    """
    Parse a prosperity4btest .log file and return a DataFrame of SUBMISSION
    trades with an ``effective_ts`` column aligned to the dashboard's scale.

    The backtester sequences days with a 1_000_000 offset; we remap to
    DAY_OFFSET_MULTIPLIER (1_000_100) so trades align with loaded price data.
    """
    with open(path) as f:
        content = f.read()

    # ── Parse activities section to discover which days the log covers
    act_start = content.index("Activities log:\n") + len("Activities log:\n")
    act_end   = content.index("\nTrade History:")
    act_df    = pd.read_csv(io.StringIO(content[act_start:act_end]), sep=";",
                            usecols=["day", "timestamp"])
    days_sorted = sorted(act_df["day"].unique())
    # Backtester offset table (1_000_000 per day, same ordering as dashboard)
    min_day     = min(days_sorted)
    log_offset  = {d: i * BACKTEST_DAY_OFFSET            for i, d in enumerate(days_sorted)}
    dash_offset = {d: (d - min_day) * DAY_OFFSET_MULTIPLIER for d in days_sorted}

    # ── Parse Trade History JSON (has trailing commas — strip them)
    th_start = content.index("Trade History:\n") + len("Trade History:\n")
    th_raw   = re.sub(r",\s*([}\]])", r"\1", content[th_start:].strip())
    raw      = json.loads(th_raw)

    if not raw:
        return pd.DataFrame()

    trades_df = pd.DataFrame(raw)

    # Determine each trade's day from its log timestamp, then remap to effective_ts
    def _to_effective(ts: int) -> int:
        day_idx = ts // BACKTEST_DAY_OFFSET
        day_idx = min(day_idx, len(days_sorted) - 1)
        day     = days_sorted[day_idx]
        raw_ts  = ts - log_offset[day]
        return raw_ts + dash_offset[day]

    trades_df["effective_ts"] = trades_df["timestamp"].map(_to_effective)

    # Keep only our own fills
    own = trades_df[
        (trades_df["buyer"] == "SUBMISSION") | (trades_df["seller"] == "SUBMISSION")
    ].copy()
    own["side"] = own.apply(
        lambda r: "buy" if r["buyer"] == "SUBMISSION" else "sell", axis=1
    )
    own.rename(columns={"symbol": TRADES_COLS["symbol"]}, inplace=True)
    return own


# ── SECTION 3: DATA LOADING & PROCESSING ──────────────────────────────────────

def _day_offset_table(paths: tuple[Path, ...]) -> dict[int, int]:
    """
    Build a mapping ``{day_int: timestamp_offset}`` for a set of file paths.
    Files are sorted by day integer; index 0 gets offset 0, index 1 gets
    DAY_OFFSET_MULTIPLIER, etc.
    """
    days = []
    for p in paths:
        m = DAY_PATTERN.search(p.name)
        if m:
            days.append(int(m.group(1)))
    if not days:
        return {}
    min_day = min(days)
    return {d: (d - min_day) * DAY_OFFSET_MULTIPLIER for d in days}


@st.cache_data(ttl=300)
def load_prices(paths: tuple[Path, ...]) -> pd.DataFrame:
    """Load and concatenate price CSVs; add ``effective_ts`` column."""
    offset_table = _day_offset_table(paths)
    frames = []
    for p in paths:
        m = DAY_PATTERN.search(p.name)
        day_int = int(m.group(1)) if m else 0
        offset = offset_table.get(day_int, 0)

        df = pd.read_csv(p, sep=PRICES_SEP, dtype={
            PRICES_COLS["day"]: int,
            PRICES_COLS["timestamp"]: int,
        })
        df["effective_ts"] = df[PRICES_COLS["timestamp"]] + offset
        df["source_day"] = f"day {day_int}"
        # mid_price == 0 means the exchange had an empty book that tick; treat as NaN
        mid_col = PRICES_COLS["mid_price"]
        df.loc[df[mid_col] == 0, mid_col] = float("nan")
        # clean_mid: two-sided mid only, forward-filled within this day
        bp1 = PRICES_COLS["bid_price"][0]
        ap1 = PRICES_COLS["ask_price"][0]
        if bp1 in df.columns and ap1 in df.columns:
            df["clean_mid"] = (df[bp1] + df[ap1]) / 2
            prod_col = PRICES_COLS["product"]
            df["clean_mid"] = df.groupby(prod_col)["clean_mid"].ffill()
        else:
            df["clean_mid"] = float("nan")
        frames.append(df)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


@st.cache_data(ttl=300)
def load_trades(paths: tuple[Path, ...]) -> pd.DataFrame:
    """Load and concatenate trade CSVs; add ``effective_ts`` column."""
    offset_table = _day_offset_table(paths)
    frames = []
    for p in paths:
        m = DAY_PATTERN.search(p.name)
        day_int = int(m.group(1)) if m else 0
        offset = offset_table.get(day_int, 0)

        df = pd.read_csv(p, sep=TRADES_SEP)
        df["effective_ts"] = df[TRADES_COLS["timestamp"]] + offset
        df["source_day"] = f"day {day_int}"

        # Classify each trade for marker coloring
        buyer_col  = TRADES_COLS["buyer"]
        seller_col = TRADES_COLS["seller"]
        buyer_filled  = df[buyer_col].notna()  & (df[buyer_col].astype(str).str.strip() != "")
        seller_filled = df[seller_col].notna() & (df[seller_col].astype(str).str.strip() != "")
        df["trade_label"] = "trade"
        df.loc[buyer_filled,  "trade_label"] = "buyer: " + df.loc[buyer_filled,  buyer_col].astype(str)
        df.loc[seller_filled, "trade_label"] = "seller: " + df.loc[seller_filled, seller_col].astype(str)
        frames.append(df)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def get_products(prices_df: pd.DataFrame) -> list[str]:
    if prices_df.empty:
        return []
    return sorted(prices_df[PRICES_COLS["product"]].unique().tolist())


def filter_product(df: pd.DataFrame, product: str, col: str | None = None) -> pd.DataFrame:
    if df.empty:
        return df
    col = col or PRICES_COLS["product"]
    return df[df[col] == product].copy()


def melt_book_levels(df: pd.DataFrame, side: str) -> pd.DataFrame:
    """
    Convert wide bid/ask columns to long form.

    Returns columns: [effective_ts, price, volume, level, source_day]
    NaN price rows are dropped (handles sparse order book levels).
    """
    price_cols = PRICES_COLS[f"{side}_price"]
    vol_cols   = PRICES_COLS[f"{side}_vol"]
    keep_cols  = ["effective_ts", "source_day"]

    frames = []
    for i, (pc, vc) in enumerate(zip(price_cols, vol_cols), start=1):
        if pc not in df.columns:
            continue
        level_df = df[keep_cols + [pc, vc]].copy()
        level_df.columns = keep_cols + ["price", "volume"]
        level_df = level_df.dropna(subset=["price"])
        level_df["level"] = i
        frames.append(level_df)

    if not frames:
        return pd.DataFrame(columns=["effective_ts", "price", "volume", "level", "source_day"])
    return pd.concat(frames, ignore_index=True)


def compute_spread(df: pd.DataFrame) -> pd.DataFrame:
    """Return DataFrame with [effective_ts, spread] = best_ask - best_bid."""
    bp = PRICES_COLS["bid_price"][0]
    ap = PRICES_COLS["ask_price"][0]
    if bp not in df.columns or ap not in df.columns:
        return pd.DataFrame(columns=["effective_ts", "spread"])
    out = df[["effective_ts", bp, ap]].dropna(subset=[bp, ap]).copy()
    out["spread"] = out[ap] - out[bp]
    return out[["effective_ts", "spread"]]


def apply_normalization(
    bid_df: pd.DataFrame,
    ask_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    ref: "pd.Series",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Subtract reference price (mid_price indexed by effective_ts) from all
    price columns in the three DataFrames.
    Uses a dict-map for speed (avoids merge overhead on large frames).
    """
    ref_dict = ref.to_dict()

    def _subtract(df: pd.DataFrame, col: str) -> pd.DataFrame:
        if df.empty or col not in df.columns:
            return df
        df = df.copy()
        df[col] = df[col] - df["effective_ts"].map(ref_dict)
        return df

    bid_df    = _subtract(bid_df, "price")
    ask_df    = _subtract(ask_df, "price")
    trades_df = _subtract(trades_df, TRADES_COLS["price"])
    return bid_df, ask_df, trades_df


def downsample(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Keep every *n*-th row. Returns df unchanged when n <= 1."""
    if n <= 1 or df.empty:
        return df
    return df.iloc[::n].copy()


# ── SECTION 4: CHART BUILDERS ──────────────────────────────────────────────────

def _marker_sizes(volumes: "pd.Series") -> list[float]:
    sizes = MARKER_BASE_SIZE + volumes.fillna(1) * VOLUME_SCALE
    return sizes.clip(upper=MARKER_MAX_SIZE).tolist()


def build_orderbook_figure(
    bid_df: pd.DataFrame,
    ask_df: pd.DataFrame,
    prices_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    own_trades_df: pd.DataFrame,
    show_mid: bool,
    show_bids: bool,
    show_asks: bool,
    show_trades: bool,
    show_clean_mid: bool,
    show_own_trades: bool,
    size_by_volume: bool,
    normalize: bool,
) -> go.Figure:
    fig = go.Figure()

    y_label = "Price deviation" if normalize else "Price"

    # Mid-price line
    if show_mid and not prices_df.empty:
        mid_col = PRICES_COLS["mid_price"]
        mid = prices_df[["effective_ts", mid_col]].dropna()
        fig.add_trace(go.Scatter(
            x=mid["effective_ts"],
            y=mid[mid_col],
            mode="lines",
            name="Mid price",
            line=dict(color=MID_COLOR, width=1, dash="dot"),
            hovertemplate="<b>Mid</b>: %{y:.2f}<br>t=%{x}<extra></extra>",
        ))

    # Clean mid line
    if show_clean_mid and not prices_df.empty and "clean_mid" in prices_df.columns:
        cm = prices_df[["effective_ts", "clean_mid"]].dropna()
        fig.add_trace(go.Scatter(
            x=cm["effective_ts"],
            y=cm["clean_mid"],
            mode="lines",
            name="Clean mid",
            line=dict(color=CLEAN_MID_COLOR, width=1, dash="dash"),
            hovertemplate="<b>Clean mid</b>: %{y:.2f}<br>t=%{x}<extra></extra>",
        ))

    # Bid levels
    if show_bids and not bid_df.empty:
        sizes = _marker_sizes(bid_df["volume"]) if size_by_volume else MARKER_BASE_SIZE
        fig.add_trace(go.Scattergl(
            x=bid_df["effective_ts"],
            y=bid_df["price"],
            mode="markers",
            name="Bid",
            marker=dict(color=BID_COLOR, size=sizes, opacity=0.75),
            customdata=bid_df[["level", "volume", "source_day"]].values,
            hovertemplate=(
                "<b>Bid</b>: %{y:.2f}<br>"
                "Vol: %{customdata[1]:.0f} | Level %{customdata[0]}<br>"
                "%{customdata[2]}<extra></extra>"
            ),
        ))

    # Ask levels
    if show_asks and not ask_df.empty:
        sizes = _marker_sizes(ask_df["volume"]) if size_by_volume else MARKER_BASE_SIZE
        fig.add_trace(go.Scattergl(
            x=ask_df["effective_ts"],
            y=ask_df["price"],
            mode="markers",
            name="Ask",
            marker=dict(color=ASK_COLOR, size=sizes, opacity=0.75),
            customdata=ask_df[["level", "volume", "source_day"]].values,
            hovertemplate=(
                "<b>Ask</b>: %{y:.2f}<br>"
                "Vol: %{customdata[1]:.0f} | Level %{customdata[0]}<br>"
                "%{customdata[2]}<extra></extra>"
            ),
        ))

    # Trade markers
    if show_trades and not trades_df.empty:
        price_col = TRADES_COLS["price"]
        qty_col   = TRADES_COLS["quantity"]
        label_col = "trade_label"
        for label, grp in trades_df.groupby(label_col):
            fig.add_trace(go.Scattergl(
                x=grp["effective_ts"],
                y=grp[price_col],
                mode="markers",
                name=str(label),
                marker=dict(
                    color=TRADE_COLOR,
                    size=9,
                    symbol="diamond",
                    line=dict(width=1, color="white"),
                ),
                customdata=grp[[qty_col, "source_day"]].values,
                hovertemplate=(
                    "<b>Trade</b>: %{y:.2f}<br>"
                    "Qty: %{customdata[0]:.0f}<br>"
                    f"{label}<br>%{{customdata[1]}}<extra></extra>"
                ),
            ))

    # Own (SUBMISSION) trades from backtest log
    if show_own_trades and not own_trades_df.empty:
        price_col = TRADES_COLS["price"]
        qty_col   = TRADES_COLS["quantity"]
        for side, color, symbol in [
            ("buy",  OWN_BUY_COLOR,  "triangle-up"),
            ("sell", OWN_SELL_COLOR, "triangle-down"),
        ]:
            grp = own_trades_df[own_trades_df["side"] == side]
            if grp.empty:
                continue
            fig.add_trace(go.Scattergl(
                x=grp["effective_ts"],
                y=grp[price_col],
                mode="markers",
                name=f"My {side}s",
                marker=dict(color=color, size=10, symbol=symbol,
                            line=dict(width=1, color="white")),
                customdata=grp[qty_col].values,
                hovertemplate=(
                    f"<b>My {side}</b>: %{{y}}<br>"
                    "Qty: %{customdata}<extra></extra>"
                ),
            ))

    fig.update_layout(
        template="plotly_dark",
        height=500,
        xaxis_title="Timestamp",
        yaxis_title=y_label,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
        margin=dict(l=60, r=20, t=50, b=40),
        hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(20,20,30,1)",
    )
    fig.update_xaxes(fixedrange=False)
    fig.update_yaxes(fixedrange=False)
    return fig


def build_pnl_figure(pnl_df: pd.DataFrame, xaxis_range: list | None = None) -> go.Figure:
    fig = go.Figure()
    if not pnl_df.empty:
        pnl_col = PRICES_COLS["pnl"]
        data = pnl_df[["effective_ts", pnl_col]].dropna()
        fig.add_trace(go.Scatter(
            x=data["effective_ts"],
            y=data[pnl_col],
            mode="lines",
            name="PnL",
            line=dict(color=PNL_COLOR, width=1.5),
            fill="tozeroy",
            fillcolor=f"rgba(86,199,134,0.15)",
            hovertemplate="<b>PnL</b>: %{y:.2f}<br>t=%{x}<extra></extra>",
        ))

    fig.update_layout(
        template="plotly_dark",
        height=180,
        xaxis_title="",
        yaxis_title="PnL",
        margin=dict(l=60, r=20, t=10, b=30),
        showlegend=False,
        dragmode="zoom",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(20,20,30,1)",
    )
    fig.update_xaxes(fixedrange=False)
    fig.update_yaxes(fixedrange=False)
    if xaxis_range:
        fig.update_xaxes(range=xaxis_range)
    return fig


def build_spread_figure(spread_df: pd.DataFrame, xaxis_range: list | None = None) -> go.Figure:
    fig = go.Figure()
    if not spread_df.empty:
        fig.add_trace(go.Scatter(
            x=spread_df["effective_ts"],
            y=spread_df["spread"],
            mode="lines",
            name="Spread",
            line=dict(color=SPREAD_COLOR, width=1.5),
            hovertemplate="<b>Spread</b>: %{y:.2f}<br>t=%{x}<extra></extra>",
        ))

    fig.update_layout(
        template="plotly_dark",
        height=150,
        xaxis_title="Timestamp",
        yaxis_title="Spread",
        margin=dict(l=60, r=20, t=10, b=30),
        showlegend=False,
        dragmode="zoom",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(20,20,30,1)",
    )
    fig.update_xaxes(fixedrange=False)
    fig.update_yaxes(fixedrange=False)
    if xaxis_range:
        fig.update_xaxes(range=xaxis_range)
    return fig


# ── SECTION 5: SIDEBAR ─────────────────────────────────────────────────────────

def render_sidebar(prices_df: pd.DataFrame | None) -> dict:
    """Render controls and return a config dict."""
    st.sidebar.header("Data")

    rounds = discover_rounds()
    if not rounds:
        st.sidebar.error(f"No subdirectories found under '{PRICES_ROOT}/'")
        return {}

    selected_round = st.sidebar.selectbox("Round", rounds, index=len(rounds) - 1)

    price_files = discover_price_files(selected_round)
    all_day_labels = list(price_files.keys())
    selected_days = st.sidebar.multiselect(
        "Days", all_day_labels, default=all_day_labels
    )

    # Product selector — derived from data once loaded
    product = None
    if prices_df is not None and not prices_df.empty:
        products = get_products(prices_df)
        product = st.sidebar.selectbox("Product", products)

    st.sidebar.header("Display")
    show_mid     = st.sidebar.checkbox("Mid price",       value=True)
    show_bids    = st.sidebar.checkbox("Bid levels",      value=True)
    show_asks    = st.sidebar.checkbox("Ask levels",      value=True)
    show_trades  = st.sidebar.checkbox("Trades",          value=True)
    show_spread  = st.sidebar.checkbox("Spread panel",    value=False)
    size_by_vol  = st.sidebar.checkbox("Size by volume",  value=True)

    st.sidebar.header("Normalization")
    normalize = st.sidebar.checkbox(
        "Normalize prices",
        value=False,
        help="Subtract mid-price from all price levels to show deviations.",
    )

    st.sidebar.header("Performance")
    downsample_n = st.sidebar.slider(
        "Downsample (every N rows)", min_value=1, max_value=20, value=1, step=1,
        help="Show only every Nth row. Useful for large datasets.",
    )

    return {
        "round":          selected_round,
        "selected_days":  selected_days,
        "price_files":    price_files,
        "product":        product,
        "show_mid":       show_mid,
        "show_bids":      show_bids,
        "show_asks":      show_asks,
        "show_trades":    show_trades,
        "show_spread":    show_spread,
        "size_by_volume": size_by_vol,
        "normalize":      normalize,
        "downsample_n":   downsample_n,
    }


# ── SECTION 6: MAIN ────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="Prosperity Dashboard",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Custom CSS: tighten top padding
    st.markdown(
        "<style>div.block-container{padding-top:1rem;}</style>",
        unsafe_allow_html=True,
    )

    # ── First pass: render sidebar with no prices loaded yet (for round/day controls)
    # We need to load prices before we can offer the product selector.
    # Two-phase render: first build the non-product sidebar widgets, load data,
    # then render the product selector inside the same sidebar run.
    # Streamlit reruns the whole script on every interaction, so this is fine.

    rounds = discover_rounds()

    # Temporary sidebar pass to get round + days
    st.sidebar.header("Data")
    if not rounds:
        st.sidebar.error(f"No subdirectories found under '{PRICES_ROOT}/'")
        st.error(f"Cannot find data directory '{PRICES_ROOT}/'. Make sure you run `streamlit run dashboard.py` from the project root.")
        return

    selected_round = st.sidebar.selectbox("Round", rounds, index=len(rounds) - 1)

    price_files = discover_price_files(selected_round)
    trade_files = discover_trade_files(selected_round)
    all_day_labels = list(price_files.keys())

    if not all_day_labels:
        st.sidebar.warning("No CSV files found for this round.")
        return

    selected_days = st.sidebar.multiselect(
        "Days", all_day_labels, default=all_day_labels
    )

    if not selected_days:
        st.info("Select at least one day from the sidebar.")
        return

    # ── Load data (cached)
    price_paths = tuple(price_files[d] for d in selected_days)
    trade_paths = tuple(trade_files[d] for d in selected_days if d in trade_files)

    with st.spinner("Loading data…"):
        prices_raw = load_prices(price_paths)
        trades_raw = load_trades(trade_paths) if trade_paths else pd.DataFrame()

    # ── Product selector (needs loaded data)
    products = get_products(prices_raw)
    if not products:
        st.warning("No products found in the loaded files.")
        return

    product = st.sidebar.selectbox("Product", products)

    # ── Remaining sidebar controls
    st.sidebar.header("Display")
    show_mid       = st.sidebar.checkbox("Mid price",       value=True)
    show_clean_mid = st.sidebar.checkbox("Clean mid",       value=False)
    show_bids      = st.sidebar.checkbox("Bid levels",      value=True)
    show_asks       = st.sidebar.checkbox("Ask levels",      value=True)
    show_trades     = st.sidebar.checkbox("Trades",          value=True)
    show_spread     = st.sidebar.checkbox("Spread panel",    value=False)
    size_by_vol     = st.sidebar.checkbox("Size by volume",  value=True)

    st.sidebar.header("Backtest overlay")
    backtest_logs = discover_backtest_logs()
    selected_log  = st.sidebar.selectbox(
        "Log file", options=["(none)"] + list(backtest_logs.keys())
    )
    show_own_trades = st.sidebar.checkbox("Show my trades", value=True)

    st.sidebar.header("Normalization & Stats")
    normalize = st.sidebar.checkbox(
        "Normalize prices",
        value=False,
        help="Subtract reference price from all price levels to show deviations around zero.",
    )
    ref_col = st.sidebar.radio(
        "Reference price",
        options=["mid_price", "clean_mid"],
        format_func=lambda x: "Mid price" if x == "mid_price" else "Clean mid (two-sided, ffilled)",
        index=1,
        help="Used for normalization and stats calculations.",
    )

    st.sidebar.header("Performance")
    downsample_n = st.sidebar.slider(
        "Downsample (every N rows)", min_value=1, max_value=20, value=1, step=1,
        help="Show every Nth row only — reduces plot lag on large datasets.",
    )

    # ── Load backtest log (if selected)
    own_trades_all = pd.DataFrame()
    if selected_log != "(none)" and selected_log in backtest_logs:
        with st.spinner("Loading backtest log…"):
            own_trades_all = load_backtest_log(backtest_logs[selected_log])

    # ── Filter by product
    prices = filter_product(prices_raw, product)
    trades = filter_product(trades_raw, product, col=TRADES_COLS["symbol"]) if not trades_raw.empty else pd.DataFrame()
    own_trades = filter_product(own_trades_all, product, col=TRADES_COLS["symbol"]) if not own_trades_all.empty else pd.DataFrame()

    # ── Optional downsampling (per unique timestamp to preserve structure)
    if downsample_n > 1:
        prices = downsample(prices, downsample_n)
        if not trades.empty:
            trades = downsample(trades, downsample_n)

    # ── Melt book levels
    bid_df = melt_book_levels(prices, "bid")
    ask_df = melt_book_levels(prices, "ask")

    # ── Normalization
    if normalize:
        ref = prices.set_index("effective_ts")[ref_series_col]
        bid_df, ask_df, trades = apply_normalization(bid_df, ask_df, trades, ref)
        if not own_trades.empty:
            _, _, own_trades = apply_normalization(
                pd.DataFrame(), pd.DataFrame(), own_trades, ref
            )

    # ── Title row
    day_str = ", ".join(selected_days)
    st.title(f"📈 {product}")
    st.caption(f"Round: **{selected_round}** | Days: **{day_str}** | Rows: **{len(prices):,}**")

    # ── Stats box
    ref_series_col = PRICES_COLS["mid_price"] if ref_col == "mid_price" else "clean_mid"
    mid_series = prices[ref_series_col].dropna()
    ac1  = calculate_autocorrelation(mid_series, 1)
    ac2  = calculate_autocorrelation(mid_series, 2)
    ac5  = calculate_autocorrelation(mid_series, 5)

    spread_df_stats = compute_spread(prices)
    mean_spread = spread_df_stats["spread"].mean() if not spread_df_stats.empty else float("nan")

    returns = mid_series.pct_change().dropna()
    vol = returns.std() * 100  # as percentage
    mean = mid_series.dropna().mean()

    with st.container(border=True):
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("AC(1)",        f"{ac1:.4f}"  if not np.isnan(ac1)         else "N/A")
        c2.metric("AC(2)",        f"{ac2:.4f}"  if not np.isnan(ac2)         else "N/A")
        c3.metric("Mean spread",  f"{mean_spread:.2f}" if not np.isnan(mean_spread) else "N/A")
        c4.metric("Return vol",   f"{vol:.4f}%" if not np.isnan(vol)         else "N/A")
        c5.metric("Mean",   f"{mean:.4f}%" if not np.isnan(mean)         else "N/A")


    # ── Order book chart
    xaxis_range = st.session_state.get("xaxis_range")
    fig_main = build_orderbook_figure(
        bid_df, ask_df, prices, trades,
        own_trades_df=own_trades,
        show_mid=show_mid,
        show_bids=show_bids,
        show_asks=show_asks,
        show_trades=show_trades,
        show_clean_mid=show_clean_mid,
        show_own_trades=show_own_trades,
        size_by_volume=size_by_vol,
        normalize=normalize,
    )
    if xaxis_range:
        fig_main.update_xaxes(range=xaxis_range)

    # Capture zoom/pan events to link other panels
    main_event = st.plotly_chart(
        fig_main,
        config=PLOTLY_CONFIG,
        use_container_width=True,
        key="main_chart",
        on_select="rerun",
    )
    # Store x-range from relayout if available
    if main_event and hasattr(main_event, "select") and main_event.select:
        rng = main_event.select.get("xaxis.range", None)
        if rng:
            st.session_state["xaxis_range"] = rng

    # ── PnL panel
    st.markdown("#### PnL")
    pnl_cols = ["effective_ts", PRICES_COLS["pnl"]]
    fig_pnl = build_pnl_figure(prices[pnl_cols], xaxis_range)
    st.plotly_chart(fig_pnl, config=PLOTLY_CONFIG, use_container_width=True, key="pnl_chart")

    # ── Spread panel (optional)
    if show_spread:
        st.markdown("#### Bid-Ask Spread")
        spread_df = compute_spread(prices)
        if spread_df.empty:
            st.info("No spread data available — product may have a one-sided order book.")
        else:
            fig_spread = build_spread_figure(spread_df, xaxis_range)
            st.plotly_chart(fig_spread, config=PLOTLY_CONFIG, use_container_width=True, key="spread_chart")

    # ── Footer stats
    with st.expander("Raw data sample"):
        st.dataframe(prices.head(100), width="stretch")


if __name__ == "__main__":
    main()
