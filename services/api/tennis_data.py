"""
Read layer for the public tennis API (RacketEdge 3.0, database `racketedge`).

Each function serves one endpoint with one indexed query (or a primary-key join) against
tables built by scripts/load_racketedge.py. No JSON parsing or aggregation at request time.
Schema: db/racketedge_schema.sql. Response shapes: services/api/schemas.py.
"""
import math
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from . import db as dbmod
from .schemas import (
	ApiAvailability, ApiDoublesMatch, ApiMatch, ApiMatchPlayer, ApiStatus, ApiTournament,
	DoublesRecord, DoublesTeam, EventPbpResponse, H2HPlayer, H2HResponse, H2HStats, H2HSurface,
	LeaderboardEntry, LeaderboardResponse, MatchResult, MatchScore, PbpGame, PbpPoint, PbpServiceStats,
	PbpSet, PbpSide, PbpSummary, PlayerElo, PlayerMatchRow, PlayerMatchesResponse, PlayerProfile,
	PlayerRecentForm, PlayerSearchResponse, PlayerSearchResult, ReturnStats, ServeStats, SetScore,
	SidesInt, SidesStr, SurfaceRecord, TiebreakScore, WalkoverCounts,
)

SURFACES = ("hard", "clay", "grass", "indoor")
RESULT_OUTCOMES = ("completed", "retired", "defaulted")
# Not counted in records or H2H (same list as scripts/load_racketedge.py EXCLUDED_CATEGORIES)
EXCLUDED_CATEGORIES = ("Exhibition", "Legends", "Electronic Leagues", "Wheelchairs", "Wheelchairs Juniors")
_EXCLUDED_SQL = "(" + ", ".join(f"'{c}'" for c in EXCLUDED_CATEGORIES) + ")"
LIVE_LOOKBACK = timedelta(days=2)

# Characters NFKD does not decompose (kept in step with scripts/load_racketedge.py)
_FOLD = {"đ": "d", "ł": "l", "ø": "o", "ß": "ss", "æ": "ae", "œ": "oe", "ı": "i", "þ": "th", "ð": "d"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _query(sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
	conn = dbmod.connect()
	try:
		with dbmod.dict_cursor(conn) as cur:
			cur.execute(sql, params)
			return dbmod.fetchall_dicts(cur)
	finally:
		try:
			conn.close()
		except Exception:
			pass


def _iso(dt: Optional[datetime]) -> Optional[str]:
	return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _int(v: Any) -> Optional[int]:
	return int(v) if v is not None else None


def normalize_search(text: str) -> str:
	s = text.strip().lower()
	s = "".join(_FOLD.get(ch, ch) for ch in s)
	s = unicodedata.normalize("NFKD", s)
	return "".join(ch for ch in s if not unicodedata.combining(ch))


def _like_escape(s: str) -> str:
	return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _tournament(r: Dict[str, Any]) -> ApiTournament:
	return ApiTournament(
		id=_int(r.get("tournament_id")), name=r.get("tournament_name"), circuit=r.get("category"),
		level=_int(r.get("level_points")), surface=r.get("surface"), environment=r.get("environment"),
		round=r.get("round_name"),
	)


def _score(r: Dict[str, Any]) -> MatchScore:
	sets = []
	for n in range(1, 6):
		h, a = r.get(f"home_s{n}"), r.get(f"away_s{n}")
		if h is None or a is None:
			continue
		th, ta = r.get(f"home_tb{n}"), r.get(f"away_tb{n}")
		sets.append(SetScore(set=n, home=int(h), away=int(a), tiebreak=TiebreakScore(home=int(th), away=int(ta)) if th is not None and ta is not None else None))
	return MatchScore(sets=SidesInt(home=_int(r.get("home_sets")), away=_int(r.get("away_sets"))), set_scores=sets, winner=r.get("winner_side"))


def _status(r: Dict[str, Any]) -> ApiStatus:
	return ApiStatus(phase=r["phase"], outcome=r["outcome"], description=r.get("status_description"))


def _availability(r: Dict[str, Any]) -> ApiAvailability:
	return ApiAvailability(
		has_stats=bool(r["has_stats"]), stats_status=r["stats_status"],
		has_pbp=bool(r["has_pbp"]), pbp_status=r["pbp_status"], points_count=_int(r.get("points_count")),
	)


def _singles(r: Dict[str, Any]) -> ApiMatch:
	return ApiMatch(
		event_id=int(r["match_id"]), scheduled_at=_iso(r.get("start_utc")), format="singles",
		tournament=_tournament(r),
		home=ApiMatchPlayer(id=_int(r.get("home_player_id")), name=r.get("home_name"), country=r.get("home_country")),
		away=ApiMatchPlayer(id=_int(r.get("away_player_id")), name=r.get("away_name"), country=r.get("away_country")),
		status=_status(r), score=_score(r), availability=_availability(r),
	)


def _team(r: Dict[str, Any], side: str) -> DoublesTeam:
	return DoublesTeam(name=r.get(f"{side}_team_name"), players=[
		ApiMatchPlayer(id=_int(r.get(f"{side}_player{i}_id")), name=r.get(f"{side}_player{i}_name")) for i in (1, 2)
	])


def _doubles(r: Dict[str, Any]) -> ApiDoublesMatch:
	return ApiDoublesMatch(
		event_id=int(r["match_id"]), scheduled_at=_iso(r.get("start_utc")), format="doubles",
		tournament=_tournament(r), home=_team(r, "home"), away=_team(r, "away"),
		status=_status(r), score=_score(r), availability=_availability(r),
	)


def _circuit_clause(circuit: Optional[str]) -> Tuple[str, Tuple]:
	if not circuit:
		return "", ()
	return " AND category = %s", (circuit.strip(),)


# ---------------------------------------------------------------------------
# Match lists
# ---------------------------------------------------------------------------

def matches_on(day: date, circuit: Optional[str] = None, doubles: bool = False) -> List[Any]:
	table = "doubles_matches" if doubles else "matches"
	extra, params = _circuit_clause(circuit)
	rows = _query(f"SELECT * FROM {table} WHERE match_date = %s{extra} ORDER BY start_utc, match_id", (day,) + params)
	return [(_doubles if doubles else _singles)(r) for r in rows]


def live_matches(circuit: Optional[str] = None, doubles: bool = False) -> List[Any]:
	table = "doubles_matches" if doubles else "matches"
	extra, params = _circuit_clause(circuit)
	since = (datetime.now(timezone.utc) - LIVE_LOOKBACK).date()
	rows = _query(f"SELECT * FROM {table} WHERE phase = 'live' AND match_date >= %s{extra} ORDER BY start_utc, match_id", (since,) + params)
	return [(_doubles if doubles else _singles)(r) for r in rows]


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------

def _player_row(player_id: int) -> Dict[str, Any]:
	rows = _query("SELECT * FROM players WHERE player_id = %s", (player_id,))
	if not rows:
		raise HTTPException(status_code=404, detail="player_not_found")
	return rows[0]


def player_profile(player_id: int) -> PlayerProfile:
	p = _player_row(player_id)
	summaries = {r["scope"]: r for r in _query("SELECT * FROM player_summary WHERE player_id = %s", (player_id,))}
	form_rows = _query("SELECT * FROM player_form WHERE player_id = %s", (player_id,))
	form = form_rows[0] if form_rows else {}
	o = summaries.get("overall", {})

	career = {
		"matches_played": int(o.get("matches_played") or 0),
		"wins": int(o.get("wins") or 0),
		"losses": int(o.get("losses") or 0),
		"wins_by_retirement": int(o.get("wins_by_retirement") or 0),
		"losses_by_retirement": int(o.get("losses_by_retirement") or 0),
		"walkovers": WalkoverCounts(won=int(o.get("walkovers_won") or 0), lost=int(o.get("walkovers_lost") or 0)).model_dump(),
		"sets_won": _int(o.get("sets_won")), "sets_lost": _int(o.get("sets_lost")),
		"games_won": _int(o.get("games_won")), "games_lost": _int(o.get("games_lost")),
		"serve": ServeStats(
			matches_with_stats=int(o.get("matches_with_stats") or 0), aces=_int(o.get("aces")),
			double_faults=_int(o.get("double_faults")), aces_per_service_game=o.get("aces_per_service_game"),
			hold_pct=o.get("hold_pct"), bp_save_pct=o.get("bp_save_pct"),
		).model_dump(),
		"return": ReturnStats(return_games_won_pct=o.get("return_games_won_pct"), bp_convert_pct=o.get("bp_convert_pct")).model_dump(),
	}
	by_surface = {
		s: SurfaceRecord(
			matches_played=int(summaries[s]["matches_played"]), wins=int(summaries[s]["wins"]), losses=int(summaries[s]["losses"]),
			hold_pct=summaries[s].get("hold_pct"), return_games_won_pct=summaries[s].get("return_games_won_pct"),
		)
		for s in SURFACES if s in summaries
	}
	results = [x for x in (form.get("results") or "").split(",") if x]
	runs = _query("SELECT finished_utc FROM load_runs WHERE status = 'success' ORDER BY run_id DESC LIMIT 1")
	return PlayerProfile(
		player_id=int(p["player_id"]), name=p["name"], short_name=p.get("short_name"), country=p.get("country"), gender=p.get("gender"),
		elo=PlayerElo(**{s: summaries.get(s, {}).get("elo") for s in ("overall",) + SURFACES}),
		career=career, by_surface=by_surface,
		recent_form=PlayerRecentForm(results=results, wins=int(form.get("wins") or 0), losses=int(form.get("losses") or 0), hold_pct=form.get("hold_pct")),
		doubles=DoublesRecord(matches_played=int(form.get("doubles_matches") or 0), wins=int(form.get("doubles_wins") or 0), losses=int(form.get("doubles_losses") or 0)),
		last_match_at=_iso(p.get("last_match_utc")),
		last_updated_at=_iso(runs[0]["finished_utc"]) if runs else None,
	)


def search_players(q: str, gender: Optional[str], country: Optional[str], limit: int) -> PlayerSearchResponse:
	norm = normalize_search(q)
	tokens = [t for t in norm.replace("-", " ").split() if t]
	if len(norm.replace(" ", "")) < 2 or not tokens:
		raise HTTPException(status_code=400, detail="query_too_short — q needs at least 2 characters")
	where, params = [], []
	for t in tokens:
		e = _like_escape(t)
		where.append("(p.search_name LIKE %s OR p.search_name LIKE %s)")
		params += [f"{e}%", f"% {e}%"]
	if gender:
		where.append("p.gender = %s")
		params.append(gender.upper()[:1])
	if country:
		where.append("p.country = %s")
		params.append(country.upper())
	cond = " AND ".join(where)
	last = _like_escape(tokens[-1])
	rows = _query(f"""
		SELECT p.player_id, p.name, p.short_name, p.country, p.gender, p.last_match_utc,
			p.singles_matches, s.elo
		FROM players p
		LEFT JOIN player_summary s ON s.player_id = p.player_id AND s.scope = 'overall'
		WHERE {cond}
		ORDER BY (p.search_name = %s) DESC,
			(SUBSTRING_INDEX(p.search_name, ' ', -1) LIKE %s) DESC,
			(s.elo IS NULL), s.elo DESC, p.last_match_utc DESC, p.player_id
		LIMIT %s
	""", tuple(params) + (norm, f"{last}%", limit))
	total = _query(f"SELECT COUNT(*) AS n FROM players p WHERE {cond}", tuple(params))[0]["n"]
	return PlayerSearchResponse(query=q, total=int(total), players=[
		PlayerSearchResult(
			id=int(r["player_id"]), name=r["name"], short_name=r.get("short_name"), country=r.get("country"), gender=r.get("gender"),
			elo_overall=r.get("elo"), matches_played=int(r.get("singles_matches") or 0), last_match_at=_iso(r.get("last_match_utc")),
		) for r in rows
	])


def player_matches(player_id: int, fmt: str, page: int, page_size: int) -> PlayerMatchesResponse:
	p = _player_row(player_id)
	offset = (page - 1) * page_size
	rows_out: List[PlayerMatchRow] = []
	if fmt == "doubles":
		cols = ("home_player1_id", "home_player2_id", "away_player1_id", "away_player2_id")
		union = " UNION ".join(f"SELECT match_id FROM doubles_matches WHERE {c} = %s" for c in cols)
		total = int(_query(f"SELECT COUNT(*) AS n FROM ({union}) u", (player_id,) * 4)[0]["n"])
		rows = _query(f"SELECT d.* FROM doubles_matches d JOIN ({union}) u USING (match_id) ORDER BY d.start_utc DESC, d.match_id DESC LIMIT %s OFFSET %s",
					  (player_id,) * 4 + (page_size, offset))
		for r in rows:
			side = "home" if player_id in (r.get("home_player1_id"), r.get("home_player2_id")) else "away"
			opp = "away" if side == "home" else "home"
			partner_slot = 2 if r.get(f"{side}_player1_id") == player_id else 1
			res = None
			if r.get("winner_side") and r["outcome"] in RESULT_OUTCOMES + ("walkover",):
				res = "won" if r["winner_side"] == side else "lost"
			rows_out.append(PlayerMatchRow(
				event_id=int(r["match_id"]), played_at=_iso(r.get("start_utc")), format="doubles", side=side, tournament=_tournament(r),
				partner=ApiMatchPlayer(id=_int(r.get(f"{side}_player{partner_slot}_id")), name=r.get(f"{side}_player{partner_slot}_name")),
				opponents=[ApiMatchPlayer(id=_int(r.get(f"{opp}_player{i}_id")), name=r.get(f"{opp}_player{i}_name")) for i in (1, 2)],
				result=MatchResult(result=res, outcome=r["outcome"],
								   sets_won=_int(r.get(f"{side}_sets")), sets_lost=_int(r.get(f"{opp}_sets"))),
				score=_score(r), availability=_availability(r),
			))
	else:
		total = int(_query("SELECT COUNT(*) AS n FROM player_match_stats WHERE player_id = %s", (player_id,))[0]["n"])
		rows = _query("""
			SELECT m.*, s.side, s.result, s.sets_won AS p_sets_won, s.sets_lost AS p_sets_lost,
				s.games_won AS p_games_won, s.games_lost AS p_games_lost
			FROM player_match_stats s JOIN matches m ON m.match_id = s.match_id
			WHERE s.player_id = %s ORDER BY s.start_utc DESC, s.match_id DESC LIMIT %s OFFSET %s
		""", (player_id, page_size, offset))
		for r in rows:
			opp = "away" if r["side"] == "home" else "home"
			rows_out.append(PlayerMatchRow(
				event_id=int(r["match_id"]), played_at=_iso(r.get("start_utc")), format="singles", side=r["side"], tournament=_tournament(r),
				opponent=ApiMatchPlayer(id=_int(r.get(f"{opp}_player_id")), name=r.get(f"{opp}_name"), country=r.get(f"{opp}_country")),
				result=MatchResult(result=r.get("result"), outcome=r["outcome"], sets_won=_int(r.get("p_sets_won")), sets_lost=_int(r.get("p_sets_lost")),
								   games_won=_int(r.get("p_games_won")), games_lost=_int(r.get("p_games_lost"))),
				score=_score(r), availability=_availability(r),
			))
	total_pages = max(1, math.ceil(total / page_size)) if total else 0
	return PlayerMatchesResponse(player_id=player_id, name=p["name"], format=fmt, page=page, page_size=page_size,
								 total=total, total_pages=total_pages, has_next_page=page < total_pages, matches=rows_out)


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

def leaderboard(tour: str, surface: str, active_only: bool, page: int, page_size: int) -> LeaderboardResponse:
	key = (tour, surface, 1 if active_only else 0)
	total = int(_query("SELECT COUNT(*) AS n FROM elo_leaderboard WHERE tour = %s AND surface = %s AND active_only = %s", key)[0]["n"])
	lo = (page - 1) * page_size
	rows = _query(
		"SELECT * FROM elo_leaderboard WHERE tour = %s AND surface = %s AND active_only = %s AND rank_no > %s AND rank_no <= %s ORDER BY rank_no",
		key + (lo, lo + page_size),
	)
	total_pages = max(1, math.ceil(total / page_size)) if total else 0
	return LeaderboardResponse(
		tour=tour, surface=surface, active_only=active_only, page=page, page_size=page_size,
		total_players=total, total_pages=total_pages, has_next_page=page < total_pages,
		players=[LeaderboardEntry(rank=int(r["rank_no"]), id=int(r["player_id"]), name=r["name"], country=r.get("country"),
								  elo=r["elo"], matches=int(r["matches"]), last_match_at=_iso(r.get("last_match_utc"))) for r in rows],
	)


# ---------------------------------------------------------------------------
# Point-by-point
# ---------------------------------------------------------------------------

def _service_from_points(points: List[Dict[str, Any]], side: str) -> PbpServiceStats:
	games = [p for p in points if p["is_game_winning_point"] and not p["is_tiebreak"] and p["server"] == side]
	served = [p for p in points if p["server"] == side and p["is_break_point"]]
	return PbpServiceStats(
		service_games_played=len(games), service_games_won=sum(1 for p in games if p["game_winner"] == side),
		bp_faced=len(served), bp_saved=sum(1 for p in served if p["point_winner"] == side),
	)


def event_pbp(match_id: int) -> EventPbpResponse:
	rows = _query("SELECT * FROM matches WHERE match_id = %s", (match_id,))
	fmt = "singles"
	if not rows:
		rows = _query("SELECT * FROM doubles_matches WHERE match_id = %s", (match_id,))
		fmt = "doubles"
	if not rows:
		raise HTTPException(status_code=404, detail="event_not_found")
	m = rows[0]
	points = _query("SELECT * FROM pbp_points WHERE match_id = %s ORDER BY point_index", (match_id,))
	if not points:
		return EventPbpResponse(event_id=match_id, format=fmt, has_pbp=False, pbp_status="none")

	if fmt == "singles":
		players = {s: PbpSide(id=_int(m.get(f"{s}_player_id")), name=m.get(f"{s}_name")) for s in ("home", "away")}
	else:
		players = {s: PbpSide(name=m.get(f"{s}_team_name"), players=_team(m, s).players) for s in ("home", "away")}

	# Sets -> games -> points
	sets: List[PbpSet] = []
	by_set: Dict[int, Dict[int, List[Dict[str, Any]]]] = {}
	for p in points:
		by_set.setdefault(p["set_no"], {}).setdefault(p["game_no"], []).append(p)
	for set_no in sorted(by_set):
		games = []
		for game_no in sorted(by_set[set_no]):
			pts = by_set[set_no][game_no]
			first, last = pts[0], pts[-1]
			tb = bool(first["is_tiebreak"])
			winner = last["game_winner"]
			server = first["server"]
			result = "tiebreak" if tb else (("hold" if winner == server else "break") if winner and server else None)
			games.append(PbpGame(game=game_no, server=server, winner=winner, result=result, is_tiebreak=tb, points=[
				PbpPoint(
					point=i + 1, server=p["server"],
					score_before=SidesStr(home=p["score_before_home"], away=p["score_before_away"]),
					score_after=SidesStr(home=p["score_after_home"], away=p["score_after_away"]) if p["score_after_home"] is not None else None,
					winner=p["point_winner"], break_point=bool(p["is_break_point"]), game_point=bool(p["is_game_point"]),
					deciding_point=bool(p["is_deciding_point"]),
				) for i, p in enumerate(pts)
			]))
		sets.append(PbpSet(set=set_no, score=SidesInt(home=_int(m.get(f"home_s{set_no}")), away=_int(m.get(f"away_s{set_no}"))), games=games))

	# Service summary: game/break-point counts from the log when complete; aces/DFs from official stats
	service: Dict[str, PbpServiceStats] = {}
	pms = {}
	if fmt == "singles":
		pms = {r["side"]: r for r in _query("SELECT * FROM player_match_stats WHERE match_id = %s", (match_id,))}
	for side in ("home", "away"):
		if m["pbp_status"] == "complete":
			st = _service_from_points(points, side)
		else:
			r = pms.get(side, {})
			st = PbpServiceStats(service_games_played=_int(r.get("service_games_played")), service_games_won=_int(r.get("service_games_won")),
								 bp_faced=_int(r.get("bp_faced")), bp_saved=_int(r.get("bp_saved")))
		if side in pms:
			st.aces, st.double_faults = _int(pms[side].get("aces")), _int(pms[side].get("double_faults"))
		service[side] = st

	winner_side = m.get("winner_side")
	return EventPbpResponse(
		event_id=match_id, format=fmt, has_pbp=True, pbp_status=m["pbp_status"], points_count=len(points),
		players=players,
		summary=PbpSummary(score=_score(m), winner=players.get(winner_side) if winner_side else None, service=service),
		sets=sets,
	)


# ---------------------------------------------------------------------------
# Head-to-head
# ---------------------------------------------------------------------------

def h2h(player_a: int, player_b: int, surface: Optional[str]) -> H2HResponse:
	if player_a == player_b:
		raise HTTPException(status_code=400, detail="players_must_differ")
	pa, pb = _player_row(player_a), _player_row(player_b)
	lo, hi = min(player_a, player_b), max(player_a, player_b)

	def surface_clause(alias: str) -> Tuple[str, Tuple]:
		if surface == "indoor":
			return f" AND {alias}surface = 'hard' AND {alias}environment = 'indoor'", ()
		if surface:
			return f" AND {alias}surface = %s", (surface,)
		return "", ()

	extra, params = surface_clause("")
	matches = _query(
		f"SELECT * FROM matches WHERE player_lo_id = %s AND player_hi_id = %s{extra} "
		f"AND (category IS NULL OR category NOT IN {_EXCLUDED_SQL}) ORDER BY start_utc DESC, match_id DESC",
		(lo, hi) + params,
	)
	extra_m, params_m = surface_clause("m.")
	stats_rows = _query(
		f"SELECT s.* FROM player_match_stats s JOIN matches m ON m.match_id = s.match_id "
		f"WHERE m.player_lo_id = %s AND m.player_hi_id = %s{extra_m} "
		f"AND (m.category IS NULL OR m.category NOT IN {_EXCLUDED_SQL}) "
		f"AND s.outcome IN ('completed','retired','defaulted') AND s.stats_source IS NOT NULL",
		(lo, hi) + params_m,
	)

	results = [r for r in matches if r["outcome"] in RESULT_OUTCOMES and r.get("winner_player_id")]
	tally = {player_a: {"won": 0, "sets": 0, "games": 0}, player_b: {"won": 0, "sets": 0, "games": 0}}
	by_surface: Dict[str, Dict[str, int]] = {}
	for r in results:
		home, away = r["home_player_id"], r["away_player_id"]
		tally[r["winner_player_id"]]["won"] += 1
		tally[home]["sets"] += int(r.get("home_sets") or 0)
		tally[away]["sets"] += int(r.get("away_sets") or 0)
		for n in range(1, 6):
			tally[home]["games"] += int(r.get(f"home_s{n}") or 0)
			tally[away]["games"] += int(r.get(f"away_s{n}") or 0)
		key = "indoor" if r.get("surface") == "hard" and r.get("environment") == "indoor" else (r.get("surface") or "unknown")
		b = by_surface.setdefault(key, {"matches_played": 0, "player_a_wins": 0, "player_b_wins": 0})
		b["matches_played"] += 1
		b["player_a_wins" if r["winner_player_id"] == player_a else "player_b_wins"] += 1

	def stats_for(pid: int) -> H2HStats:
		mine = [s for s in stats_rows if s["player_id"] == pid]
		tot = lambda c: int(sum(int(s[c] or 0) for s in mine))  # noqa: E731
		return H2HStats(matches_with_stats=len(mine), aces=tot("aces"), double_faults=tot("double_faults"),
						return_games_won=tot("return_games_won"), bp_saved=tot("bp_saved"), bp_faced=tot("bp_faced"),
						bp_converted=tot("bp_converted"), bp_created=tot("bp_created"))

	return H2HResponse(
		surface=surface, matches_played=len(results),
		walkovers=sum(1 for r in matches if r["outcome"] == "walkover"),
		retirements=sum(1 for r in results if r["outcome"] == "retired"),
		player_a=H2HPlayer(id=player_a, name=pa["name"], matches_won=tally[player_a]["won"], sets_won=tally[player_a]["sets"], games_won=tally[player_a]["games"], stats=stats_for(player_a)),
		player_b=H2HPlayer(id=player_b, name=pb["name"], matches_won=tally[player_b]["won"], sets_won=tally[player_b]["sets"], games_won=tally[player_b]["games"], stats=stats_for(player_b)),
		by_surface={k: H2HSurface(**v) for k, v in by_surface.items()},
		last_meetings=[_singles(r) for r in results[:5]],
	)
