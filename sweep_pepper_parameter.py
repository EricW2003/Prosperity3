import argparse
import os
import subprocess
import itertools
import re

TRADER = "trader_strategy/new_pepper_trader.py"
PNL_RE = re.compile(r"Total profit:\s*([\d,\-]+)")


def run_backtest(params, days):
    env = {**os.environ}

    # inject generic params
    for k, v in params.items():
        env[k] = str(v)

    result = subprocess.run(
        ["prosperity4btest", TRADER, *days, "--no-out"],
        capture_output=True,
        text=True,
        env=env,
    )

    m = PNL_RE.search(result.stdout)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--days", nargs="+", default=["1"])

    parser.add_argument("--param1", nargs="+", type=float, default=[1, 2, 3])
    parser.add_argument("--param2", nargs="+", type=float, default=[10, 20])
    parser.add_argument("--param3", nargs="+", type=float, default=[0.1, 0.2, 0.3])

    args = parser.parse_args()

    grid = list(itertools.product(
        args.param1,
        args.param2,
        args.param3
    ))

    print(f"Testing {len(grid)} combinations...\n")

    results = []

    for p1, p2, p3 in grid:

        params = {
            "PARAM1": p1,
            "PARAM2": p2,
            "PARAM3": p3,
        }

        pnl = run_backtest(params, args.days)

        print(f"P1={p1:>5} P2={p2:>5} P3={p3:>5} -> PnL={pnl}")

        if pnl is not None:
            results.append((pnl, params))

    if not results:
        print("No valid results")
        return

    best = max(results, key=lambda x: x[0])

    print("\n🏆 BEST CONFIG:")
    print(best)


if __name__ == "__main__":
    main()