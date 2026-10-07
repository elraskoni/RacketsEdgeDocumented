"""Synthetic tennis: players, tournaments and point-by-point match simulation.

Produces payloads in a small public JSON format ("synthetic feed v1") that the synthetic source
adapter (libs/parsers/synthetic) parses into the clean layer. It stands in for the private
production source so the whole pipeline (landing -> raw archive -> parser -> clean Parquet ->
serving database -> API) can run anywhere.

Matches are played point by point with real scoring: games with deuce and advantage, sets to
six with a tiebreak at 6-6, best of three (best of five in men's Grand Slams), first and
second serves, aces and double faults, retirements and walkovers. Every statistic and every
point is therefore consistent with the final score.
"""
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

SURFACES = {"hard": ("Hardcourt outdoor", "outdoor"), "indoor": ("Hardcourt indoor", "indoor"),
	"clay": ("Red clay", "outdoor"), "grass": ("Grass", "outdoor")}
COUNTRIES = ["ESP", "ITA", "FRA", "USA", "GBR", "DEU", "AUS", "ARG", "SRB", "CZE", "JPN", "CAN", "POL", "NLD", "BRA", "SWE"]
_SYL = ["ka", "lo", "mi", "ra", "ve", "to", "si", "an", "del", "mar", "ni", "co", "be", "ru", "sa", "ten", "vi", "or", "lu", "pe"]
POINT_NAMES = ["0", "15", "30", "40"]


@dataclass
class Player:
	id: int
	name: str
	short_name: str
	gender: str
	country: str
	skill: float                      # overall strength (standard deviations)
	surface_bias: Dict[str, float]    # per-surface adjustment
	ace_rate: float                   # aces per first serve in
	first_in: float                   # first-serve percentage


@dataclass
class Tournament:
	id: int
	unique_id: int
	name: str
	category: str                     # ATP, WTA, Challenger, WTA 125, ITF Men, ITF Women
	tier: str                         # main, second, lower
	points: int
	surface: str
	gender: str
	slam: bool = False
	matches: List[Dict[str, Any]] = field(default_factory=list)


def make_players(rng: random.Random, n_per_gender: int, first_id: int = 10_000) -> List[Player]:
	players, pid = [], first_id
	for gender in ("M", "F"):
		for _ in range(n_per_gender):
			first = "".join(rng.choice(_SYL) for _ in range(2)).capitalize()
			last = "".join(rng.choice(_SYL) for _ in range(rng.choice((2, 3)))).capitalize()
			players.append(Player(
				id=pid, name=f"{first} {last}", short_name=f"{first[0]}. {last}", gender=gender, country=rng.choice(COUNTRIES),
				skill=rng.gauss(0, 1), surface_bias={s: rng.gauss(0, 0.35) for s in SURFACES},
				ace_rate=max(0.02, rng.gauss(0.12 if gender == "M" else 0.06, 0.04)), first_in=min(0.75, max(0.5, rng.gauss(0.62, 0.05))),
			))
			pid += 1
	return players


def serve_win_prob(server: Player, receiver: Player, surface: str) -> float:
	"""Probability that the server wins a point, from the strength gap on this surface."""
	gap = (server.skill + server.surface_bias[surface]) - (receiver.skill + receiver.surface_bias[surface])
	base = 0.64 if server.gender == "M" else 0.57
	return min(0.82, max(0.40, base + 0.035 * gap))


class _Side:
	def __init__(self):
		self.s = dict(aces=0, double_faults=0, first_serves_in=0, first_serves_total=0, first_serve_points_won=0,
			first_serve_points_total=0, second_serves_in=0, second_serves_total=0, second_serve_points_won=0,
			second_serve_points_total=0, service_points_won=0, service_games_played=0, service_games_won=0,
			bp_faced=0, bp_saved=0, points_won=0)


def _is_break_point(server_pts: int, recv_pts: int, tiebreak: bool) -> bool:
	return not tiebreak and recv_pts >= 3 and recv_pts > server_pts


def simulate_match(rng: random.Random, home: Player, away: Player, surface: str, best_of: int,
		retire_p: float = 0.012) -> Dict[str, Any]:
	"""Play one match point by point. Returns sets, winner, outcome, per-side stats and the point log."""
	sides = {"home": _Side(), "away": _Side()}
	players = {"home": home, "away": away}
	other = {"home": "away", "away": "home"}
	server = rng.choice(("home", "away"))
	first_server = server
	sets: List[Dict[str, Optional[int]]] = []
	sets_won = {"home": 0, "away": 0}
	points: List[Dict[str, Any]] = []
	retire_at = rng.randrange(40, 200) if rng.random() < retire_p else None
	retired: Optional[str] = None

	def play_point(srv: str, tiebreak: bool, before: Tuple[str, str], set_no: int, game_no: int, games: Dict[str, int]) -> str:
		nonlocal retired
		p, ss = players[srv], sides[srv].s
		rcv = other[srv]
		p_win = serve_win_prob(p, players[rcv], surface)
		ss["first_serves_total"] += 1
		if rng.random() < p.first_in:
			ss["first_serves_in"] += 1
			ss["first_serve_points_total"] += 1
			if rng.random() < p.ace_rate:
				ss["aces"] += 1
				won = True
			else:
				won = rng.random() < min(0.9, p_win + 0.08)
			if won:
				ss["first_serve_points_won"] += 1
		else:
			ss["second_serves_total"] += 1
			ss["second_serve_points_total"] += 1
			if rng.random() < 0.08:
				ss["double_faults"] += 1
				won = False
			else:
				ss["second_serves_in"] += 1
				won = rng.random() < max(0.3, p_win - 0.12)
				if won:
					ss["second_serve_points_won"] += 1
		winner = srv if won else rcv
		if won:
			ss["service_points_won"] += 1
		sides[winner].s["points_won"] += 1
		points.append({"set": set_no, "game": game_no, "server": srv, "tiebreak": tiebreak, "winner": winner,
			"score_before": list(before), "games_before": [games["home"], games["away"]],
			"sets_before": [sets_won["home"], sets_won["away"]]})
		if retire_at is not None and len(points) >= retire_at and retired is None:
			retired = rng.choice(("home", "away"))
		return winner

	set_no = 0
	while max(sets_won.values()) < (best_of + 1) // 2 and retired is None:
		set_no += 1
		games = {"home": 0, "away": 0}
		tb_score: Optional[Dict[str, int]] = None
		game_no = 0
		while retired is None:
			game_no += 1
			if games["home"] == 6 and games["away"] == 6:
				# Tiebreak: first to 7, by 2; server changes after the first point, then every two points
				pts = {"home": 0, "away": 0}
				i, tb_server = 0, server
				while retired is None:
					srv = tb_server if i == 0 else (tb_server if ((i - 1) // 2) % 2 == 1 else other[tb_server])
					w = play_point(srv, True, (str(pts["home"]), str(pts["away"])), set_no, game_no, games)
					pts[w] += 1
					i += 1
					if max(pts.values()) >= 7 and abs(pts["home"] - pts["away"]) >= 2:
						break
				if retired is not None:
					break
				tb_winner = "home" if pts["home"] > pts["away"] else "away"
				points[-1]["game_winner"] = tb_winner
				games[tb_winner] += 1
				tb_score = pts
				server = other[tb_server]
				break
			sv, pts = server, {"home": 0, "away": 0}
			sides[sv].s["service_games_played"] += 1
			while retired is None:
				before = _game_score(pts, sv)
				if _is_break_point(pts[sv], pts[other[sv]], False):
					sides[sv].s["bp_faced"] += 1
				w = play_point(sv, False, before, set_no, game_no, games)
				if _is_break_point(pts[sv], pts[other[sv]], False) and w == sv:
					sides[sv].s["bp_saved"] += 1
				pts[w] += 1
				if max(pts.values()) >= 4 and abs(pts["home"] - pts["away"]) >= 2:
					break
			if retired is not None:
				break
			gw = "home" if pts["home"] > pts["away"] else "away"
			points[-1]["game_winner"] = gw
			if gw == sv:
				sides[sv].s["service_games_won"] += 1
			games[gw] += 1
			server = other[sv]
			if max(games.values()) >= 6 and abs(games["home"] - games["away"]) >= 2:
				break
		sets.append({"home": games["home"], "away": games["away"],
			"tb_home": tb_score["home"] if tb_score else None, "tb_away": tb_score["away"] if tb_score else None})
		if retired is None:
			sets_won["home" if games["home"] > games["away"] else "away"] += 1

	if retired is not None:
		winner, outcome = other[retired], "retired"
		if sets and sets[-1]["home"] == 0 and sets[-1]["away"] == 0 and len(points) == 0:
			sets.pop()
	else:
		winner, outcome = ("home" if sets_won["home"] > sets_won["away"] else "away"), "completed"
	stats = {s: sides[s].s for s in ("home", "away")}
	for s in ("home", "away"):
		o = stats[other[s]]
		stats[s]["return_points_won"] = (o["first_serve_points_total"] + o["second_serve_points_total"]) - o["service_points_won"]
		stats[s]["return_games_played"] = o["service_games_played"]
		stats[s]["return_games_won"] = o["service_games_played"] - o["service_games_won"]
		stats[s]["bp_created"] = o["bp_faced"]
		stats[s]["bp_converted"] = o["bp_faced"] - o["bp_saved"]
	return {"sets": sets, "winner": winner, "outcome": outcome, "first_server": first_server, "stats": stats, "points": points}


def _game_score(pts: Dict[str, int], server: str) -> Tuple[str, str]:
	h, a = pts["home"], pts["away"]
	if h >= 3 and a >= 3:
		if h == a:
			return ("40", "40")
		return ("A", "40") if h > a else ("40", "A")
	return (POINT_NAMES[min(h, 3)], POINT_NAMES[min(a, 3)])


def ts(d: date, hour: int) -> int:
	return int(datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc).timestamp())


ROUNDS = {32: ["Round of 32", "Round of 16", "Quarterfinals", "Semifinals", "Final"]}


def season(rng: random.Random, players: List[Player], start: date, end: date, now: datetime,
		first_event_id: int = 1_000_000) -> List[Dict[str, Any]]:
	"""Weekly tournaments per gender and tier, 32-player knockouts. Returns feed payloads per match."""
	by_gender = {g: sorted([p for p in players if p.gender == g], key=lambda p: -p.skill) for g in ("M", "F")}
	tiers = [("main", {"M": "ATP", "F": "WTA"}, 250, (0, 48)), ("second", {"M": "Challenger", "F": "WTA 125"}, 100, (24, 96)),
		("lower", {"M": "ITF Men", "F": "ITF Women"}, 25, (64, 10_000))]
	surfaces_by_month = {1: "hard", 2: "indoor", 3: "hard", 4: "clay", 5: "clay", 6: "grass", 7: "hard", 8: "hard",
		9: "hard", 10: "indoor", 11: "indoor", 12: "hard"}
	payloads, event_id, tid = [], first_event_id, 1
	week = start - timedelta(days=start.weekday())
	while week <= end:
		surface = surfaces_by_month[week.month]
		slam = week.isocalendar()[1] in (3, 21, 26, 35)
		for tier, cats, level, (lo, hi) in tiers:
			for gender in ("M", "F"):
				pool = by_gender[gender][lo:hi]
				if len(pool) < 32:
					continue
				city = "".join(rng.choice(_SYL) for _ in range(2)).capitalize()
				t = Tournament(id=tid, unique_id=tid if not slam else 2000 + week.isocalendar()[1], name=f"{city}, {rng.choice(COUNTRIES)}",
					category=cats[gender], tier=tier, points=2000 if (slam and tier == "main") else level, surface=surface,
					gender=gender, slam=slam and tier == "main")
				tid += 1
				draw = rng.sample(pool, 32)
				best_of = 5 if (t.slam and gender == "M") else 3
				for r, rname in enumerate(ROUNDS[32]):
					day = week + timedelta(days=min(6, r + 1))
					nxt = []
					for i in range(0, len(draw), 2):
						home, away = draw[i], draw[i + 1]
						start_ts = ts(day, 10 + (i // 2) % 9)
						event_id += 1
						if datetime.fromtimestamp(start_ts, tz=timezone.utc) > now:
							payloads.append(_payload(event_id, start_ts, t, rname, home, away, None))
							nxt.append(home)
							continue
						if rng.random() < 0.01:
							res = {"sets": [], "winner": rng.choice(("home", "away")), "outcome": "walkover", "first_server": None,
								"stats": None, "points": []}
						else:
							res = simulate_match(rng, home, away, surface, best_of)
						payloads.append(_payload(event_id, start_ts, t, rname, home, away, res))
						nxt.append(home if res["winner"] == "home" else away)
					draw = nxt
					if len(draw) < 2:
						break
		week += timedelta(days=7)
	return payloads


def _player_json(p: Player) -> Dict[str, Any]:
	return {"id": p.id, "name": p.name, "short_name": p.short_name, "gender": p.gender, "country": p.country}


def _payload(event_id: int, start_ts: int, t: Tournament, round_name: str, home: Player, away: Player,
		res: Optional[Dict[str, Any]]) -> Dict[str, Any]:
	status = "scheduled" if res is None else {"completed": "finished", "retired": "retired", "walkover": "walkover"}[res["outcome"]]
	event = {
		"id": event_id, "start_timestamp": start_ts, "round": round_name, "status": status,
		"tournament": {"id": t.id, "unique_id": t.unique_id, "name": t.name, "category": t.category, "tier": t.tier,
			"points": t.points, "surface": t.surface},
		"home": _player_json(home), "away": _player_json(away),
		"winner": None if res is None else res["winner"], "first_server": None if res is None else res["first_server"],
		"sets": [] if res is None else res["sets"],
	}
	stats = None if res is None or res["stats"] is None else {"home": res["stats"]["home"], "away": res["stats"]["away"]}
	pbp = None if res is None or not res["points"] or t.tier != "main" else {"points": res["points"]}
	return {"event": event, "statistics": stats if t.tier in ("main", "second") else None, "pbp": pbp}
