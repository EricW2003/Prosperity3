"""
Grid search over delta for round_1_trader_nicho.py.

Usage:
    # explicit values
    python sweep_delta.py --deltas 1 2 3 4 5 6 7 8 9 10

    # linspace (start, stop, num points)
    python sweep_delta.py --linspace 1 15 30

    # custom days
    python sweep_delta.py --linspace 1 15 30 --days 1-0 1--1 1--2
"""

import argparse
import os
import re
import subprocess

import numpy as np
import matplotlib.pyplot as plt

TRADER = "trader_strategy/round_1_trader_nicho.py"
PNL_RE = re.compile(r"Total profit:\s*([\d,\-]+)")


def run_backtest(delta: float, days: list):
    env = {**os.environ, "DELTA": str(delta)}
    result = subprocess.run(
        ["prosperity4btest", TRADER, *days, "--no-out"],
        capture_output=True, text=True, env=env,
    )
    m = PNL_RE.search(result.stdout)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--deltas",   type=float, nargs="+", help="Explicit delta values")
    group.add_argument("--linspace", type=float, nargs=3,   metavar=("START", "STOP", "NUM"),
                       help="numpy linspace(start, stop, num)")
    parser.add_argument("--days", type=str, nargs="+", default=["1"])
    args = parser.parse_args()

    if args.linspace:
        start, stop, num = args.linspace
        deltas = np.linspace(start, stop, int(num))
    elif args.deltas:
        deltas = np.array(args.deltas)
    else:
        deltas = np.arange(1, 16, dtype=float)

    print(f"Sweeping {len(deltas)} delta values: {deltas[0]:.2f} → {deltas[-1]:.2f}")
    print(f"Days: {args.days}\n")
    print(f"{'delta':>10}  {'PnL':>12}")
    print("-" * 26)

    pnls = []
    for delta in deltas:
        pnl = run_backtest(delta, args.days)
        pnls.append(pnl)
        pnl_str = f"{pnl:,}" if pnl is not None else "error"
        print(f"{delta:>10.3f}  {pnl_str:>12}")

    valid = [(d, p) for d, p in zip(deltas, pnls) if p is not None]
    if not valid:
        print("No valid results.")
        return

    best_delta, best_pnl = max(valid, key=lambda x: x[1])
    print(f"\nBest delta: {best_delta:.3f}  (PnL: {best_pnl:,})")

    # ── Plot ──────────────────────────────────────────────────────────────────
    xs, ys = zip(*valid)
    _, ax = plt.subplots(figsize=(9, 5))
    ax.plot(xs, ys, marker="o", markersize=4, linewidth=1.5, color="#4C8EDA")
    ax.axvline(best_delta, color="#F5A623", linestyle="--", linewidth=1, label=f"best δ={best_delta:.2f}")
    ax.axhline(0, color="#888888", linewidth=0.8, linestyle=":")
    ax.set_xlabel("delta")
    ax.set_ylabel("PnL (SeaShells)")
    ax.set_title(f"PnL vs delta — days: {', '.join(args.days)}")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
