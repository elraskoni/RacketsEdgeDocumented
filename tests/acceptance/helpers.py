"""Test-data builders used by the step definitions."""
import hashlib
import uuid

SCOPES = ("overall", "hard", "clay", "grass", "indoor")


def seed_players(conn, rows):
	"""rows: dicts with id, name and an Elo per scope (overall, hard, clay, grass, indoor)."""
	with conn.cursor() as cur:
		for r in rows:
			pid = int(r["id"])
			cur.execute(
				"INSERT INTO players (player_id, name, gender, search_name, singles_matches) VALUES (%s, %s, 'M', %s, 100)",
				(pid, r["name"], r["name"].lower()),
			)
			for scope in SCOPES:
				# Identical records for everyone, so only the Elo ratings separate the players
				cur.execute(
					"""INSERT INTO player_summary (player_id, scope, elo, elo_matches, matches_played, wins, losses,
						wins_by_retirement, losses_by_retirement, walkovers_won, walkovers_lost, matches_with_stats)
					VALUES (%s, %s, %s, 100, 100, 50, 50, 0, 0, 0, 0, 0)""",
					(pid, scope, float(r[scope])),
				)


def create_api_key(conn):
	"""A fresh active key on an unlimited test plan; returns the raw key."""
	raw = "test_" + uuid.uuid4().hex
	with conn.cursor() as cur:
		cur.execute("INSERT INTO organizations (name, plan_id) VALUES (%s, 'plan-test')", ("org-" + raw,))
		org_id = cur.lastrowid
		cur.execute(
			"INSERT INTO api_keys (org_id, product_id, key_prefix, key_hash, name) VALUES (%s, 'tennis', %s, %s, 'acceptance')",
			(org_id, raw[:8], hashlib.sha256(raw.encode("utf-8")).digest()),
		)
	return raw
