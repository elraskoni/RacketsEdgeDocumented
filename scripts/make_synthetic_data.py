"""
Generate a synthetic tennis season into the landing area, where the real collector writes.

	python scripts/make_synthetic_data.py --root ./library --months 6
	python scripts/refresh_racketedge.py --root ./library     # fold -> clean Parquet -> serving DB

Players, weekly tournaments (ATP/WTA, Challenger/WTA 125, ITF) and matches simulated point by
point (libs/synthetic/simulator.py). Matches after "now" are written as scheduled, so the
/today endpoint has fixtures. Statistics for the main and second tiers, point-by-point for the
main tier: the same coverage policy as production.
"""
import argparse
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from libs.racketedge import landing  # noqa: E402
from libs.synthetic.simulator import make_players, season  # noqa: E402


def main() -> int:
	ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
	ap.add_argument("--root", default="./library")
	ap.add_argument("--months", type=int, default=6, help="history length up to today")
	ap.add_argument("--players", type=int, default=160, help="players per gender")
	ap.add_argument("--seed", type=int, default=7)
	args = ap.parse_args()
	root = os.path.abspath(args.root)
	rng = random.Random(args.seed)
	now = datetime.now(timezone.utc)
	start = (now - timedelta(days=30 * args.months)).date()
	t0 = time.time()
	players = make_players(rng, args.players)
	payloads = season(rng, players, start, now.date() + timedelta(days=6), now)
	counts = {"events": 0, "statistics": 0, "pbp": 0}
	for p in payloads:
		ev = p["event"]
		for kind in counts:
			body = ev if kind == "events" else p["statistics" if kind == "statistics" else "pbp"]
			if body is not None:
				landing.write(root, kind, ev["id"], ev["start_timestamp"], body)
				counts[kind] += 1
	print(f"[synthetic] {len(players)} players, {len(payloads)} matches {start}..{now.date() + timedelta(days=6)}; "
		f"landed {counts} under {root} in {time.time() - t0:.0f}s", flush=True)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
