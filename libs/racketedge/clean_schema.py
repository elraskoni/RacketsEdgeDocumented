"""Schema of the clean layer (matches, player_match_stats, pbp_points, validation).

Used by scripts/build_clean_dataset.py to write monthly Parquet and by the live path to
build the same tables in memory, so both feed the serving SQL identical inputs.
"""
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pyarrow as pa

from libs.parsers.synthetic import PLAYER_STATS_COLUMNS, POINT_COLUMNS, parse_match

TABLES = ("matches", "player_match_stats", "pbp_points", "validation")

COLUMNS: Dict[str, List[str]] = {
	# Match columns come from the parser itself so every output has the same schema
	"matches": list(parse_match({"id": 0})["match"].keys()),
	"player_match_stats": PLAYER_STATS_COLUMNS,
	"pbp_points": POINT_COLUMNS,
	"validation": ["event_id", "source", "check", "severity", "detail"],
}

_STRING_COLUMNS = {
	"tournament_name", "unique_tournament_name", "category_name", "tier", "round_name", "match_type",
	"home_name", "away_name", "status_type", "status_description", "outcome", "winner_side", "winner_source",
	"ground_type", "surface", "environment", "first_to_serve", "pbp_status", "stats_status", "parser_version",
	"side", "server", "score_before_home", "score_before_away", "score_after_home", "score_after_away",
	"point_winner", "game_winner", "source", "check", "severity", "detail",
}


def arrow_type(column: str) -> pa.DataType:
	"""Fixed type per column so every output has an identical schema."""
	if column == "start_utc":
		return pa.timestamp("us", tz="UTC")
	if column in _STRING_COLUMNS or column.endswith(("_name", "_gender", "_country")):
		return pa.string()
	if column.startswith(("is_", "has_")) or column == "final_result_only":
		return pa.bool_()
	return pa.int64()


def to_table(table: str, rows: List[Dict[str, Any]]) -> pa.Table:
	columns = COLUMNS[table]
	schema = pa.schema([(c, arrow_type(c)) for c in columns])
	return pa.Table.from_pylist([{c: r.get(c) for c in columns} for r in rows], schema=schema)


def parse_events(items: Iterable[Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]]) -> Dict[str, List[Dict[str, Any]]]:
	"""Parse (event, statistics, pbp) payloads into clean-layer row lists."""
	out: Dict[str, List[Dict[str, Any]]] = {t: [] for t in TABLES}
	for event_json, stats_json, pbp_json in items:
		parsed = parse_match(event_json, stats_json, pbp_json)
		out["matches"].append(parsed["match"])
		out["player_match_stats"].extend(parsed["player_stats"])
		out["pbp_points"].extend(parsed["points"])
		out["validation"].extend(parsed["issues"])
	return out
