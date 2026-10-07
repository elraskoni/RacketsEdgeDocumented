"""
Load the RacketEdge 3.0 serving tables (env file env/<APP_ENV>.env, default `demo`) from the clean Parquet layer.

	python scripts/load_racketedge.py --root ./library

Full rebuild with an atomic swap: every serving table is loaded into `<table>__new`, then
all of them replace the live tables in one RENAME TABLE, so the API never sees a partial
or empty database. The id_* maps are only appended to (run scripts/seed_racketedge_ids.py
once first). Row-building SQL is shared with the live poller (libs/racketedge/serving_sql.py);
aggregates (Elo, career, form, leaderboard) are computed here only.

Serving schema: db/racketedge_schema.sql.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import duckdb
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from racketedge_db import connect  # noqa: E402
from libs.racketedge import serving_sql as sql  # noqa: E402
from libs.racketedge.serving_sql import RESULT_OUTCOMES  # noqa: E402
from libs.racketedge.live import assert_racketedge  # noqa: E402

ELO_K = 24.0
ELO_START = 1500.0
SCOPES = ("overall", "hard", "clay", "grass", "indoor")
LEADERBOARD_MIN_MATCHES = 10
LEADERBOARD_ACTIVE_DAYS = 365

SERVING_TABLES = (
	"players", "tournaments", "matches", "doubles_matches", "player_match_stats", "pbp_points",
	"player_summary", "player_form", "elo_leaderboard",
)


def _scope_filter(scope: str) -> str:
	return {
		"overall": "TRUE",
		"hard": "surface = 'hard'",
		"clay": "surface = 'clay'",
		"grass": "surface = 'grass'",
		"indoor": "surface = 'hard' AND environment = 'indoor'",
	}[scope]


class Loader:
	def __init__(self, root: str):
		self.clean = os.path.join(root, "source", "clean").replace("\\", "/")
		self.db = duckdb.connect()
		self.db.sql("SET TimeZone = 'UTC'")
		self.my = connect(os.getenv("APP_ENV") or "demo", local_infile=True)
		assert_racketedge(self.my)
		self.tmp = tempfile.mkdtemp(prefix="racketedge_load_")
		self.counts: Dict[str, int] = {}
		self.data_through: Optional[datetime] = None

	def close(self) -> None:
		self.my.close()
		shutil.rmtree(self.tmp, ignore_errors=True)

	# ---- helpers ----

	def _mysql_rows(self, query: str) -> List[Tuple]:
		with self.my.cursor() as cur:
			cur.execute(query)
			return list(cur.fetchall())

	def _load(self, table: str, select_sql: str) -> int:
		"""Write a DuckDB query to TSV and LOAD DATA it into `<table>__new` (swapped in later)."""
		rel = self.db.sql(select_sql)
		columns = rel.columns
		path = os.path.join(self.tmp, f"{table}.tsv").replace("\\", "/")
		rel.write_csv(path, sep="\t", header=False, na_rep="NULL", quotechar='"')
		staging = f"{table}__new"
		with self.my.cursor() as cur:
			cur.execute(f"DROP TABLE IF EXISTS {staging}")
			cur.execute(f"CREATE TABLE {staging} LIKE {table}")
			cur.execute(
				f"LOAD DATA LOCAL INFILE '{path}' INTO TABLE {staging} CHARACTER SET utf8mb4 "
				"FIELDS TERMINATED BY '\\t' OPTIONALLY ENCLOSED BY '\"' ESCAPED BY '' "
				"LINES TERMINATED BY '\\n' "
				f"({', '.join(f'`{c}`' for c in columns)})"
			)
			cur.execute(f"SELECT COUNT(*) FROM {staging}")
			n = int(cur.fetchone()[0])
			cur.execute("SHOW WARNINGS LIMIT 5")
			warnings = cur.fetchall()
		os.remove(path)
		expected = int(self.db.sql(f"SELECT COUNT(*) FROM ({select_sql})").fetchone()[0])
		if n != expected:
			raise RuntimeError(f"{table}: loaded {n} rows, expected {expected}")
		if warnings:
			print(f"[load] {table} warnings: {warnings}", flush=True)
		self.counts[table] = n
		print(f"[load] {table}: {n} rows", flush=True)
		return n

	def _swap_all(self) -> None:
		"""Replace every serving table with its __new copy in one atomic RENAME."""
		renames = ", ".join(f"{t} TO {t}__old, {t}__new TO {t}" for t in SERVING_TABLES)
		with self.my.cursor() as cur:
			for t in SERVING_TABLES:
				cur.execute(f"DROP TABLE IF EXISTS {t}__old")
			cur.execute(f"RENAME TABLE {renames}")
			for t in SERVING_TABLES:
				cur.execute(f"DROP TABLE {t}__old")
		print(f"[load] swapped {len(SERVING_TABLES)} tables", flush=True)

	def _drop_staging(self) -> None:
		with self.my.cursor() as cur:
			for t in SERVING_TABLES:
				cur.execute(f"DROP TABLE IF EXISTS {t}__new")

	# ---- steps ----

	def register_sources(self) -> None:
		self.db.sql(f"CREATE VIEW cm AS SELECT * FROM read_parquet('{self.clean}/matches/*/*.parquet')")
		self.db.sql(f"CREATE VIEW cp AS SELECT * FROM read_parquet('{self.clean}/player_match_stats/*/*.parquet')")
		self.db.sql(f"CREATE VIEW cpts AS SELECT * FROM read_parquet('{self.clean}/pbp_points/*/*.parquet')")
		self.parser_versions = [r[0] for r in self.db.sql("SELECT DISTINCT parser_version FROM cm").fetchall()]
		with open(os.path.join(self.clean, "manifests", "matches.json"), encoding="utf-8") as f:
			self.source_months = len(json.load(f)["months"])

	def assign_ids(self) -> None:
		"""Append any new source ids to the id maps, then pull the maps into DuckDB."""
		cols = [f"{s}_player{i}_id" for s in ("home", "away") for i in (1, 2)]
		players = self.db.sql(" UNION ".join(f"SELECT {c} AS s FROM cm WHERE {c} IS NOT NULL" for c in cols)).fetchall()
		events = self.db.sql("SELECT event_id FROM cm").fetchall()
		tourns = self.db.sql("SELECT DISTINCT tournament_id FROM cm WHERE tournament_id IS NOT NULL").fetchall()
		with self.my.cursor() as cur:
			for table, col, rows in (
				("id_players", "source_player_id", players),
				("id_matches", "source_event_id", events),
				("id_tournaments", "source_tournament_id", tourns),
			):
				before = self._mysql_rows(f"SELECT COUNT(*) FROM {table}")[0][0]
				for i in range(0, len(rows), 10000):
					cur.executemany(f"INSERT IGNORE INTO {table} ({col}) VALUES (%s)", [(int(r[0]),) for r in rows[i:i + 10000]])
				after = self._mysql_rows(f"SELECT COUNT(*) FROM {table}")[0][0]
				print(f"[ids] {table}: {after} ({after - before} new)", flush=True)
		for table, cols in (("id_players", "player_id, source_player_id"), ("id_matches", "match_id, source_event_id"), ("id_tournaments", "tournament_id, source_tournament_id")):
			df = pd.DataFrame(self._mysql_rows(f"SELECT {cols} FROM {table}"), columns=[c.strip() for c in cols.split(",")])
			self.db.register(f"{table}_df", df)
			self.db.sql(f"CREATE TABLE {table} AS SELECT * FROM {table}_df")

	def build_rows(self) -> None:
		sql.create_core(self.db)
		self.data_through = self.db.sql(f"SELECT MAX(start_ts) FROM m WHERE outcome IN {RESULT_OUTCOMES}").fetchone()[0]
		sql.create_appearances(self.db)
		self.db.sql("CREATE TABLE appear_players AS SELECT player_id, arg_max(name, start_ts) AS name, "
					"arg_max(country, start_ts) FILTER (WHERE country IS NOT NULL) AS country, "
					"arg_max(gender, start_ts) FILTER (WHERE gender IS NOT NULL) AS gender FROM appear GROUP BY player_id")
		sql.create_player_sides(self.db)
		self.db.sql(f"CREATE TABLE pms_rows AS {sql.player_match_stats_select()}")

	def load_rows(self) -> None:
		now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
		self._load("players", sql.players_select())
		self._load("tournaments", sql.tournaments_select())
		self._load("matches", sql.matches_select(now))
		self._load("doubles_matches", sql.doubles_select(now))
		self._load("player_match_stats", "SELECT * FROM pms_rows")
		self._load("pbp_points", sql.pbp_points_select())

	def compute_elo(self) -> None:
		rows = self.db.sql(f"""
			SELECT hp1, ap1, winner_side, surface, environment
			FROM m
			WHERE match_type = 'singles' AND outcome IN {RESULT_OUTCOMES} AND winner_side IS NOT NULL
				AND hp1 IS NOT NULL AND ap1 IS NOT NULL AND hp1 <> ap1 AND NOT excluded_category
			ORDER BY start_timestamp, event_id
		""").fetchall()
		elo: Dict[str, Dict[int, float]] = {s: {} for s in SCOPES}
		n_matches: Dict[str, Dict[int, int]] = {s: defaultdict(int) for s in SCOPES}
		for home, away, winner, surface, environment in rows:
			scopes = ["overall"]
			if surface in ("hard", "clay", "grass"):
				scopes.append(surface)
			if surface == "hard" and environment == "indoor":
				scopes.append("indoor")
			s_home = 1.0 if winner == "home" else 0.0
			for sc in scopes:
				e = elo[sc]
				rh, ra = e.get(home, ELO_START), e.get(away, ELO_START)
				exp_h = 1.0 / (1.0 + 10.0 ** ((ra - rh) / 400.0))
				e[home] = rh + ELO_K * (s_home - exp_h)
				e[away] = ra + ELO_K * ((1.0 - s_home) - (1.0 - exp_h))
				n_matches[sc][home] += 1
				n_matches[sc][away] += 1
		recs = [(pid, sc, round(r, 2), n_matches[sc][pid]) for sc in SCOPES for pid, r in elo[sc].items()]
		self.db.register("elo_df", pd.DataFrame(recs, columns=["player_id", "scope", "elo", "elo_matches"]))
		self.db.sql("CREATE TABLE elo AS SELECT * FROM elo_df")
		print(f"[elo] {len(rows)} singles results rated; {len(elo['overall'])} players", flush=True)

	def load_player_summary(self) -> None:
		# Career rows: the loaded player_match_stats rows, minus excluded categories
		self.db.sql("""
			CREATE TABLE ps AS
			SELECT r.*, b.surface, b.environment, b.start_ts
			FROM pms_rows r
			JOIN pms b ON b.match_id = r.match_id AND b.player_id = r.player_id
			WHERE NOT b.excluded_category
		""")
		parts = []
		for sc in SCOPES:
			parts.append(f"""
				SELECT player_id, '{sc}' AS scope,
					count(*) FILTER (WHERE outcome IN {RESULT_OUTCOMES} AND result IS NOT NULL) AS matches_played,
					count(*) FILTER (WHERE outcome IN {RESULT_OUTCOMES} AND result = 'won') AS wins,
					count(*) FILTER (WHERE outcome IN {RESULT_OUTCOMES} AND result = 'lost') AS losses,
					count(*) FILTER (WHERE outcome = 'retired' AND result = 'won') AS wins_by_retirement,
					count(*) FILTER (WHERE outcome = 'retired' AND result = 'lost') AS losses_by_retirement,
					count(*) FILTER (WHERE outcome = 'walkover' AND result = 'won') AS walkovers_won,
					count(*) FILTER (WHERE outcome = 'walkover' AND result = 'lost') AS walkovers_lost,
					sum(sets_won) FILTER (WHERE outcome IN {RESULT_OUTCOMES}) AS sets_won,
					sum(sets_lost) FILTER (WHERE outcome IN {RESULT_OUTCOMES}) AS sets_lost,
					sum(games_won) FILTER (WHERE outcome IN {RESULT_OUTCOMES}) AS games_won,
					sum(games_lost) FILTER (WHERE outcome IN {RESULT_OUTCOMES}) AS games_lost,
					count(*) FILTER (WHERE outcome IN {RESULT_OUTCOMES} AND stats_source IS NOT NULL) AS matches_with_stats,
					sum(aces) AS aces, sum(double_faults) AS double_faults,
					sum(service_games_played) FILTER (WHERE aces IS NOT NULL) AS serve_stat_games,
					sum(service_games_played) AS service_games_played, sum(service_games_won) AS service_games_won,
					sum(bp_faced) AS bp_faced, sum(bp_saved) AS bp_saved,
					sum(return_games_played) AS return_games_played, sum(return_games_won) AS return_games_won,
					sum(bp_created) AS bp_created, sum(bp_converted) AS bp_converted,
					max(start_ts) FILTER (WHERE outcome IN {RESULT_OUTCOMES}) AS last_match_utc
				FROM ps WHERE {_scope_filter(sc)} GROUP BY player_id""")
		self._load("player_summary", f"""
			SELECT a.player_id, a.scope, e.elo, coalesce(e.elo_matches, 0) AS elo_matches,
				a.matches_played, a.wins, a.losses, a.wins_by_retirement, a.losses_by_retirement,
				a.walkovers_won, a.walkovers_lost, a.sets_won, a.sets_lost, a.games_won, a.games_lost,
				a.matches_with_stats, a.aces, a.double_faults, a.serve_stat_games,
				a.service_games_played, a.service_games_won, a.bp_faced, a.bp_saved,
				a.return_games_played, a.return_games_won, a.bp_created, a.bp_converted,
				round(a.service_games_won / nullif(a.service_games_played, 0), 4) AS hold_pct,
				round(a.return_games_won / nullif(a.return_games_played, 0), 4) AS return_games_won_pct,
				round(a.bp_saved / nullif(a.bp_faced, 0), 4) AS bp_save_pct,
				round(a.bp_converted / nullif(a.bp_created, 0), 4) AS bp_convert_pct,
				round(a.aces / nullif(a.serve_stat_games, 0), 4) AS aces_per_service_game,
				a.last_match_utc
			FROM ({' UNION ALL '.join(parts)}) a
			LEFT JOIN elo e ON e.player_id = a.player_id AND e.scope = a.scope
			WHERE a.matches_played > 0 OR a.walkovers_won > 0 OR a.walkovers_lost > 0
		""")

	def load_player_form(self) -> None:
		doubles = " UNION ALL ".join(
			f"SELECT {p} AS player_id, CASE WHEN winner_side = '{side}' THEN 1 ELSE 0 END AS won FROM m "
			f"WHERE match_type = 'doubles' AND outcome IN {RESULT_OUTCOMES} AND winner_side IS NOT NULL AND {p} IS NOT NULL AND NOT excluded_category"
			for side, p in (("home", "hp1"), ("home", "hp2"), ("away", "ap1"), ("away", "ap2"))
		)
		self._load("player_form", f"""
			WITH last5 AS (
				SELECT player_id, result, service_games_played, service_games_won,
					row_number() OVER (PARTITION BY player_id ORDER BY start_ts DESC, match_id DESC) AS rn
				FROM ps WHERE outcome IN {RESULT_OUTCOMES} AND result IS NOT NULL
			),
			form AS (
				SELECT player_id,
					string_agg(CASE result WHEN 'won' THEN 'W' ELSE 'L' END, ',' ORDER BY rn) AS results,
					count(*) FILTER (WHERE result = 'won') AS wins,
					count(*) FILTER (WHERE result = 'lost') AS losses,
					round(sum(service_games_won) / nullif(sum(service_games_played), 0), 4) AS hold_pct
				FROM last5 WHERE rn <= 5 GROUP BY player_id
			),
			dbl AS (SELECT player_id, count(*) AS n, sum(won) AS w FROM ({doubles}) GROUP BY player_id)
			SELECT coalesce(f.player_id, d.player_id) AS player_id,
				coalesce(f.results, '') AS results, coalesce(f.wins, 0) AS wins, coalesce(f.losses, 0) AS losses, f.hold_pct,
				coalesce(d.n, 0) AS doubles_matches, coalesce(d.w, 0) AS doubles_wins, coalesce(d.n - d.w, 0) AS doubles_losses
			FROM form f FULL OUTER JOIN dbl d ON d.player_id = f.player_id
		""")

	def load_leaderboard(self) -> None:
		cutoff = f"TIMESTAMP '{self.data_through}' - INTERVAL {LEADERBOARD_ACTIVE_DAYS} DAY"
		self._load("elo_leaderboard", f"""
			WITH base AS (
				SELECT CASE p.gender WHEN 'M' THEN 'atp' WHEN 'F' THEN 'wta' END AS tour, e.scope AS surface,
					e.player_id, p.name, p.country, e.elo, e.elo_matches AS matches, s.last_match_utc
				FROM elo e
				JOIN appear_players p ON p.player_id = e.player_id
				LEFT JOIN (SELECT player_id, max(start_ts) AS last_match_utc FROM ps WHERE outcome IN {RESULT_OUTCOMES} GROUP BY player_id) s
					ON s.player_id = e.player_id
				WHERE p.gender IN ('M', 'F')
			),
			ranked AS (
				SELECT tour, surface, 1 AS active_only, row_number() OVER (PARTITION BY tour, surface ORDER BY elo DESC, player_id) AS rank_no,
					player_id, name, country, elo, matches, last_match_utc
				FROM base WHERE matches >= {LEADERBOARD_MIN_MATCHES} AND last_match_utc >= {cutoff}
				UNION ALL
				SELECT tour, surface, 0, row_number() OVER (PARTITION BY tour, surface ORDER BY elo DESC, player_id),
					player_id, name, country, elo, matches, last_match_utc
				FROM base
			)
			SELECT tour, surface, active_only, rank_no, player_id, name, country, elo, matches, last_match_utc FROM ranked
		""")

	def run(self) -> int:
		started = datetime.now(timezone.utc).replace(tzinfo=None)
		with self.my.cursor() as cur:
			cur.execute("INSERT INTO load_runs (started_utc, status) VALUES (%s, 'running')", (started,))
			run_id = cur.lastrowid
		try:
			t0 = time.time()
			self.register_sources()
			self.assign_ids()
			self.build_rows()
			self.load_rows()
			self.compute_elo()
			self.load_player_summary()
			self.load_player_form()
			self.load_leaderboard()
			self._swap_all()
			with self.my.cursor() as cur:
				cur.execute(
					"UPDATE load_runs SET finished_utc=%s, status='success', parser_version=%s, source_months=%s, data_through=%s, row_counts=%s WHERE run_id=%s",
					(datetime.now(timezone.utc).replace(tzinfo=None), ",".join(self.parser_versions), self.source_months,
					 self.data_through, json.dumps(self.counts), run_id),
				)
			print(f"[load] done in {time.time() - t0:.0f}s (run {run_id}, data through {self.data_through})", flush=True)
			return run_id
		except Exception as exc:
			self._drop_staging()
			with self.my.cursor() as cur:
				cur.execute("UPDATE load_runs SET finished_utc=%s, status='failed', error=%s WHERE run_id=%s",
							(datetime.now(timezone.utc).replace(tzinfo=None), repr(exc)[:4000], run_id))
			raise


def load(root: str) -> int:
	loader = Loader(os.path.abspath(root))
	try:
		return loader.run()
	finally:
		loader.close()


def main() -> int:
	parser = argparse.ArgumentParser(description="Load RacketEdge 3.0 serving tables from clean Parquet.")
	parser.add_argument("--root", required=True, help="Library root, e.g. ./library")
	args = parser.parse_args()
	load(args.root)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
