"""Synthetic source: the match simulator follows tennis rules, and the adapter validates its feed.

Run from repo root:  python -m pytest tests/test_synthetic_source.py -v
"""
import copy
import random
import unittest
from datetime import datetime, timedelta, timezone

from libs.parsers.synthetic import POINT_COLUMNS, parse_match
from libs.synthetic.simulator import make_players, season, simulate_match


def _matches(n=60, best_of=3, seed=3):
	rng = random.Random(seed)
	players = make_players(rng, 20)
	return [simulate_match(rng, players[i % 20], players[(i + 1) % 20], "hard", best_of, retire_p=0.0) for i in range(n)]


class Simulator(unittest.TestCase):
	def test_every_set_follows_the_scoring_rules(self):
		for m in _matches():
			for s in m["sets"]:
				hi, lo = max(s["home"], s["away"]), min(s["home"], s["away"])
				self.assertTrue((hi == 6 and lo <= 4) or (hi == 7 and lo in (5, 6)), s)
				if hi == 7 and lo == 6:
					tb = (s["tb_home"], s["tb_away"])
					self.assertTrue(max(tb) >= 7 and abs(tb[0] - tb[1]) >= 2, tb)

	def test_match_length_respects_best_of(self):
		for best_of, need in ((3, 2), (5, 3)):
			for m in _matches(30, best_of):
				won = {"home": sum(s["home"] > s["away"] for s in m["sets"]), "away": sum(s["away"] > s["home"] for s in m["sets"])}
				self.assertEqual(won[m["winner"]], need)
				self.assertLess(won["away" if m["winner"] == "home" else "home"], need)

	def test_statistics_add_up(self):
		for m in _matches():
			self.assertEqual(sum(m["stats"][s]["points_won"] for s in ("home", "away")), len(m["points"]))
			for s in ("home", "away"):
				st = m["stats"][s]
				self.assertEqual(st["first_serves_total"], st["first_serve_points_total"] + st["second_serve_points_total"])
				self.assertEqual(st["service_points_won"], st["first_serve_points_won"] + st["second_serve_points_won"])
				self.assertLessEqual(st["aces"], st["first_serve_points_won"])
				self.assertLessEqual(st["bp_saved"], st["bp_faced"])

	def test_tiebreak_serve_rotation(self):
		# First point by one player, then two points each, alternating
		for m in _matches(120):
			tb = [p for p in m["points"] if p["tiebreak"]]
			for set_no in {p["set"] for p in tb}:
				pts = [p for p in tb if p["set"] == set_no]
				first = pts[0]["server"]
				other = "away" if first == "home" else "home"
				for i, p in enumerate(pts):
					expected = first if i == 0 or ((i - 1) // 2) % 2 == 1 else other
					self.assertEqual(p["server"], expected, (set_no, i))


class Adapter(unittest.TestCase):
	def setUp(self):
		rng = random.Random(11)
		players = make_players(rng, 40)
		now = datetime.now(timezone.utc)
		self.payloads = season(rng, players, (now - timedelta(days=14)).date(), now.date(), now)

	def test_simulated_feed_parses_cleanly(self):
		for p in self.payloads:
			out = parse_match(p["event"], p["statistics"], p["pbp"])
			errors = [i for i in out["issues"] if i["severity"] == "error"]
			self.assertEqual(errors, [], p["event"]["id"])
			if p["pbp"] and out["match"]["outcome"] == "completed":
				self.assertEqual(out["match"]["pbp_status"], "complete")
				self.assertEqual(set(out["points"][0]), set(POINT_COLUMNS))

	def test_points_rebuild_the_official_score(self):
		p = next(p for p in self.payloads if p["pbp"] and p["event"]["status"] == "finished")
		out = parse_match(p["event"], p["statistics"], p["pbp"])
		for n, s in enumerate(p["event"]["sets"], 1):
			for side in ("home", "away"):
				games = sum(1 for r in out["points"] if r["set_no"] == n and r["game_winner"] == side)
				self.assertEqual(games, s[side])

	def test_findings_are_flagged_not_dropped(self):
		p = copy.deepcopy(next(p for p in self.payloads if p["statistics"] and p["event"]["status"] == "finished"))
		p["event"]["sets"][0]["home"] = 9                       # impossible set score
		p["event"]["away"]["id"] = p["event"]["home"]["id"]    # same player on both sides
		p["statistics"]["home"]["aces"] = 10_000               # more aces than service points
		out = parse_match(p["event"], p["statistics"], p["pbp"])
		checks = {i["check"] for i in out["issues"]}
		self.assertTrue({"invalid_set_score", "player_on_both_sides", "aces_le_service_points"} <= checks, checks)
		self.assertEqual(out["match"]["stats_status"], "suspect")
		self.assertEqual(out["match"]["event_id"], p["event"]["id"])  # the match is kept

	def test_scheduled_match_has_no_result(self):
		p = next((p for p in self.payloads if p["event"]["status"] == "scheduled"), None)
		if p is None:
			self.skipTest("no scheduled matches in this window")
		m = parse_match(p["event"], p["statistics"], p["pbp"])["match"]
		self.assertEqual((m["outcome"], m["winner_side"], m["has_stats"], m["pbp_status"]), ("scheduled", None, False, "none"))


if __name__ == "__main__":
	unittest.main()
