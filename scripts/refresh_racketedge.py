"""
Hourly refresh of the RacketEdge 3.0 data (interim cadence while the user base grows).

	python scripts/refresh_racketedge.py

1. Fold landing into the raw archive (files last written before this run started).
2. Rebuild the clean Parquet months that changed.
3. Rebuild the serving database with an atomic table swap (scripts/load_racketedge.py).
4. Re-apply landing files written while 2-3 ran, so live updates made during the rebuild
   are not lost.

Library root: RACKETEDGE_LIBRARY (env file) or --root. Run with APP_ENV pointing at the 3.0
database. A lock file prevents overlapping runs.
"""
import argparse
import os
import sys
import time
from collections import defaultdict
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import config  # noqa: E402,F401  (loads env/<APP_ENV>.env)
from build_clean_dataset import build_month  # noqa: E402
from load_racketedge import load  # noqa: E402
from libs.racketedge import landing, live  # noqa: E402


def _lock(root: str):
	path = os.path.join(root, "source", ".refresh.lock")
	try:
		fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
	except FileExistsError:
		raise SystemExit(f"another refresh is running (lock: {path}); delete it if that run died")
	os.write(fd, str(os.getpid()).encode())
	os.close(fd)
	return path


def _replay(root: str, since: float) -> int:
	"""Re-apply landing files written after `since` (poller updates made during the rebuild)."""
	from services.api import db as dbmod
	days = defaultdict(set)
	for kind in ("events", "statistics", "pbp"):
		for path, _ in landing.iter_files(root, kind, modified_since=since):
			days[date.fromisoformat(os.path.basename(os.path.dirname(path)))].add(int(os.path.basename(path)[:-5]))
	if not days:
		return 0
	conn = dbmod.connect(read_timeout=600, write_timeout=600)
	try:
		n = 0
		for day, event_ids in sorted(days.items()):
			cache_events = live.latest_payloads(root, "events", day)
			stats = live.latest_payloads(root, "statistics", day)
			pbp = live.latest_payloads(root, "pbp", day)
			items = [(cache_events[e], stats.get(e), pbp.get(e)) for e in sorted(event_ids) if e in cache_events]
			live.upsert(conn, items)
			n += len(items)
		return n
	finally:
		conn.close()


def main() -> int:
	parser = argparse.ArgumentParser(description="Fold landing, rebuild changed months, reload the 3.0 database.")
	parser.add_argument("--root", default="", help="Library root (default: RACKETEDGE_LIBRARY)")
	parser.add_argument("--skip-load", action="store_true", help="Fold and rebuild Parquet only")
	args = parser.parse_args()
	root = os.path.abspath(args.root or os.getenv("RACKETEDGE_LIBRARY") or "")
	if not args.root and not os.getenv("RACKETEDGE_LIBRARY"):
		raise SystemExit("Set RACKETEDGE_LIBRARY (env file) or pass --root")

	lock = _lock(root)
	try:
		started = time.time()
		pending = landing.pending_counts(root)
		affected = landing.fold(root, before=started)
		print(f"[refresh] folded landing {pending} -> months {sorted(affected)}", flush=True)
		for y, m in sorted(affected):
			counts = build_month(root, y, m)
			print(f"[refresh] rebuilt {y:04d}-{m:02d}: {counts}", flush=True)
		if not args.skip_load:
			run_id = load(root)
			replayed = _replay(root, since=started)
			print(f"[refresh] load run {run_id}; replayed {replayed} matches updated during the rebuild", flush=True)
		print(f"[refresh] done in {time.time() - started:.0f}s", flush=True)
		return 0
	finally:
		os.remove(lock)


if __name__ == "__main__":
	raise SystemExit(main())
