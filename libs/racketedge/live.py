"""
Live upsert: apply just-fetched matches to the serving tables within seconds.

Runs the same parser and the same serving SQL as the hourly rebuild
(libs/racketedge/serving_sql.py) on a small batch, then upserts the resulting rows:
matches / doubles_matches / player_match_stats are replaced per row, pbp_points are
replaced per match, new players and tournaments are inserted. Aggregates (Elo, career,
form, leaderboard) are left to the hourly rebuild.

Callers must pass the latest known statistics and point-by-point payload for every event
(see latest_payloads); passing None means "this match has none", not "unchanged".
"""
import gzip
import json
import os
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import duckdb
import pandas as pd

from . import archive, landing
from . import serving_sql as sql
from .clean_schema import parse_events, to_table

Payloads = Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]


def assert_racketedge(conn) -> None:
	"""Refuse to write unless the connection points at a RacketEdge 3.0 database."""
	with conn.cursor() as cur:
		cur.execute("SELECT DATABASE(), COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'load_runs'")
		name, n = cur.fetchone()
	if not n:
		raise RuntimeError(f"database '{name}' is not a RacketEdge 3.0 database (no load_runs table); check APP_ENV")


def latest_payloads(root: str, kind: str, day: date) -> Dict[int, Any]:
	"""Latest payload per event for one day: archive day file overlaid with landing files."""
	col = archive.json_column(kind)
	out: Dict[int, Any] = {}
	path = archive.day_path(root, kind, day)
	if os.path.exists(path):
		with gzip.open(path, "rt", encoding="utf-8") as f:
			for line in f:
				rec = json.loads(line)
				out[int(rec["event_id"])] = rec.get(col)
	day_dir = os.path.join(landing.landing_root(root), kind, day.isoformat())
	if os.path.isdir(day_dir):
		for name in os.listdir(day_dir):
			if name.endswith(".json"):
				with open(os.path.join(day_dir, name), encoding="utf-8") as f:
					rec = json.load(f)
				out[int(rec["event_id"])] = rec.get(col)
	return out


def _rows(db, query: str) -> Tuple[List[str], List[tuple]]:
	rel = db.sql(query)
	return rel.columns, rel.fetchall()


def _replace(cur, table: str, columns: List[str], rows: List[tuple]) -> None:
	if not rows:
		return
	cols = ", ".join(f"`{c}`" for c in columns)
	marks = ", ".join(["%s"] * len(columns))
	cur.executemany(f"REPLACE INTO {table} ({cols}) VALUES ({marks})", rows)


def _assign_ids(conn, db) -> None:
	"""Append new source ids, then register only this batch's id maps in DuckDB."""
	cols = [f"{s}_player{i}_id" for s in ("home", "away") for i in (1, 2)]
	players = [int(r[0]) for r in db.sql(" UNION ".join(f"SELECT {c} FROM cm WHERE {c} IS NOT NULL" for c in cols)).fetchall()]
	events = [int(r[0]) for r in db.sql("SELECT event_id FROM cm").fetchall()]
	tourns = [int(r[0]) for r in db.sql("SELECT DISTINCT tournament_id FROM cm WHERE tournament_id IS NOT NULL").fetchall()]
	with conn.cursor() as cur:
		for table, id_col, src_col, ids in (
			("id_players", "player_id", "source_player_id", players),
			("id_matches", "match_id", "source_event_id", events),
			("id_tournaments", "tournament_id", "source_tournament_id", tourns),
		):
			if ids:
				cur.executemany(f"INSERT IGNORE INTO {table} ({src_col}) VALUES (%s)", [(i,) for i in ids])
				marks = ",".join(["%s"] * len(ids))
				cur.execute(f"SELECT {id_col}, {src_col} FROM {table} WHERE {src_col} IN ({marks})", ids)
				found = list(cur.fetchall())
			else:
				found = []
			db.register(f"{table}_df", pd.DataFrame(found, columns=[id_col, src_col]).astype("int64"))
			db.sql(f"CREATE TABLE {table} AS SELECT * FROM {table}_df")


def upsert(conn, items: Iterable[Payloads]) -> Dict[str, int]:
	"""Parse and upsert a batch of (event, statistics, pbp) payloads. Returns row counts."""
	items = list(items)
	if not items:
		return {}
	assert_racketedge(conn)
	parsed = parse_events(items)
	db = duckdb.connect()
	db.sql("SET TimeZone = 'UTC'")
	for name, table in (("cm", "matches"), ("cp", "player_match_stats"), ("cpts", "pbp_points")):
		db.register(f"{name}_arrow", to_table(table, parsed[table]))
		db.sql(f"CREATE TABLE {name} AS SELECT * FROM {name}_arrow")

	_assign_ids(conn, db)
	sql.create_core(db)
	sql.create_appearances(db)
	sql.create_player_sides(db)
	now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

	counts: Dict[str, int] = {}
	with conn.cursor() as cur:
		# New players and tournaments only; names and aggregates are refreshed by the hourly rebuild
		for table, query in (("players", sql.players_select()), ("tournaments", sql.tournaments_select())):
			columns, rows = _rows(db, query)
			if rows:
				cols = ", ".join(f"`{c}`" for c in columns)
				cur.executemany(f"INSERT IGNORE INTO {table} ({cols}) VALUES ({', '.join(['%s'] * len(columns))})", rows)
			counts[table] = len(rows)
		for table, query in (
			("matches", sql.matches_select(now)),
			("doubles_matches", sql.doubles_select(now)),
			("player_match_stats", sql.player_match_stats_select()),
		):
			columns, rows = _rows(db, query)
			_replace(cur, table, columns, rows)
			counts[table] = len(rows)
		columns, rows = _rows(db, sql.pbp_points_select())
		match_ids = sorted({r[0] for r in rows})
		if match_ids:
			cur.execute(f"DELETE FROM pbp_points WHERE match_id IN ({','.join(['%s'] * len(match_ids))})", match_ids)
			cols = ", ".join(f"`{c}`" for c in columns)
			cur.executemany(f"INSERT INTO pbp_points ({cols}) VALUES ({', '.join(['%s'] * len(columns))})", rows)
		counts["pbp_points"] = len(rows)
	db.close()
	return counts
