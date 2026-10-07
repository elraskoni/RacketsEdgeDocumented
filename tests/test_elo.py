"""Elo rating engine (libs/ml/elo.py): defaults, surfaces, tunable options.

Run from repo root:  python -m pytest tests/test_elo.py -v
"""
import unittest

from libs.ml.elo import EloBook, EloParams, expected


class Elo(unittest.TestCase):
	def test_default_params_match_the_served_ratings(self):
		# Default parameters: the classic update, K = 24 from 1500 (as scripts/load_racketedge.py)
		book = EloBook()
		book.record(1, 2, "grass", "outdoor", "main")
		self.assertAlmostEqual(book.raw(1, "overall"), 1512.0)
		self.assertAlmostEqual(book.raw(2, "grass"), 1488.0)
		self.assertEqual(book.n(1, "clay"), 0)

	def test_indoor_hard_updates_hard_and_indoor(self):
		book = EloBook()
		book.record(1, 2, "hard", "indoor", "main")
		self.assertEqual((book.n(1, "hard"), book.n(1, "indoor"), book.n(1, "clay")), (1, 1, 0))

	def test_blending_pulls_thin_surface_ratings_towards_overall(self):
		book = EloBook(EloParams(blend_n=10))
		for _ in range(20):
			book.record(1, 2, "hard", "outdoor", "main")
		book.record(1, 2, "grass", "outdoor", "main")
		self.assertGreater(book.rating(1, "grass"), book.raw(1, "grass"))
		self.assertLess(book.rating(1, "grass"), book.raw(1, "overall"))

	def test_surface_transfer_moves_other_surfaces_less(self):
		book = EloBook(EloParams(surface_transfer=0.5))
		book.record(1, 2, "clay", "outdoor", "main")
		clay, grass = book.raw(1, "clay") - 1500, book.raw(1, "grass") - 1500
		self.assertAlmostEqual(grass, clay * 0.5)
		self.assertEqual((book.n(1, "clay"), book.n(1, "grass")), (1, 0))  # counts only for surfaces played

	def test_no_transfer_keeps_surfaces_independent(self):
		book = EloBook()
		book.record(1, 2, "clay", "outdoor", "main")
		self.assertEqual(book.raw(1, "grass"), 1500.0)

	def test_tier_and_retirement_weights_scale_k(self):
		book = EloBook(EloParams(tier_weight={"lower": 0.5}, retired_weight=0.5))
		book.record(1, 2, None, None, "lower")
		self.assertAlmostEqual(book.raw(1, "overall"), 1506.0)
		book.record(3, 4, None, None, "main", "retired")
		self.assertAlmostEqual(book.raw(3, "overall"), 1506.0)

	def test_start_rating_by_first_tier(self):
		book = EloBook(EloParams(start_by_tier={"lower": 1400.0}))
		book.record(1, 2, None, None, "lower")
		book.record(3, 1, None, None, "main")
		self.assertLess(book.raw(2, "overall"), 1400.0)
		self.assertEqual(book.starts[3], 1500.0)

	def test_fixed_surface_weight(self):
		book = EloBook(EloParams(surface_weight=0.5))
		book.record(1, 2, "grass", "outdoor", "main")
		book.record(1, 2, "hard", "outdoor", "main")
		self.assertAlmostEqual(book.rating(1, "grass"), 0.5 * book.raw(1, "grass") + 0.5 * book.raw(1, "overall"))

	def test_expected_score(self):
		self.assertAlmostEqual(expected(1600, 1400), 1 / (1 + 10 ** (-0.5)))


if __name__ == "__main__":
	unittest.main()
