"""
DuckDB SQL that turns clean-layer tables into serving-table rows.

One implementation, two callers:
  - scripts/load_racketedge.py   whole history from Parquet (batch rebuild)
  - libs/racketedge/live.py      a handful of just-fetched matches (live upsert)

Inputs (DuckDB relations): cm (matches), cp (player_match_stats), cpts (pbp_points) in the
clean-layer schema, plus id_players / id_matches / id_tournaments maps.
"""

# Results that count as played matches (wins / losses / Elo)
RESULT_OUTCOMES = ("completed", "retired", "defaulted")
# Categories excluded from Elo, career aggregates and H2H
EXCLUDED_CATEGORIES = ("Exhibition", "Legends", "Electronic Leagues", "Wheelchairs", "Wheelchairs Juniors")

_PHASE = """CASE outcome
	WHEN 'scheduled' THEN 'upcoming'
	WHEN 'live' THEN 'live'
	WHEN 'suspended' THEN 'suspended'
	WHEN 'postponed' THEN 'postponed'
	WHEN 'cancelled' THEN 'cancelled'
	WHEN 'abandoned' THEN 'cancelled'
	ELSE 'completed' END"""

# Characters strip_accents() does not decompose (kept in step with services/api/tennis_data.py)
_FOLD = [("đ", "d"), ("ł", "l"), ("ø", "o"), ("ß", "ss"), ("æ", "ae"), ("œ", "oe"), ("ı", "i"), ("þ", "th"), ("ð", "d")]


def search_name(expr: str) -> str:
	s = f"lower({expr})"
	for a, b in _FOLD:
		s = f"replace({s}, '{a}', '{b}')"
	return f"strip_accents({s})"


def ts(expr: str) -> str:
	"""Epoch seconds -> naive UTC TIMESTAMP."""
	return f"make_timestamp(CAST({expr} AS BIGINT) * 1000000)"


def create_core(db) -> None:
	"""Table `m`: clean matches with public ids attached; every later step reads it."""
	db.sql(f"""
		CREATE TABLE m AS
		SELECT cm.*,
			im.match_id,
			it.tournament_id AS t_id,
			{ts('cm.start_timestamp')} AS start_ts,
			ph1.player_id AS hp1, ph2.player_id AS hp2, pa1.player_id AS ap1, pa2.player_id AS ap2,
			{_PHASE} AS phase,
			coalesce(category_name IN {EXCLUDED_CATEGORIES}, FALSE) AS excluded_category
		FROM cm
		JOIN id_matches im ON im.source_event_id = cm.event_id
		LEFT JOIN id_tournaments it ON it.source_tournament_id = cm.tournament_id
		LEFT JOIN id_players ph1 ON ph1.source_player_id = cm.home_player1_id
		LEFT JOIN id_players ph2 ON ph2.source_player_id = cm.home_player2_id
		LEFT JOIN id_players pa1 ON pa1.source_player_id = cm.away_player1_id
		LEFT JOIN id_players pa2 ON pa2.source_player_id = cm.away_player2_id
	""")


def create_appearances(db) -> None:
	"""Table `appear`: one row per player per match (singles and doubles)."""
	appearances = " UNION ALL ".join(
		f"SELECT {alias}{i} AS player_id, {side}_player{i}_name AS name, {side}_player{i}_short_name AS short_name, "
		f"{side}_player{i}_gender AS gender, {side}_player{i}_country AS country, start_ts, match_type FROM m WHERE {alias}{i} IS NOT NULL"
		for side, alias in (("home", "hp"), ("away", "ap")) for i in (1, 2)
	)
	db.sql(f"CREATE TABLE appear AS {appearances}")


def players_select() -> str:
	return f"""
		SELECT player_id,
			arg_max(name, start_ts) AS name,
			arg_max(short_name, start_ts) AS short_name,
			arg_max(gender, start_ts) FILTER (WHERE gender IS NOT NULL) AS gender,
			arg_max(country, start_ts) FILTER (WHERE country IS NOT NULL) AS country,
			{search_name('arg_max(name, start_ts)')} AS search_name,
			count(*) FILTER (WHERE match_type = 'singles') AS singles_matches,
			count(*) FILTER (WHERE match_type = 'doubles') AS doubles_matches,
			min(start_ts) AS first_match_utc,
			max(start_ts) AS last_match_utc
		FROM appear GROUP BY player_id
	"""


def tournaments_select() -> str:
	return """
		SELECT t_id AS tournament_id,
			arg_max(tournament_name, start_timestamp) AS name,
			arg_max(unique_tournament_id, start_timestamp) AS unique_tournament_id,
			arg_max(unique_tournament_name, start_timestamp) AS unique_name,
			arg_max(category_name, start_timestamp) AS category,
			arg_max(tier, start_timestamp) AS tier,
			arg_max(tournament_points, start_timestamp) AS level_points,
			mode(surface) AS surface,
			mode(environment) AS environment
		FROM m WHERE t_id IS NOT NULL GROUP BY t_id
	"""


def _score_columns() -> str:
	out = []
	for n in range(1, 6):
		out += [f"home_games_s{n} AS home_s{n}", f"away_games_s{n} AS away_s{n}", f"home_tb_s{n} AS home_tb{n}", f"away_tb_s{n} AS away_tb{n}"]
	return ", ".join(out)


_COMMON_HEAD = """match_id, start_ts AS start_utc, CAST(start_ts AS DATE) AS match_date, t_id AS tournament_id,
	tournament_name, category_name AS category, tier, tournament_points AS level_points, round_name, surface, environment"""


def _common_tail(now: str, singles: bool) -> str:
	winner_player = "CASE winner_side WHEN 'home' THEN hp1 WHEN 'away' THEN ap1 END AS winner_player_id, " if singles else ""
	return f"""phase, outcome, status_description, winner_side, {winner_player}
		home_sets, away_sets, {_score_columns()}, match_tiebreak_set,
		CAST(has_stats AS INT) AS has_stats, stats_status, CAST(has_pbp AS INT) AS has_pbp, pbp_status,
		CASE WHEN pbp_status = 'complete' THEN pbp_points END AS points_count, TIMESTAMP '{now}' AS updated_utc"""


def matches_select(now: str) -> str:
	return f"""
		SELECT {_COMMON_HEAD},
			hp1 AS home_player_id, ap1 AS away_player_id, home_player1_name AS home_name, away_player1_name AS away_name,
			home_player1_country AS home_country, away_player1_country AS away_country,
			least(hp1, ap1) AS player_lo_id, greatest(hp1, ap1) AS player_hi_id,
			{_common_tail(now, singles=True)}
		FROM m WHERE match_type = 'singles'
	"""


def doubles_select(now: str) -> str:
	return f"""
		SELECT {_COMMON_HEAD},
			hp1 AS home_player1_id, hp2 AS home_player2_id, ap1 AS away_player1_id, ap2 AS away_player2_id,
			home_player1_name, home_player2_name, away_player1_name, away_player2_name,
			home_name AS home_team_name, away_name AS away_team_name,
			{_common_tail(now, singles=False)}
		FROM m WHERE match_type = 'doubles'
	"""


def create_player_sides(db) -> None:
	"""Table `pms`: one row per side of every singles match, joined to both sides' clean stats."""
	db.sql(f"""
		CREATE TABLE pms AS
		WITH sides AS (
			SELECT m.match_id, m.event_id, m.start_ts, m.outcome, m.winner_side, m.stats_status, m.pbp_status,
				m.surface, m.environment, m.excluded_category, m.start_timestamp,
				s.side, CASE s.side WHEN 'home' THEN m.hp1 ELSE m.ap1 END AS player_id,
				CASE s.side WHEN 'home' THEN m.ap1 ELSE m.hp1 END AS opponent_id,
				CASE s.side WHEN 'home' THEN m.home_sets ELSE m.away_sets END AS sets_won,
				CASE s.side WHEN 'home' THEN m.away_sets ELSE m.home_sets END AS sets_lost,
				CASE s.side WHEN 'home' THEN m.home_games_total ELSE m.away_games_total END AS games_won,
				CASE s.side WHEN 'home' THEN m.away_games_total ELSE m.home_games_total END AS games_lost,
				{' + '.join(f'(m.home_tb_s{n} IS NOT NULL)::INT' for n in range(1, 6))} AS tb_played_score
			FROM m CROSS JOIN (VALUES ('home'), ('away')) s(side)
			WHERE m.match_type = 'singles'
		)
		SELECT sd.*,
			CASE WHEN sd.winner_side IS NULL OR sd.outcome NOT IN ('completed','retired','defaulted','walkover') THEN NULL
				WHEN sd.winner_side = sd.side THEN 'won' ELSE 'lost' END AS result,
			me.* EXCLUDE (event_id, side, player_id, opponent_id, games_won),
			op.pbp_service_games_played AS opp_pbp_service_games_played,
			op.pbp_bp_faced AS opp_pbp_bp_faced
		FROM sides sd
		LEFT JOIN cp me ON me.event_id = sd.event_id AND me.side = sd.side
		LEFT JOIN cp op ON op.event_id = sd.event_id AND op.side <> sd.side
	""")


def player_match_stats_select() -> str:
	"""Per-stat source rule: PBP log when complete, else vendor when stats_status = 'ok'."""
	ok = "stats_status = 'ok'"
	pbp = "pbp_points_won IS NOT NULL"
	g = lambda pbp_expr, vendor_expr: f"CASE WHEN {pbp} THEN {pbp_expr} WHEN {ok} THEN {vendor_expr} END"  # noqa: E731
	return f"""
		SELECT match_id, player_id, opponent_id, start_ts AS start_utc, side, result, outcome,
			sets_won, sets_lost, games_won, games_lost, stats_status,
			CASE WHEN {pbp} THEN 'pbp' WHEN {ok} THEN 'vendor' END AS stats_source,
			CASE WHEN {ok} THEN aces END AS aces,
			CASE WHEN {ok} THEN double_faults END AS double_faults,
			CASE WHEN {ok} THEN first_serves_in END AS first_serves_in,
			CASE WHEN {ok} THEN first_serves_total END AS first_serves_total,
			CASE WHEN {ok} THEN first_serve_points_won END AS first_serve_points_won,
			CASE WHEN {ok} THEN second_serve_points_won END AS second_serve_points_won,
			{g('pbp_service_games_played', 'service_games_played')} AS service_games_played,
			{g('pbp_service_games_won', 'service_games_won')} AS service_games_won,
			{g('pbp_bp_faced', 'bp_faced')} AS bp_faced,
			{g('pbp_bp_saved', 'bp_saved')} AS bp_saved,
			{g('opp_pbp_service_games_played', 'return_games_played')} AS return_games_played,
			{g('pbp_bp_converted', 'return_games_won')} AS return_games_won,
			{g('opp_pbp_bp_faced', 'bp_created')} AS bp_created,
			{g('pbp_bp_converted', 'bp_converted')} AS bp_converted,
			{g('pbp_service_points_played', 'first_serves_total')} AS service_points_played,
			{g('pbp_service_points_won', 'service_points_won')} AS service_points_won,
			{g('pbp_return_points_won', 'return_points_won')} AS return_points_won,
			{g('pbp_points_won', 'points_won')} AS points_won,
			{g('pbp_tiebreaks_played', 'tb_played_score')} AS tiebreaks_played,
			{g('pbp_tiebreaks_won', 'tiebreaks_won')} AS tiebreaks_won
		FROM pms WHERE player_id IS NOT NULL AND player_id IS DISTINCT FROM opponent_id  -- vendor errors list a player against themselves
	"""


def pbp_points_select() -> str:
	"""Point rows for ATP/WTA matches (singles and doubles) only."""
	return """
		SELECT m.match_id, p.point_index, p.set_no, p.game_no, p.server, CAST(p.is_tiebreak AS INT) AS is_tiebreak,
			p.score_before_home, p.score_before_away, p.score_after_home, p.score_after_away,
			p.point_winner, p.game_winner,
			CAST(p.is_break_point AS INT) AS is_break_point, CAST(p.is_game_point AS INT) AS is_game_point,
			CAST(p.is_deciding_point AS INT) AS is_deciding_point, CAST(p.is_game_winning_point AS INT) AS is_game_winning_point
		FROM cpts p JOIN m ON m.event_id = p.event_id
		WHERE m.tier = 'main'
	"""
