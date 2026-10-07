"""Raw archive layout and manifests (<root>/source/raw, <root>/source/manifests).

Shared by scripts/export_raw_archive.py (initial export) and libs/racketedge/landing.py
(hourly fold of newly fetched payloads).
"""
import hashlib
import json
import os
from datetime import date, datetime, timezone
from typing import Any, Dict

# archive kind -> (pre-3.0 DB table, JSON column holding the vendor payload)
KINDS: Dict[str, tuple] = {
	"events": ("events", "raw_event_json"),
	"statistics": ("event_statistics", "statistics_json"),
	"pbp": ("event_pbp", "point_by_point_json"),
	"odds": (None, "odds_json"),  # opening + closing pre-match prices; null = source has none
}


def json_column(kind: str) -> str:
	return KINDS[kind][1]


def utc_day(ts: int) -> date:
	return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()


def day_rel_path(d: date) -> str:
	return f"{d.year:04d}/{d.month:02d}/{d.isoformat()}.jsonl.gz"


def day_path(root: str, kind: str, d: date) -> str:
	return os.path.join(root, "source", "raw", kind, *day_rel_path(d).split("/"))


def sha256_file(path: str) -> str:
	h = hashlib.sha256()
	with open(path, "rb") as f:
		for chunk in iter(lambda: f.read(1 << 20), b""):
			h.update(chunk)
	return h.hexdigest()


def manifest_path(root: str, kind: str) -> str:
	return os.path.join(root, "source", "manifests", f"{kind}.json")


def load_manifest(root: str, kind: str) -> Dict[str, Any]:
	path = manifest_path(root, kind)
	if os.path.exists(path):
		with open(path, "r", encoding="utf-8") as f:
			return json.load(f)
	db_table, json_col = KINDS[kind]
	return {"table": db_table, "json_column": json_col, "partition": "utc_date(events.start_timestamp)", "days": {}}


def save_manifest(root: str, kind: str, manifest: Dict[str, Any]) -> None:
	days = manifest["days"]
	manifest["days"] = {k: days[k] for k in sorted(days)}
	manifest["totals"] = {
		"days": len(days),
		"rows": sum(v["rows"] for v in days.values()),
		"bytes": sum(v["bytes"] for v in days.values()),
	}
	manifest["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
	path = manifest_path(root, kind)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	tmp = path + ".tmp"
	with open(tmp, "w", encoding="utf-8") as f:
		json.dump(manifest, f, indent=2)
		f.write("\n")
	os.replace(tmp, path)
