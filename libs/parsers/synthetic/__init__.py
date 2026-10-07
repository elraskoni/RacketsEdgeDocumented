"""Source adapter for the synthetic feed (libs/synthetic/simulator.py).

The production parser for the private data source is not in this repository. This adapter has
the same interface: parse_match(event, statistics, pbp) returns clean-layer rows

	{"match": {...}, "player_stats": [...], "points": [...], "issues": [...]}

with exactly the columns below, so everything downstream (clean Parquet, serving SQL, API) is
the production code. Adding a data source means writing one adapter like this one.

Validation never drops data: findings are returned as issues and summarised in the match's
stats_status / pbp_status flags.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

PARSER_VERSION = "synthetic-1.0.0"
RESULT_OUTCOMES = frozenset({"completed", "retired", "defaulted"})
MAX_SETS = 5

PLAYER_STATS_COLUMNS = [
	"event_id", "side", "player_id", "opponent_id", "aces", "bp_converted", "bp_created", "bp_faced", "bp_saved", "double_faults",
	"first_return_points_total", "first_return_points_won", "first_serve_points_total", "first_serve_points_won", "first_serves_in",
	"first_serves_total", "games_won", "max_games_in_row", "max_points_in_row", "points_won", "return_games_played", "return_games_won",
	"return_points_won", "second_return_points_total", "second_return_points_won", "second_serve_points_total", "second_serve_points_won",
	"second_serves_in", "second_serves_total", "service_games_played", "service_games_won", "service_points_won", "tiebreaks_won",
	"unforced_errors", "winners", "pbp_points_won", "pbp_service_points_played", "pbp_service_points_won", "pbp_return_points_won",
	"pbp_service_games_played", "pbp_service_games_won", "pbp_bp_faced", "pbp_bp_saved", "pbp_bp_converted", "pbp_tiebreaks_played",
	"pbp_tiebreaks_won",
]
POINT_COLUMNS = [
	"event_id", "set_no", "game_no", "point_index", "server", "is_tiebreak",
	"score_before_home", "score_before_away", "score_after_home", "score_after_away",
	"point_winner", "is_break_point", "is_game_point", "is_deciding_point", "is_game_winning_point",
	"game_winner", "games_home_before", "games_away_before", "sets_home_before", "sets_away_before",
]
_CATEGORY_IDS = {"ATP": 3, "WTA": 6, "Challenger": 72, "WTA 125": 871, "ITF Men": 785, "ITF Women": 213}
_STATUS = {  # feed status -> (status_type, status_description, outcome)
	"finished": ("finished", "Ended", "completed"),
	"retired": ("finished", "Retired", "retired"),
	"walkover": ("finished", "Walkover", "walkover"),
	"scheduled": ("notstarted", "Not started", "scheduled"),
}
_GROUND = {"hard": ("Hardcourt outdoor", "hard", "outdoor"), "indoor": ("Hardcourt indoor", "hard", "indoor"),
	"clay": ("Red clay", "clay", "outdoor"), "grass": ("Grass", "grass", "outdoor")}
_OTHER = {"home": "away", "away": "home"}


class Issues:
	def __init__(self, event_id):
		self.event_id, self.items = event_id, []

	def add(self, check: str, severity: str, detail: str = "", source: str = "event") -> None:
		self.items.append({"event_id": self.event_id, "source": source, "check": check, "severity": severity, "detail": detail})


def _player_fields(prefix: str, p: Optional[Dict[str, Any]]) -> Dict[str, Any]:
	p = p or {}
	out = {f"{prefix}1_{k}": p.get(k) for k in ("id", "name", "short_name", "gender", "country")}
	out.update({f"{prefix}2_{k}": None for k in ("id", "name", "short_name", "gender", "country")})
	return out


def _valid_set(h: int, a: int, tb_h: Optional[int], tb_a: Optional[int]) -> bool:
	hi, lo = max(h, a), min(h, a)
	if hi == 6 and lo <= 4:
		return tb_h is None
	if hi == 7 and lo == 5:
		return tb_h is None
	if hi == 7 and lo == 6:
		return tb_h is not None and tb_a is not None and max(tb_h, tb_a) >= 7 and abs(tb_h - tb_a) >= 2
	return False


def parse_match(event: Dict[str, Any], stats: Optional[Dict[str, Any]] = None, pbp: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
	eid = event.get("id")
	issues = Issues(eid)
	t = event.get("tournament") or {}
	home, away = event.get("home") or {}, event.get("away") or {}
	status_type, status_desc, outcome = _STATUS.get(event.get("status"), (None, None, "other"))
	ground, surface, environment = _GROUND.get(t.get("surface"), (None, None, None))
	start = event.get("start_timestamp")
	sets = event.get("sets") or []
	winner_side = event.get("winner")

	if home.get("id") is not None and home.get("id") == away.get("id"):
		issues.add("player_on_both_sides", "error", f"player id {home.get('id')}")

	# Score: per set, plus validity of every completed set
	won = {"home": 0, "away": 0}
	completed_sets = len(sets) if outcome != "retired" else max(0, len(sets) - 1)
	for i, s in enumerate(sets[:completed_sets]):
		if not _valid_set(s["home"], s["away"], s.get("tb_home"), s.get("tb_away")):
			issues.add("invalid_set_score", "error", f"set {i + 1}: {s['home']}-{s['away']}")
		won["home" if s["home"] > s["away"] else "away"] += 1
	if outcome == "completed" and winner_side and won[winner_side] <= won[_OTHER[winner_side]]:
		issues.add("winner_vs_sets", "error", f"winner {winner_side}, sets {won}")

	match: Dict[str, Any] = {
		"event_id": eid, "start_timestamp": start,
		"start_utc": datetime.fromtimestamp(start, tz=timezone.utc) if start else None,
		"tournament_id": t.get("id"), "tournament_name": t.get("name"),
		"unique_tournament_id": t.get("unique_id"), "unique_tournament_name": t.get("name"),
		"category_id": _CATEGORY_IDS.get(t.get("category")), "category_name": t.get("category"), "tier": t.get("tier") or "other",
		"round_name": event.get("round"), "tournament_points": t.get("points"),
		"season_year": datetime.fromtimestamp(start, tz=timezone.utc).year if start else None,
		"match_type": "singles",
		"home_id": home.get("id"), "home_name": home.get("name"), "away_id": away.get("id"), "away_name": away.get("name"),
		"status_type": status_type, "status_description": status_desc, "outcome": outcome,
		"winner_side": winner_side if outcome != "scheduled" else None,
		"winner_id": (home if winner_side == "home" else away).get("id") if winner_side else None,
		"winner_source": "winner_code" if winner_side else None,
		"home_sets": won["home"] if sets else None, "away_sets": won["away"] if sets else None,
		"sets_played": len(sets) or None, "match_tiebreak_set": None,
		"ground_type": ground, "surface": surface, "environment": environment,
		"first_to_serve": event.get("first_server"), "final_result_only": False,
		**_player_fields("home_player", home), **_player_fields("away_player", away),
	}
	for n in range(1, MAX_SETS + 1):
		s = sets[n - 1] if n <= len(sets) else {}
		for side in ("home", "away"):
			match[f"{side}_games_s{n}"] = s.get(side)
			match[f"{side}_tb_s{n}"] = s.get(f"tb_{side}")
	for side in ("home", "away"):
		match[f"{side}_games_total"] = sum(s[side] for s in sets) if sets else None

	points, pbp_totals, pbp_status = _parse_points(eid, pbp, sets, outcome, issues)
	player_stats = _parse_stats(eid, stats, match, pbp_totals, issues) if (stats or pbp_totals) else []

	match["has_stats"] = bool(stats)
	match["stats_status"] = ("suspect" if any(i["source"] == "stats" and i["severity"] == "error" for i in issues.items) else "ok") if stats else "none"
	match["has_pbp"] = bool(points)
	match["pbp_status"] = pbp_status
	match["pbp_points"] = len(points) or None
	match["pbp_sets"] = len({p["set_no"] for p in points}) or None
	match["pbp_games"] = len({(p["set_no"], p["game_no"]) for p in points}) or None
	match["issue_count"] = len(issues.items)
	match["parser_version"] = PARSER_VERSION
	return {"match": match, "player_stats": player_stats, "points": points, "issues": issues.items}


def _parse_points(eid, pbp, sets, outcome, issues) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Dict[str, int]]], str]:
	raw = (pbp or {}).get("points") or []
	if not raw:
		return [], None, "none"
	rows: List[Dict[str, Any]] = []
	games_won: Dict[Tuple[int, str], int] = {}
	for i, p in enumerate(raw):
		srv, tiebreak = p["server"], p["tiebreak"]
		bh, ba = p["score_before"]
		gw = p.get("game_winner")
		s_pts, r_pts = (bh, ba) if srv == "home" else (ba, bh)
		is_bp = (not tiebreak) and r_pts in ("40", "A") and s_pts not in ("40", "A") or (not tiebreak and r_pts == "A")
		is_gp = (not tiebreak) and s_pts in ("40", "A") and r_pts not in ("40", "A") or (not tiebreak and s_pts == "A")
		after = (None, None) if gw else _next_score(bh, ba, p["winner"], tiebreak)
		rows.append({
			"event_id": eid, "set_no": p["set"], "game_no": p["game"], "point_index": i, "server": srv, "is_tiebreak": tiebreak,
			"score_before_home": bh, "score_before_away": ba, "score_after_home": after[0], "score_after_away": after[1],
			"point_winner": p["winner"], "is_break_point": bool(is_bp), "is_game_point": bool(is_gp), "is_deciding_point": False,
			"is_game_winning_point": gw is not None, "game_winner": gw,
			"games_home_before": p["games_before"][0], "games_away_before": p["games_before"][1],
			"sets_home_before": p["sets_before"][0], "sets_away_before": p["sets_before"][1],
		})
		if gw:
			games_won[(p["set"], gw)] = games_won.get((p["set"], gw), 0) + 1
	# Rebuild the official set scores from the log
	complete = True
	for n, s in enumerate(sets, 1):
		for side in ("home", "away"):
			if games_won.get((n, side), 0) != s[side]:
				complete = False
	if not complete:
		issues.add("pbp_games_vs_score", "warning" if outcome == "retired" else "error", "rebuilt games differ from the official score", "pbp")
	totals = {side: _side_totals(rows, side) for side in ("home", "away")} if complete else None
	return rows, totals, "complete" if complete else "partial"


def _next_score(bh: str, ba: str, winner: str, tiebreak: bool) -> Tuple[str, str]:
	if tiebreak:
		h, a = int(bh) + (winner == "home"), int(ba) + (winner == "away")
		return str(h), str(a)
	order = ["0", "15", "30", "40"]
	h, a = bh, ba
	if winner == "home":
		if a == "A":
			return "40", "40"
		return (order[order.index(h) + 1] if h in order[:3] else "A"), a
	if h == "A":
		return "40", "40"
	return h, (order[order.index(a) + 1] if a in order[:3] else "A")


def _side_totals(rows: List[Dict[str, Any]], side: str) -> Dict[str, int]:
	opp = _OTHER[side]
	served = [p for p in rows if p["server"] == side]
	returned = [p for p in rows if p["server"] == opp]
	ends = [p for p in rows if p["is_game_winning_point"]]
	svc_games = [p for p in ends if not p["is_tiebreak"] and p["server"] == side]
	tb_ends = [p for p in ends if p["is_tiebreak"]]
	return {
		"pbp_points_won": sum(p["point_winner"] == side for p in rows),
		"pbp_service_points_played": len(served),
		"pbp_service_points_won": sum(p["point_winner"] == side for p in served),
		"pbp_return_points_won": sum(p["point_winner"] == side for p in returned),
		"pbp_service_games_played": len(svc_games),
		"pbp_service_games_won": sum(p["game_winner"] == side for p in svc_games),
		"pbp_bp_faced": sum(1 for p in served if p["is_break_point"]),
		"pbp_bp_saved": sum(1 for p in served if p["is_break_point"] and p["point_winner"] == side),
		"pbp_bp_converted": sum(1 for p in returned if p["is_break_point"] and p["point_winner"] == side),
		"pbp_tiebreaks_played": len(tb_ends),
		"pbp_tiebreaks_won": sum(p["game_winner"] == side for p in tb_ends),
	}


def _parse_stats(eid, stats, match, pbp_totals, issues) -> List[Dict[str, Any]]:
	rows = []
	for side in ("home", "away"):
		me = (stats or {}).get(side) or {}
		op = (stats or {}).get(_OTHER[side]) or {}
		row: Dict[str, Any] = {c: None for c in PLAYER_STATS_COLUMNS}
		row.update({"event_id": eid, "side": side, "player_id": match[f"{side}_player1_id"], "opponent_id": match[f"{_OTHER[side]}_player1_id"]})
		if me:
			row.update({k: me.get(k) for k in PLAYER_STATS_COLUMNS if k in me})
			row["first_return_points_total"] = op.get("first_serve_points_total")
			row["first_return_points_won"] = (op.get("first_serve_points_total") or 0) - (op.get("first_serve_points_won") or 0)
			row["second_return_points_total"] = op.get("second_serve_points_total")
			row["second_return_points_won"] = (op.get("second_serve_points_total") or 0) - (op.get("second_serve_points_won") or 0)
			row["games_won"] = match[f"{side}_games_total"]
			row["tiebreaks_won"] = sum(1 for n in range(1, MAX_SETS + 1)
				if match[f"{side}_tb_s{n}"] is not None and match[f"{side}_tb_s{n}"] > match[f"{_OTHER[side]}_tb_s{n}"])
			_check_stats(me, side, issues)
		if pbp_totals:
			row.update(pbp_totals[side])
		rows.append(row)
	return rows


def _check_stats(s: Dict[str, Any], side: str, issues: Issues) -> None:
	served = (s.get("first_serve_points_total") or 0) + (s.get("second_serve_points_total") or 0)
	checks = [
		("aces_le_service_points", (s.get("aces") or 0) <= served),
		("service_points_won_le_played", (s.get("service_points_won") or 0) <= served),
		("bp_saved_le_faced", (s.get("bp_saved") or 0) <= (s.get("bp_faced") or 0)),
		("service_games_won_le_played", (s.get("service_games_won") or 0) <= (s.get("service_games_played") or 0)),
	]
	for name, ok in checks:
		if not ok:
			issues.add(name, "error", side, "stats")
