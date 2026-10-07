"""
Build the clean Parquet dataset from the raw archive using the source parser (libs.parsers.synthetic in this repository).

Reads   <root>/source/raw/{events,statistics,pbp}/YYYY/MM/*.jsonl.gz
Writes  <root>/source/clean/{matches,player_match_stats,pbp_points,validation}/YYYY/YYYY-MM.parquet
        <root>/source/clean/manifests/{table}.json   (rows, bytes, sha256, parser version per month)

Files are written to clean/.staging and moved into place only when complete.

Usage (from repo root, project .venv):

	python scripts/build_clean_dataset.py --root ./library --year 2025
	python scripts/build_clean_dataset.py --root ./library --year 2025 --month 5
	python scripts/build_clean_dataset.py --root ./library --all
	python scripts/build_clean_dataset.py --root ./library --verify
"""
import argparse
import glob
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import pyarrow.parquet as pq

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if repo_root not in sys.path:
	sys.path.append(repo_root)
from libs.parsers.synthetic import PARSER_VERSION  # noqa: E402
from libs.racketedge.clean_schema import TABLES, parse_events, to_table  # noqa: E402

_JSON_COL = {"events": "raw_event_json", "statistics": "statistics_json", "pbp": "point_by_point_json"}


def _sha256_file(path: str) -> str:
	h = hashlib.sha256()
	with open(path, "rb") as f:
		for chunk in iter(lambda: f.read(1 << 20), b""):
			h.update(chunk)
	return h.hexdigest()


def _load_month(raw_root: str, kind: str, year: int, month: int) -> Dict[int, Any]:
	out: Dict[int, Any] = {}
	col = _JSON_COL[kind]
	for path in sorted(glob.glob(os.path.join(raw_root, kind, f"{year:04d}", f"{month:02d}", "*.jsonl.gz"))):
		with gzip.open(path, "rt", encoding="utf-8") as f:
			for line in f:
				rec = json.loads(line)
				out[int(rec["event_id"])] = rec.get(col)
	return out


def _months_in_raw(raw_root: str) -> List[Tuple[int, int]]:
	months = set()
	for path in glob.glob(os.path.join(raw_root, "events", "*", "*")):
		y, m = os.path.basename(os.path.dirname(path)), os.path.basename(path)
		if y.isdigit() and m.isdigit():
			months.add((int(y), int(m)))
	return sorted(months)


# ---- manifest ----

def _manifest_path(clean_root: str, table: str) -> str:
	return os.path.join(clean_root, "manifests", f"{table}.json")


def _load_manifest(clean_root: str, table: str) -> Dict[str, Any]:
	path = _manifest_path(clean_root, table)
	if os.path.exists(path):
		with open(path, "r", encoding="utf-8") as f:
			return json.load(f)
	return {"table": table, "partition": "utc month of matches.start_timestamp", "months": {}}


def _save_manifest(clean_root: str, table: str, manifest: Dict[str, Any]) -> None:
	months = manifest["months"]
	manifest["months"] = {k: months[k] for k in sorted(months)}
	manifest["totals"] = {"months": len(months), "rows": sum(v["rows"] for v in months.values()), "bytes": sum(v["bytes"] for v in months.values())}
	manifest["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
	path = _manifest_path(clean_root, table)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	tmp = path + ".tmp"
	with open(tmp, "w", encoding="utf-8") as f:
		json.dump(manifest, f, indent=2)
		f.write("\n")
	os.replace(tmp, path)


# ---- build ----

def build_month(root: str, year: int, month: int) -> Dict[str, int]:
	raw_root = os.path.join(root, "source", "raw")
	clean_root = os.path.join(root, "source", "clean")
	staging = os.path.join(clean_root, ".staging")
	os.makedirs(staging, exist_ok=True)

	events = _load_month(raw_root, "events", year, month)
	stats = _load_month(raw_root, "statistics", year, month)
	pbp = _load_month(raw_root, "pbp", year, month)

	out = parse_events((events[eid], stats.get(eid), pbp.get(eid)) for eid in sorted(events))

	key = f"{year:04d}-{month:02d}"
	counts: Dict[str, int] = {}
	for table in TABLES:
		tbl = to_table(table, out[table])
		tbl = tbl.replace_schema_metadata({"parser_version": PARSER_VERSION, "month": key})
		tmp = os.path.join(staging, f"{table}_{key}.parquet")
		pq.write_table(tbl, tmp, compression="zstd")
		rel = f"{year:04d}/{key}.parquet"
		final = os.path.join(clean_root, table, f"{year:04d}", f"{key}.parquet")
		os.makedirs(os.path.dirname(final), exist_ok=True)
		os.replace(tmp, final)
		manifest = _load_manifest(clean_root, table)
		manifest["months"][key] = {
			"file": rel,
			"rows": tbl.num_rows,
			"bytes": os.path.getsize(final),
			"sha256": _sha256_file(final),
			"parser_version": PARSER_VERSION,
			"source_events": len(events),
		}
		_save_manifest(clean_root, table, manifest)
		counts[table] = tbl.num_rows

	if os.path.isdir(staging) and not os.listdir(staging):
		os.rmdir(staging)
	return counts


def verify(root: str) -> int:
	clean_root = os.path.join(root, "source", "clean")
	issues = 0
	for table in TABLES:
		manifest = _load_manifest(clean_root, table)
		tracked = set()
		for key, entry in manifest["months"].items():
			path = os.path.join(clean_root, table, *entry["file"].split("/"))
			tracked.add(entry["file"])
			if not os.path.exists(path):
				print(f"[{table}] {key}: missing file")
				issues += 1
			elif os.path.getsize(path) != entry["bytes"] or _sha256_file(path) != entry["sha256"]:
				print(f"[{table}] {key}: checksum mismatch")
				issues += 1
			elif pq.ParquetFile(path).metadata.num_rows != entry["rows"]:
				print(f"[{table}] {key}: row count mismatch")
				issues += 1
		base = os.path.join(clean_root, table)
		for dirpath, _, files in os.walk(base):
			for fn in files:
				rel = os.path.relpath(os.path.join(dirpath, fn), base).replace(os.sep, "/")
				if rel not in tracked:
					print(f"[{table}] stray file: {rel}")
					issues += 1
		print(f"[{table}] months={len(manifest['months'])} rows={manifest.get('totals', {}).get('rows', 0)}")
	print("OK" if not issues else f"{issues} issue(s)")
	return 1 if issues else 0


def main() -> int:
	parser = argparse.ArgumentParser(description="Build clean Parquet dataset from the raw archive.")
	parser.add_argument("--root", required=True, help="Library root, e.g. ./library")
	parser.add_argument("--year", type=int)
	parser.add_argument("--month", type=int)
	parser.add_argument("--all", action="store_true", help="Build every month present in the raw archive")
	parser.add_argument("--verify", action="store_true", help="Check clean files against their manifests")
	args = parser.parse_args()
	root = os.path.abspath(args.root)

	if args.verify:
		return verify(root)

	raw_root = os.path.join(root, "source", "raw")
	months = _months_in_raw(raw_root)
	if args.year:
		months = [(y, m) for y, m in months if y == args.year and (not args.month or m == args.month)]
	elif not args.all:
		parser.error("choose --year [--month], --all or --verify")
	if not months:
		print("No matching months in raw archive.")
		return 1

	for y, m in months:
		counts = build_month(root, y, m)
		print(f"[clean] {y:04d}-{m:02d}  " + "  ".join(f"{t}={n}" for t, n in counts.items()), flush=True)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
