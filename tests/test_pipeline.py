"""Raw-first landing and the fold into the day-partitioned raw archive (no database, no network).

Run from repo root:  python -m pytest tests/test_pipeline.py -v
"""
import gzip
import json
import os
import shutil
import tempfile
import time
import unittest

from libs.racketedge import archive, landing

EVENT = {"id": 4_000_001, "start_timestamp": 1_767_225_600, "status": "scheduled", "home": {"id": 1}, "away": {"id": 2}}


def _day_lines(root, kind, day):
	with gzip.open(archive.day_path(root, kind, day), "rt", encoding="utf-8") as f:
		return [json.loads(line) for line in f]


class LandingFold(unittest.TestCase):
	def setUp(self):
		self.root = tempfile.mkdtemp(prefix="re_landing_")
		self.day = archive.utc_day(EVENT["start_timestamp"])

	def tearDown(self):
		shutil.rmtree(self.root, ignore_errors=True)

	def test_write_then_fold_creates_day_file_and_manifest(self):
		landing.write(self.root, "events", EVENT["id"], EVENT["start_timestamp"], EVENT)
		affected = landing.fold(self.root, before=time.time() + 1)
		self.assertEqual(affected, {(self.day.year, self.day.month)})
		lines = _day_lines(self.root, "events", self.day)
		self.assertEqual([r["event_id"] for r in lines], [EVENT["id"]])
		self.assertEqual(lines[0][archive.json_column("events")], EVENT)
		entry = archive.load_manifest(self.root, "events")["days"][self.day.isoformat()]
		self.assertEqual(entry["rows"], 1)
		self.assertEqual(entry["sha256"], archive.sha256_file(archive.day_path(self.root, "events", self.day)))
		self.assertTrue(all(v == 0 for v in landing.pending_counts(self.root).values()))

	def test_latest_payload_wins_and_other_events_are_kept(self):
		landing.write(self.root, "events", 1, EVENT["start_timestamp"], dict(EVENT, id=1))
		landing.write(self.root, "events", EVENT["id"], EVENT["start_timestamp"], EVENT)
		landing.fold(self.root, before=time.time() + 1)
		landing.write(self.root, "events", EVENT["id"], EVENT["start_timestamp"], dict(EVENT, status="finished", winner="home"))
		landing.fold(self.root, before=time.time() + 1)
		lines = {r["event_id"]: r[archive.json_column("events")] for r in _day_lines(self.root, "events", self.day)}
		self.assertEqual(set(lines), {1, EVENT["id"]})
		self.assertEqual(lines[EVENT["id"]]["winner"], "home")

	def test_files_written_after_the_fold_started_are_kept(self):
		landing.write(self.root, "events", EVENT["id"], EVENT["start_timestamp"], EVENT)
		affected = landing.fold(self.root, before=time.time() - 60)  # the fold "started" before the write
		self.assertEqual(affected, set())
		self.assertEqual(landing.pending_counts(self.root)["events"], 1)
		self.assertFalse(os.path.exists(archive.day_path(self.root, "events", self.day)))


if __name__ == "__main__":
	unittest.main()
