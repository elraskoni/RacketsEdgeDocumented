"""Elo ratings with tunable parameters (overall + per-surface scopes).

The default parameters reproduce the ratings served today (scripts/load_racketedge.py: start 1500,
K = 24, every tier equal, surface scopes independent). Phase 2e step 2 tunes them
on a held-out season.

Surface handling:
  - every match updates the overall rating and its own surface rating (indoor hard: hard + indoor)
  - surface_transfer > 0: it also updates the *other* surface ratings with K * surface_transfer,
    because a clay result says something, but less, about grass ability
  - the rating used to predict a match on a surface blends that surface's rating with the overall
    rating: fixed weight (surface_weight) or growing with surface experience (blend_n)
"""
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

SURFACE_SCOPES = ("hard", "clay", "grass", "indoor")


@dataclass(frozen=True)
class EloParams:
	start: float = 1500.0
	k: float = 24.0
	# Match-count-dependent K (FiveThirtyEight style): K = k_scale / (n + k_offset) ** k_shape when k_scale is set
	k_scale: Optional[float] = None
	k_offset: float = 5.0
	k_shape: float = 0.4
	# Multiplier on K per tier (main / second / lower / team / other); missing tiers use 1.0
	tier_weight: Dict[str, float] = field(default_factory=dict)
	# Multiplier on K for retirements / defaults (a partial match says less)
	retired_weight: float = 1.0
	# Starting rating by the tier of a player's first match (missing tiers use start)
	start_by_tier: Dict[str, float] = field(default_factory=dict)
	# Share of K applied to the other surfaces' ratings (0 = surfaces independent)
	surface_transfer: float = 0.0
	# Prediction rating on a surface = w * surface + (1 - w) * overall, with
	#   w = n_surface / (n_surface + blend_n)  when blend_n is set, else
	#   w = surface_weight                     (1.0 = surface rating only, as served today)
	blend_n: Optional[float] = None
	surface_weight: float = 1.0

	def __hash__(self) -> int:
		return hash(repr(self))


# The tuned parameter set used in production is private; EloParams() reproduces the original
# fixed-K ratings, and every option below can be tuned with a held-out season.


def expected(r_a: float, r_b: float) -> float:
	return 1.0 / (1.0 + 10.0 ** ((r_b - r_a) / 400.0))


def match_scopes(surface: Optional[str], environment: Optional[str]) -> Tuple[str, ...]:
	scopes = ["overall"]
	if surface in ("hard", "clay", "grass"):
		scopes.append(surface)
	if surface == "hard" and environment == "indoor":
		scopes.append("indoor")
	return tuple(scopes)


def surface_scope(surface: Optional[str], environment: Optional[str]) -> Optional[str]:
	"""The most specific surface scope of a match (indoor hard -> indoor), or None."""
	scopes = match_scopes(surface, environment)
	return scopes[-1] if len(scopes) > 1 else None


class EloBook:
	"""Ratings and match counts per player and scope. Read with rating() / predict(); update with record()."""

	def __init__(self, params: EloParams = EloParams()):
		self.p = params
		self.ratings: Dict[str, Dict[int, float]] = {"overall": {}, **{s: {} for s in SURFACE_SCOPES}}
		self.counts: Dict[str, Dict[int, int]] = {"overall": {}, **{s: {} for s in SURFACE_SCOPES}}
		self.starts: Dict[int, float] = {}

	def raw(self, player: int, scope: str) -> float:
		r = self.ratings[scope].get(player)
		return r if r is not None else self.starts.get(player, self.p.start)

	def n(self, player: int, scope: str) -> int:
		return self.counts[scope].get(player, 0)

	def rating(self, player: int, scope: Optional[str]) -> float:
		"""Overall rating, or the rating used for a match on `scope` (surface blended with overall)."""
		if scope is None or scope == "overall":
			return self.raw(player, "overall")
		if self.p.blend_n is not None:
			n = self.n(player, scope)
			w = n / (n + self.p.blend_n)
		else:
			w = self.p.surface_weight
		if w >= 1.0:
			return self.raw(player, scope)
		return w * self.raw(player, scope) + (1.0 - w) * self.raw(player, "overall")

	def predict(self, a: int, b: int, scope: Optional[str]) -> float:
		"""P(a beats b) on a surface scope (None = overall)."""
		return expected(self.rating(a, scope), self.rating(b, scope))

	def _k(self, player: int, scope: str, weight: float) -> float:
		k = self.p.k if self.p.k_scale is None else self.p.k_scale / (self.n(player, scope) + self.p.k_offset) ** self.p.k_shape
		return k * weight

	def record(self, winner: int, loser: int, surface: Optional[str], environment: Optional[str], tier: Optional[str],
			outcome: Optional[str] = None) -> None:
		for pid in (winner, loser):
			if pid not in self.starts and self.p.start_by_tier:
				self.starts[pid] = self.p.start_by_tier.get(tier or "", self.p.start)
		weight = self.p.tier_weight.get(tier or "", 1.0)
		if outcome in ("retired", "defaulted"):
			weight *= self.p.retired_weight
		own = match_scopes(surface, environment)
		updates = [(scope, weight) for scope in own]
		if self.p.surface_transfer > 0 and len(own) > 1:
			updates += [(scope, weight * self.p.surface_transfer) for scope in SURFACE_SCOPES if scope not in own]
		for scope, w in updates:
			rw, rl = self.raw(winner, scope), self.raw(loser, scope)
			e = expected(rw, rl)
			self.ratings[scope][winner] = rw + self._k(winner, scope, w) * (1.0 - e)
			self.ratings[scope][loser] = rl - self._k(loser, scope, w) * (1.0 - e)
			if scope in own:  # counts = matches actually played in that scope
				self.counts[scope][winner] = self.n(winner, scope) + 1
				self.counts[scope][loser] = self.n(loser, scope) + 1
