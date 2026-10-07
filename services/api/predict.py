"""
Match-winner prediction for /v1/tennis/predict (RacketEdge 3.0 tables).

Builds the feature vector the current model artifact expects from `player_summary`
(career + Elo) and `player_match_stats` (last five results). Player ids are public
RacketEdge ids.

"""
import os
from typing import Any, Dict, List, Optional, Tuple

try:
	from . import db as dbmod
except Exception:
	# when run as script
	import services.api.db as dbmod  # type: ignore

_ARTIFACT_PATH = os.getenv(
	"MATCH_WINNER_MODEL_PATH",
	os.path.join("backend", "storage", "models", "RacketEdgeModelV1.joblib"),
)

_MODEL_CACHE: Dict[str, Any] = {}

_CAREER_KEYS = ["matches_played", "wins", "sets_won", "games_won", "aces", "double_faults",
				"hold_pct", "return_games_won_pct", "bp_save_pct", "bp_convert_pct"]
_RECENT_KEYS = ["matches_played", "wins", "sets_won", "aces", "double_faults", "hold_pct", "return_games_won_pct"]


def _load_artifact() -> Dict[str, Any]:
	import joblib  # type: ignore
	if "artifact" not in _MODEL_CACHE:
		_MODEL_CACHE["artifact"] = joblib.load(_ARTIFACT_PATH)
	return _MODEL_CACHE["artifact"]


def _f(v: Any) -> Optional[float]:
	return float(v) if v is not None else None


def _career_and_elo(conn, player_id: int, surface: Optional[str]) -> Tuple[Dict[str, Optional[float]], Optional[float]]:
	"""Career aggregates and Elo for the surface scope, falling back to overall."""
	scope = surface if surface in ("hard", "clay", "grass", "indoor") else "overall"
	with dbmod.dict_cursor(conn) as cur:
		cur.execute("SELECT * FROM player_summary WHERE player_id = %s AND scope IN (%s, 'overall')", (player_id, scope))
		rows = {r["scope"]: r for r in dbmod.fetchall_dicts(cur)}
	row = rows.get(scope) or rows.get("overall") or {}
	elo = _f(row.get("elo")) if row.get("elo") is not None else _f((rows.get("overall") or {}).get("elo"))
	return {k: _f(row.get(k)) for k in _CAREER_KEYS}, elo


def _recent5(conn, player_id: int) -> Dict[str, Optional[float]]:
	with dbmod.dict_cursor(conn) as cur:
		cur.execute("""
			SELECT result, sets_won, aces, double_faults, service_games_played, service_games_won,
				return_games_played, return_games_won
			FROM player_match_stats
			WHERE player_id = %s AND outcome IN ('completed', 'retired', 'defaulted') AND result IS NOT NULL
			ORDER BY start_utc DESC, match_id DESC LIMIT 5
		""", (player_id,))
		rows = dbmod.fetchall_dicts(cur)
	if not rows:
		return {}
	tot = lambda c: sum(int(r[c] or 0) for r in rows)  # noqa: E731
	sgp, rgp = tot("service_games_played"), tot("return_games_played")
	return {
		"matches_played": float(len(rows)),
		"wins": float(sum(1 for r in rows if r["result"] == "won")),
		"sets_won": float(tot("sets_won")),
		"aces": float(tot("aces")),
		"double_faults": float(tot("double_faults")),
		"hold_pct": tot("service_games_won") / sgp if sgp else None,
		"return_games_won_pct": tot("return_games_won") / rgp if rgp else None,
	}


def _player_features(player_id: int, surface: Optional[str]) -> Dict[str, Any]:
	conn = dbmod.connect()
	try:
		career, elo = _career_and_elo(conn, player_id, surface)
		recent = _recent5(conn, player_id)
	finally:
		try:
			conn.close()
		except Exception:
			pass
	return {"career": career, "recent": recent, "elo": elo}


def _row(a: Dict[str, Any], b: Dict[str, Any], surface: Optional[str], feat_names: List[str]) -> List[float]:
	feat: Dict[str, float] = {}
	for prefix, group, keys in (("career_", "career", _CAREER_KEYS), ("recent5_", "recent", _RECENT_KEYS)):
		for k in keys:
			av, bv = a[group].get(k), b[group].get(k)
			feat[f"delta_{prefix}{k}"] = float(av) - float(bv) if av is not None and bv is not None else 0.0
	feat["delta_elo_overall"] = a["elo"] - b["elo"] if a["elo"] is not None and b["elo"] is not None else 0.0
	feat.update({"side_home": 1.0, "side_away": 0.0, "side_unknown": 0.0})
	feat["surface_unknown"] = 0.0 if surface in ("hard", "clay", "grass", "indoor") else 1.0
	return [float(feat.get(n, 0.0)) for n in feat_names]


def predict_two_players(player_a_id: int, player_b_id: int, surface: Optional[str]) -> Tuple[float, float, List[str]]:
	"""Return (p_a, p_b, feature_names). Averaged over both orderings so the answer
	does not depend on which player is passed first."""
	import pandas as pd
	artifact = _load_artifact()
	model = artifact.get("calibrator") or artifact.get("model")
	feat_names: List[str] = artifact["metadata"]["feature_names"]
	a, b = _player_features(player_a_id, surface), _player_features(player_b_id, surface)
	# Named columns, as the model was fitted with them
	X = pd.DataFrame([_row(a, b, surface, feat_names), _row(b, a, surface, feat_names)], columns=feat_names, dtype="float64")
	p_ab, p_ba = model.predict_proba(X)[:, 1]
	p_a = (float(p_ab) + (1.0 - float(p_ba))) / 2.0
	return p_a, 1.0 - p_a, feat_names
