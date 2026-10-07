"""
Landing area for freshly fetched source payloads, and the fold into the raw archive.

	<root>/source/landing/{events,statistics,pbp}/YYYY-MM-DD/<event_id>.json

One file per event per kind, overwritten on every fetch (latest payload wins). The date is
the UTC date of the event's start, the same partition the archive uses. Each file holds a
complete archive line: {"event_id", "start_timestamp", "fetched_utc", <json column>: payload}.

fold() merges landing files into the archive day files (landing wins over the archive for
the same event), updates the manifests, and removes the folded files. Files rewritten
while a fold runs are kept for the next fold.
"""
import gzip
import json
import os
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterator, Optional, Set, Tuple

from . import archive

_TMP_SUFFIX = ".tmp"


def landing_root(root: str) -> str:
	return os.path.join(root, "source", "landing")


def _atomic_write(path: str, data: bytes) -> None:
	os.makedirs(os.path.dirname(path), exist_ok=True)
	tmp = path + _TMP_SUFFIX
	with open(tmp, "wb") as f:
		f.write(data)
	os.replace(tmp, path)


def write(root: str, kind: str, event_id: int, start_timestamp: int, payload: Dict[str, Any]) -> str:
	"""Save one payload to landing; returns the file path."""
	day = archive.utc_day(start_timestamp)
	record = {
		"event_id": int(event_id),
		"start_timestamp": int(start_timestamp),
		"fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		archive.json_column(kind): payload,
	}
	path = os.path.join(landing_root(root), kind, day.isoformat(), f"{int(event_id)}.json")
	_atomic_write(path, json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
	return path


def read(root: str, kind: str, day: date, event_id: int) -> Optional[Dict[str, Any]]:
	"""The landed payload for one event, or None."""
	path = os.path.join(landing_root(root), kind, day.isoformat(), f"{int(event_id)}.json")
	if not os.path.exists(path):
		return None
	with open(path, encoding="utf-8") as f:
		return json.load(f)[archive.json_column(kind)]


def iter_files(root: str, kind: str, modified_since: float = 0.0) -> Iterator[Tuple[str, float]]:
	"""(path, mtime) of landed files for a kind, optionally only those modified since a time."""
	base = os.path.join(landing_root(root), kind)
	if not os.path.isdir(base):
		return
	for day in sorted(os.listdir(base)):
		day_dir = os.path.join(base, day)
		if not os.path.isdir(day_dir):
			continue
		for name in sorted(os.listdir(day_dir)):
			if not name.endswith(".json"):
				continue
			path = os.path.join(day_dir, name)
			mtime = os.stat(path).st_mtime
			if mtime >= modified_since:
				yield path, mtime


def _read_day_file(path: str) -> Dict[int, str]:
	lines: Dict[int, str] = {}
	if os.path.exists(path):
		with gzip.open(path, "rt", encoding="utf-8") as f:
			for line in f:
				line = line.rstrip("\n")
				if line:
					lines[int(json.loads(line)["event_id"])] = line
	return lines


def fold(root: str, before: float) -> Set[Tuple[int, int]]:
	"""Merge landing files last modified before `before` into the archive. Returns affected (year, month)s."""
	affected: Set[Tuple[int, int]] = set()
	staging = os.path.join(root, "source", ".staging")
	for kind in archive.KINDS:
		base = os.path.join(landing_root(root), kind)
		if not os.path.isdir(base):
			continue
		manifest = archive.load_manifest(root, kind)
		for day_name in sorted(os.listdir(base)):
			day_dir = os.path.join(base, day_name)
			files = [
				(os.path.join(day_dir, n), os.stat(os.path.join(day_dir, n)).st_mtime)
				for n in sorted(os.listdir(day_dir)) if n.endswith(".json")
			]
			files = [(p, m) for p, m in files if m < before]
			if not files:
				continue
			day = date.fromisoformat(day_name)
			final = archive.day_path(root, kind, day)
			lines = _read_day_file(final)
			for path, _ in files:
				with open(path, encoding="utf-8") as f:
					rec = json.load(f)
				lines[int(rec["event_id"])] = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))

			os.makedirs(staging, exist_ok=True)
			tmp = os.path.join(staging, f"{kind}_{day_name}.jsonl.gz")
			with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
				for event_id in sorted(lines):
					f.write(lines[event_id] + "\n")
			os.makedirs(os.path.dirname(final), exist_ok=True)
			os.replace(tmp, final)
			manifest["days"][day_name] = {
				"file": archive.day_rel_path(day),
				"rows": len(lines),
				"bytes": os.path.getsize(final),
				"sha256": archive.sha256_file(final),
			}
			archive.save_manifest(root, kind, manifest)
			affected.add((day.year, day.month))

			# Remove what was folded; a file rewritten during the fold stays for next time
			for path, mtime in files:
				try:
					if os.stat(path).st_mtime == mtime:
						os.remove(path)
				except FileNotFoundError:
					pass
			if not os.listdir(day_dir):
				os.rmdir(day_dir)
	if os.path.isdir(staging) and not os.listdir(staging):
		os.rmdir(staging)
	return affected


def pending_counts(root: str) -> Dict[str, int]:
	return {kind: sum(1 for _ in iter_files(root, kind)) for kind in archive.KINDS}
