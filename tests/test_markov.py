"""Tests for libs/ml/markov.py against an independent point-by-point reference.

The reference below plays the rules literally (one point at a time, scalar, memoised on the full score)
and shares no code with the vectorised engine.
"""
import unittest
from functools import lru_cache

import numpy as np

from libs.ml import markov


def reference(pa, pb, best_of=3, mtb=False):
	"""Returns f(sets_a, sets_b, games_a, games_b, server) = P(A wins) at that game start."""
	need = best_of // 2 + 1

	def a_wins_point(server):
		return pa if server == 0 else 1 - pb

	@lru_cache(maxsize=None)
	def point(sa, sb, ga, gb, pa_, pb_, server, in_tb, tb_first, tb_target):
		# pa_/pb_ are points in the current game or tiebreak
		if in_tb:
			if pa_ >= tb_target and pa_ - pb_ >= 2:
				return end_set(sa + 1, sb, 1 - tb_first)
			if pb_ >= tb_target and pb_ - pa_ >= 2:
				return end_set(sa, sb + 1, 1 - tb_first)
			n = pa_ + pb_
			if pa_ == pb_ and pa_ >= tb_target - 1:
				# Late tie: the next two points (one per server) win it or return to the same tie
				s1 = tb_first if ((n + 1) // 2) % 2 == 0 else 1 - tb_first
				x, y = a_wins_point(s1) * a_wins_point(1 - s1), (1 - a_wins_point(s1)) * (1 - a_wins_point(1 - s1))
				return (x * end_set(sa + 1, sb, 1 - tb_first) + y * end_set(sa, sb + 1, 1 - tb_first)) / (x + y)
			srv = tb_first if ((n + 1) // 2) % 2 == 0 else 1 - tb_first
			w = a_wins_point(srv)
			return w * point(sa, sb, ga, gb, pa_ + 1, pb_, srv, True, tb_first, tb_target) + \
				(1 - w) * point(sa, sb, ga, gb, pa_, pb_ + 1, srv, True, tb_first, tb_target)
		if pa_ >= 4 and pa_ - pb_ >= 2:
			return game_over(sa, sb, ga + 1, gb, 1 - server)
		if pb_ >= 4 and pb_ - pa_ >= 2:
			return game_over(sa, sb, ga, gb + 1, 1 - server)
		if pa_ == pb_ and pa_ >= 3:
			# Deuce: two points in a row win the game, a split returns to deuce
			w = a_wins_point(server)
			x, y = w * w, (1 - w) * (1 - w)
			return (x * game_over(sa, sb, ga + 1, gb, 1 - server) + y * game_over(sa, sb, ga, gb + 1, 1 - server)) / (x + y)
		w = a_wins_point(server)
		return w * point(sa, sb, ga, gb, pa_ + 1, pb_, server, False, 0, 0) + \
			(1 - w) * point(sa, sb, ga, gb, pa_, pb_ + 1, server, False, 0, 0)

	def game_over(sa, sb, ga, gb, next_server):
		if (ga >= 6 and ga - gb >= 2) or ga == 7:
			return end_set(sa + 1, sb, next_server)
		if (gb >= 6 and gb - ga >= 2) or gb == 7:
			return end_set(sa, sb + 1, next_server)
		return start(sa, sb, ga, gb, next_server)

	def end_set(sa, sb, next_server):
		if sa == need:
			return 1.0
		if sb == need:
			return 0.0
		return start(sa, sb, 0, 0, next_server)

	def start(sa, sb, ga, gb, server):
		if mtb and sa == sb == need - 1:
			return point(sa, sb, 0, 0, 0, 0, server, True, server, 10)
		if ga == 6 and gb == 6:
			return point(sa, sb, 6, 6, 0, 0, server, True, server, 7)
		return point(sa, sb, ga, gb, 0, 0, server, False, 0, 0)

	start.point = point  # point-level access for the live win-probability tests
	return start


class Markov(unittest.TestCase):
	def test_game_known_values(self):
		self.assertAlmostEqual(float(markov.game_win(np.array([0.5]))[0]), 0.5, places=12)
		# Classic value: a 60% server holds 73.57% of games
		self.assertAlmostEqual(float(markov.game_win(np.array([0.6]))[0]), 0.735729, places=5)

	def test_symmetry(self):
		p = np.array([0.62, 0.55])
		for bo in (3, 5):
			v = markov.match_win_prob(p, p, 0, 0, 0, 0, np.array([0, 1]), bo)
			np.testing.assert_allclose(v, 0.5, atol=0.02)  # serving first is a tiny edge, not zero
			both = 0.5 * (markov.match_win_prob(p, p, 0, 0, 0, 0, 0, bo) + markov.match_win_prob(p, p, 0, 0, 0, 0, 1, bo))
			np.testing.assert_allclose(both, 0.5, atol=1e-9)

	def test_matches_reference_on_many_states(self):
		rng = np.random.default_rng(3)
		cases = [(3, False), (5, False), (3, True)]
		for bo, mtb in cases:
			need = bo // 2 + 1
			for pa, pb in ((0.64, 0.58), (0.70, 0.66), (0.55, 0.61)):
				ref = reference(pa, pb, bo, mtb)
				states = []
				for _ in range(60):
					sa, sb = rng.integers(0, need), rng.integers(0, need)
					ga, gb = rng.integers(0, 7), rng.integers(0, 7)
					if ga == 6 and gb < 5 or gb == 6 and ga < 5:
						continue  # set already over
					if mtb and sa == sb == need - 1:
						ga = gb = 0
					states.append((sa, sb, ga, gb, rng.integers(0, 2)))
				arr = np.array(states)
				got = markov.match_win_prob(np.full(len(arr), pa), np.full(len(arr), pb), *arr.T, best_of=bo, match_tiebreak=mtb)
				want = [ref(*map(int, s)) for s in states]
				np.testing.assert_allclose(got, want, atol=1e-9, err_msg=f"bo{bo} mtb={mtb} pa={pa} pb={pb}")

	def test_point_level_matches_reference(self):
		rng = np.random.default_rng(9)
		for bo, mtb in ((3, False), (5, False), (3, True)):
			need = bo // 2 + 1
			for pa, pb in ((0.64, 0.58), (0.55, 0.61)):
				ref = reference(pa, pb, bo, mtb)
				states, want = [], []
				for _ in range(80):
					sa, sb = int(rng.integers(0, need)), int(rng.integers(0, need))
					ga, gb = int(rng.integers(0, 7)), int(rng.integers(0, 7))
					if ga == 6 and gb < 5 or gb == 6 and ga < 5:
						continue
					srv = int(rng.integers(0, 2))
					in_mtb = mtb and sa == sb == need - 1
					if in_mtb:
						ga = gb = 0
					tb = in_mtb or (ga == 6 and gb == 6)
					if tb:
						pt_a, pt_b = int(rng.integers(0, 14)), int(rng.integers(0, 14))
						target = 10 if in_mtb else 7
						if (pt_a >= target and pt_a - pt_b >= 2) or (pt_b >= target and pt_b - pt_a >= 2):
							continue  # tiebreak already over
						want.append(ref.point(sa, sb, ga, gb, pt_a, pt_b, srv, True, srv, target))
					else:
						pt_a, pt_b = int(rng.integers(0, 6)), int(rng.integers(0, 6))
						if (pt_a >= 4 and pt_a - pt_b >= 2) or (pt_b >= 4 and pt_b - pt_a >= 2):
							continue  # game already over
						if pt_a >= 3 and pt_b >= 3 and abs(pt_a - pt_b) > 1:
							continue
						want.append(ref.point(sa, sb, ga, gb, pt_a, pt_b, srv, False, 0, 0))
					states.append((sa, sb, ga, gb, srv, pt_a, pt_b))
				arr = np.array(states)
				got = markov.point_win_prob(np.full(len(arr), pa), np.full(len(arr), pb), *arr.T, best_of=bo, match_tiebreak=mtb)
				np.testing.assert_allclose(got, want, atol=1e-9, err_msg=f"bo{bo} mtb={mtb}")

	def test_point_level_at_game_start_equals_game_level(self):
		p = np.array([0.63, 0.6, 0.66])
		q = np.array([0.6, 0.64, 0.58])
		args = (np.array([1, 0, 1]), np.array([0, 1, 1]), np.array([3, 6, 0]), np.array([2, 6, 0]), np.array([1, 0, 0]))
		np.testing.assert_allclose(markov.point_win_prob(p, q, *args, 0, 0, 3, True), markov.match_win_prob(p, q, *args, 3, True), atol=1e-12)

	def test_implied_serve_probs_reproduce_prematch(self):
		target = np.array([0.2, 0.5, 0.73, 0.9])
		pa, pb = markov.implied_serve_probs(target, 0.62)
		z = np.zeros(4)
		back = 0.5 * (markov.match_win_prob(pa, pb, z, z, z, z, 0) + markov.match_win_prob(pa, pb, z, z, z, z, 1))
		np.testing.assert_allclose(back, target, atol=1e-6)
		np.testing.assert_allclose((pa + pb) / 2, 0.62)


if __name__ == "__main__":
	unittest.main()
