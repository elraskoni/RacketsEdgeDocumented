"""Exact Markov win probabilities for tennis singles, vectorised over many states at once.

Model: points are independent; player A wins a point on A's serve with probability pa, player B wins a
point on B's serve with probability pb (so A wins a return point with 1 - pb).

Rules: advantage games; sets to 6 with a 7-point tiebreak at 6-6; best of 3 or 5 sets; optionally the
deciding set replaced by a 10-point match tiebreak. Serve alternates every game, the tiebreak counts as one
game for the rotation, and inside a tiebreak serve changes after the first point and then every two points.

All functions take numpy arrays (pa, pb of equal shape) and return arrays of the same shape, so a few
hundred array operations price millions of states. States are game starts (the score before the first
point of a game), or at any point inside a game (point_win_prob). It is the engine behind the live
win-probability endpoint and the pre-registered in-play test described in docs/machine_learning.md.
"""
from functools import lru_cache
from typing import Dict, Tuple

import numpy as np

A, B = 0, 1


def game_win(p: np.ndarray) -> np.ndarray:
	"""P(server wins an advantage game from 0-0), server wins each point with probability p."""
	q = 1.0 - p
	deuce = p * p / (p * p + q * q)
	return p ** 4 * (1 + 4 * q + 10 * q * q) + 20 * p ** 3 * q ** 3 * deuce


def _tb_server(n: int, first: int) -> int:
	"""Server of point n (0-based) in a tiebreak whose first point is served by `first`."""
	return first if ((n + 1) // 2) % 2 == 0 else 1 - first


def tiebreak_win(pa: np.ndarray, pb: np.ndarray, first: int, target: int = 7) -> np.ndarray:
	"""P(A wins a tiebreak to `target` (win by 2) from 0-0; `first` serves the first point."""
	def a_point(n: int) -> np.ndarray:
		return pa if _tb_server(n, first) == A else 1.0 - pb

	memo: Dict[Tuple[int, int], np.ndarray] = {}

	def win(a: int, b: int) -> np.ndarray:
		if a >= target and a - b >= 2:
			return np.ones_like(pa)
		if b >= target and b - a >= 2:
			return np.zeros_like(pa)
		if a == b and a >= target - 1:
			# Tied late: two points (one served by each player) decide or return to a tie
			n = a + b
			x, y = a_point(n) * a_point(n + 1), (1 - a_point(n)) * (1 - a_point(n + 1))
			return x / (x + y)
		key = (a, b)
		if key not in memo:
			w = a_point(a + b)
			memo[key] = w * win(a + 1, b) + (1 - w) * win(a, b + 1)
		return memo[key]

	return win(0, 0)


def _set_outcomes(pa: np.ndarray, pb: np.ndarray):
	"""For every game-start state of a set, the probabilities of (A wins set, B wins set) split by who
	serves the first game of the next set. Returns f(ga, gb, server) -> 4 arrays
	[A & next A, A & next B, B & next A, B & next B]."""
	ha, hb = game_win(pa), game_win(pb)
	tb = {A: tiebreak_win(pa, pb, A), B: tiebreak_win(pa, pb, B)}
	one, zero = np.ones_like(pa), np.zeros_like(pa)
	memo: Dict[Tuple[int, int, int], Tuple[np.ndarray, ...]] = {}

	def f(ga: int, gb: int, srv: int):
		if (ga >= 6 and ga - gb >= 2) or ga == 7:
			return (one, zero, zero, zero) if srv == A else (zero, one, zero, zero)
		if (gb >= 6 and gb - ga >= 2) or gb == 7:
			return (zero, zero, one, zero) if srv == A else (zero, zero, zero, one)
		key = (ga, gb, srv)
		if key in memo:
			return memo[key]
		if ga == 6 and gb == 6:
			t = tb[srv]
			nxt = 1 - srv  # the tiebreak counts as one game for the serve rotation
			out = (t, zero, 1 - t, zero) if nxt == A else (zero, t, zero, 1 - t)
		else:
			w = ha if srv == A else 1.0 - hb  # P(A wins this game)
			win_a, win_b = f(ga + 1, gb, 1 - srv), f(ga, gb + 1, 1 - srv)
			out = tuple(w * x + (1 - w) * y for x, y in zip(win_a, win_b))
		memo[key] = out
		return out

	return f


def match_win_prob(pa, pb, sets_a, sets_b, games_a, games_b, server, best_of=3, match_tiebreak=False):
	"""P(A wins the match) at a game start.

	Every argument is an array (or scalar broadcast) of the same length: serve-point probabilities, sets
	won, games in the current set, server of the game about to start (0 = A, 1 = B), best of 3/5, and
	whether the deciding set is a 10-point match tiebreak. At a deciding-set match tiebreak the state is
	0-0 in games and `server` serves its first point.
	"""
	pa, pb = np.asarray(pa, dtype=float), np.asarray(pb, dtype=float)
	n = pa.shape[0]
	cols = [np.broadcast_to(np.asarray(x), (n,)) for x in (sets_a, sets_b, games_a, games_b, server, best_of, match_tiebreak)]
	sets_a, sets_b, games_a, games_b, server, best_of, match_tiebreak = cols
	out = np.full(n, np.nan)
	for bo in np.unique(best_of):
		for mtb in np.unique(match_tiebreak):
			sel = (best_of == bo) & (match_tiebreak == mtb)
			if sel.any():
				out[sel] = _match(pa[sel], pb[sel], sets_a[sel], sets_b[sel], games_a[sel], games_b[sel], server[sel], int(bo), bool(mtb))
	return out


def _match(pa, pb, sets_a, sets_b, games_a, games_b, server, best_of, mtb):
	need = best_of // 2 + 1
	f = _set_outcomes(pa, pb)
	mtb_win = {A: tiebreak_win(pa, pb, A, 10), B: tiebreak_win(pa, pb, B, 10)} if mtb else None
	one, zero = np.ones_like(pa), np.zeros_like(pa)

	@lru_cache(maxsize=None)
	def m(sa: int, sb: int, first: int) -> np.ndarray:
		"""P(A wins the match) at the start of a set with score sa-sb, `first` serving its first game."""
		if sa >= need:
			return one
		if sb >= need:
			return zero
		if mtb and sa == sb == need - 1:
			return mtb_win[first]
		aa, ab, ba, bb = f(0, 0, first)
		return aa * m(sa + 1, sb, A) + ab * m(sa + 1, sb, B) + ba * m(sa, sb + 1, A) + bb * m(sa, sb + 1, B)

	out = np.empty_like(pa)
	keys = np.stack([sets_a, sets_b, games_a, games_b, server], axis=1).astype(int)
	uniq, inverse = np.unique(keys, axis=0, return_inverse=True)
	order = np.argsort(inverse.ravel(), kind="stable")
	bounds = np.searchsorted(inverse.ravel()[order], np.arange(len(uniq) + 1))
	for i, key in enumerate(uniq):
		sa, sb, ga, gb, srv = (int(x) for x in key)
		idx = order[bounds[i]:bounds[i + 1]]  # rows with this score; arithmetic only on them
		if mtb and sa == sb == need - 1:
			out[idx] = mtb_win[srv][idx]
			continue
		aa, ab, ba, bb = f(ga, gb, srv)
		out[idx] = (aa[idx] * m(sa + 1, sb, A)[idx] + ab[idx] * m(sa + 1, sb, B)[idx]
					+ ba[idx] * m(sa, sb + 1, A)[idx] + bb[idx] * m(sa, sb + 1, B)[idx])
	return out


def implied_serve_probs(p_match, mu, best_of=3, match_tiebreak=False, iters=40):
	"""Split a pre-match win probability into serve-point probabilities pa = mu + d/2, pb = mu - d/2,
	solving for d by bisection so that the Markov match probability at 0-0 equals p_match."""
	p_match = np.asarray(p_match, dtype=float)
	mu = np.broadcast_to(np.asarray(mu, dtype=float), p_match.shape)
	lo, hi = np.full_like(p_match, -0.6), np.full_like(p_match, 0.6)
	lim = 2 * np.minimum(mu - 0.01, 0.99 - mu)
	lo, hi = np.maximum(lo, -lim), np.minimum(hi, lim)
	zeros = np.zeros_like(p_match)
	for _ in range(iters):
		mid = (lo + hi) / 2
		# Average over who serves first: the coin toss is not known before the match
		pm = 0.5 * (match_win_prob(mu + mid / 2, mu - mid / 2, zeros, zeros, zeros, zeros, 0, best_of, match_tiebreak)
					+ match_win_prob(mu + mid / 2, mu - mid / 2, zeros, zeros, zeros, zeros, 1, best_of, match_tiebreak))
		up = pm < p_match
		lo, hi = np.where(up, mid, lo), np.where(up, hi, mid)
	d = (lo + hi) / 2
	return mu + d / 2, mu - d / 2


# ---------------------------------------------------------------------------
# Point level (live win probability): any score inside a game or tiebreak
# ---------------------------------------------------------------------------

def game_win_from(p: np.ndarray, a, b) -> np.ndarray:
	"""P(server wins the game) from server points a, receiver points b (0, 1, 2, 3, 4... as points won)."""
	p = np.asarray(p, dtype=float)
	a, b = (np.broadcast_to(np.asarray(x), p.shape).astype(int) for x in (a, b))
	# Deuce and advantage repeat: map every score with both on 3+ to 3-3, 4-3 or 3-4
	both = (a >= 3) & (b >= 3)
	diff = np.clip(a - b, -1, 1)
	a = np.where(both, 3 + (diff > 0), a)
	b = np.where(both, 3 + (diff < 0), b)
	out = np.empty_like(p)
	for ka, kb in set(zip(a.ravel().tolist(), b.ravel().tolist())):
		sel = (a == ka) & (b == kb)
		out[sel] = _game_from(p[sel], ka, kb)
	return out


def _game_from(p: np.ndarray, a: int, b: int) -> np.ndarray:
	q = 1.0 - p
	if a >= 4 and a - b >= 2:
		return np.ones_like(p)
	if b >= 4 and b - a >= 2:
		return np.zeros_like(p)
	if a >= 3 and b >= 3:
		deuce = p * p / (p * p + q * q)
		return deuce if a == b else (p + q * deuce if a > b else p * deuce)
	return p * _game_from(p, a + 1, b) + q * _game_from(p, a, b + 1)


def tiebreak_win_from(pa: np.ndarray, pb: np.ndarray, first, a, b, target) -> np.ndarray:
	"""P(A wins the tiebreak) from points a (A) - b (B); `first` (0 = A) served the tiebreak's first point."""
	pa, pb = np.asarray(pa, dtype=float), np.asarray(pb, dtype=float)
	first, a, b, target = (np.broadcast_to(np.asarray(x), pa.shape).astype(int) for x in (first, a, b, target))
	# Long tiebreaks repeat every 4 points (same servers): bring them back near the target
	excess = np.maximum(np.minimum(a, b) - target, 0)
	shift = 2 * ((excess + 1) // 2)
	a, b = a - shift, b - shift
	out = np.empty_like(pa)
	keys = np.stack([first, a, b, target], axis=1)
	for key in np.unique(keys, axis=0):
		f, ka, kb, t = (int(x) for x in key)
		sel = np.all(keys == key, axis=1)
		out[sel] = _tiebreak_from(pa[sel], pb[sel], f, ka, kb, t)
	return out


def _tiebreak_from(pa, pb, first, a0, b0, target):
	def a_point(n):
		return pa if _tb_server(n, first) == A else 1.0 - pb

	memo: Dict[Tuple[int, int], np.ndarray] = {}

	def win(a, b):
		if a >= target and a - b >= 2:
			return np.ones_like(pa)
		if b >= target and b - a >= 2:
			return np.zeros_like(pa)
		if a == b and a >= target - 1:
			n = a + b
			x, y = a_point(n) * a_point(n + 1), (1 - a_point(n)) * (1 - a_point(n + 1))
			return x / (x + y)
		if (a, b) not in memo:
			w = a_point(a + b)
			memo[(a, b)] = w * win(a + 1, b) + (1 - w) * win(a, b + 1)
		return memo[(a, b)]

	return win(a0, b0)


def after_game(sets_a, sets_b, games_a, games_b, server, best_of, match_tiebreak, a_wins: bool):
	"""Score after the current game (or tiebreak) is won by A (a_wins) or B. Returns
	(terminal: 1 / 0 / nan, sets_a, sets_b, games_a, games_b, next server)."""
	sa, sb, ga, gb, srv = (np.asarray(x).astype(int) for x in (sets_a, sets_b, games_a, games_b, server))
	need = np.asarray(best_of).astype(int) // 2 + 1
	mtb_state = np.asarray(match_tiebreak, dtype=bool) & (sa == need - 1) & (sb == need - 1) & (ga == 0) & (gb == 0)
	tb = (ga == 6) & (gb == 6)
	g1, g2 = (ga + 1, gb) if a_wins else (ga, gb + 1)
	won_a = (tb & a_wins) | (~tb & (((g1 >= 6) & (g1 - g2 >= 2)) | (g1 == 7)))
	won_b = (tb & (not a_wins)) | (~tb & (((g2 >= 6) & (g2 - g1 >= 2)) | (g2 == 7)))
	nsa, nsb = sa + won_a, sb + won_b
	over = won_a | won_b
	term = np.full(sa.shape, np.nan)
	term[nsa >= need] = 1.0
	term[nsb >= need] = 0.0
	term[mtb_state] = 1.0 if a_wins else 0.0
	return term, nsa, nsb, np.where(over, 0, g1), np.where(over, 0, g2), 1 - srv


def point_win_prob(pa, pb, sets_a, sets_b, games_a, games_b, server, points_a, points_b, best_of=3, match_tiebreak=False):
	"""P(A wins the match) at any point: the score in sets and games, the game's server (for a tiebreak:
	who served its first point) and the points won in the current game or tiebreak. At 0-0 points this
	equals match_win_prob."""
	pa, pb = np.asarray(pa, dtype=float), np.asarray(pb, dtype=float)
	n = pa.shape[0]
	cols = [np.broadcast_to(np.asarray(x), (n,)) for x in (sets_a, sets_b, games_a, games_b, server, points_a, points_b, best_of, match_tiebreak)]
	sa, sb, ga, gb, srv, pta, ptb, bo, mtb = cols
	need = bo.astype(int) // 2 + 1
	in_mtb = mtb.astype(bool) & (sa == need - 1) & (sb == need - 1) & (ga == 0) & (gb == 0)
	in_tb = ((ga == 6) & (gb == 6)) | in_mtb
	# P(A wins the current game / tiebreak)
	g = np.empty(n)
	reg = ~in_tb
	if reg.any():
		a_srv = reg & (srv == A)
		b_srv = reg & (srv == B)
		if a_srv.any():
			g[a_srv] = game_win_from(pa[a_srv], pta[a_srv], ptb[a_srv])
		if b_srv.any():
			g[b_srv] = 1.0 - game_win_from(pb[b_srv], ptb[b_srv], pta[b_srv])
	if in_tb.any():
		g[in_tb] = tiebreak_win_from(pa[in_tb], pb[in_tb], srv[in_tb], pta[in_tb], ptb[in_tb], np.where(in_mtb[in_tb], 10, 7))
	vals = []
	for a_wins in (True, False):
		term, *st = after_game(sa, sb, ga, gb, srv, bo, mtb, a_wins)
		v = term.copy()
		live = np.isnan(term)
		if live.any():
			v[live] = match_win_prob(pa[live], pb[live], *(x[live] for x in st), best_of=bo[live], match_tiebreak=mtb[live])
		vals.append(v)
	return g * vals[0] + (1 - g) * vals[1]
