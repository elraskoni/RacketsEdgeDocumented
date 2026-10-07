import os
from datetime import date, datetime, timezone
from typing import Any, Dict, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from . import tennis_data
from .schemas import (
	ApiDoublesMatchesResponse, ApiMatchesResponse, EventPbpResponse, H2HResponse, LeaderboardResponse,
	PlayerMatchesResponse, PlayerProfile, PlayerSearchResponse, PredictMatchMeta, PredictMatchRequest,
	PredictMatchResponse, PredictPlayer,
)
from . import predict as ml_predict
from .api_auth import require_api_key
from .error_handlers import register_error_handlers
from .endpoint_flags import endpoint_guard

try:
	# Load env/dev.env if present (side-effect in config module)
	import config  # type: ignore
except Exception:
	config = None  # type: ignore


app = FastAPI(title="Racket-Edge Tennis API", version="3.0.0")
register_error_handlers(app)


def _preload_model() -> None:
	"""Load the prediction model at startup so the first /predict request is not slow."""
	try:
		ml_predict._load_artifact()
	except Exception:
		pass  # /predict reports the error per request; the rest of the API still serves


app.add_event_handler("startup", _preload_model)

# RapidAPI proxy secret — when RAPIDAPI_PROXY_SECRET is set in env, every
# request to /v1/* must carry the matching X-RapidAPI-Proxy-Secret header.
# This ensures traffic can only reach the API through RapidAPI's gateway,
# preventing billing bypass. No-op locally (env var not set in dev.env).
_RAPIDAPI_SECRET = os.getenv("RAPIDAPI_PROXY_SECRET", "").strip()

class _RapidAPIProxyGuard(BaseHTTPMiddleware):
	async def dispatch(self, request: Request, call_next):
		if _RAPIDAPI_SECRET and request.url.path.startswith("/v1/"):
			incoming = request.headers.get("X-RapidAPI-Proxy-Secret", "")
			if incoming != _RAPIDAPI_SECRET:
				return JSONResponse(
					status_code=403,
					content={"error": "forbidden", "detail": "invalid proxy secret"},
				)
		return await call_next(request)

app.add_middleware(_RapidAPIProxyGuard)

_is_prod = os.getenv("APP_ENV", "dev").lower() == "prod"

# Security headers on every response
class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
	async def dispatch(self, request: Request, call_next):
		response = await call_next(request)
		response.headers["X-Content-Type-Options"] = "nosniff"
		response.headers["X-Frame-Options"] = "DENY"
		response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
		if _is_prod:
			response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
		return response

app.add_middleware(_SecurityHeadersMiddleware)

# CORS — in production only allow explicitly configured origins.
# In dev (APP_ENV != "prod") localhost origins are added automatically.

_env_origins = [
	o.strip()
	for o in (os.getenv("FRONTEND_ORIGINS") or os.getenv("FRONTEND_ORIGIN") or "").split(",")
	if o.strip()
]

_dev_origins = [] if _is_prod else [
	"http://localhost:8080",
	"http://127.0.0.1:8080",
	"http://localhost:5173",
	"http://127.0.0.1:5173",
]

_allowed_origins = _env_origins + _dev_origins

app.add_middleware(
	CORSMiddleware,
	allow_origins=_allowed_origins,
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)


@app.get(
	"/health",
	summary="Service health check",
	description="Liveness probe. No authentication required. Returns `{\"ok\": true}` when the service is running.",
)
def health() -> Dict[str, Any]:
	return {"ok": True, "service": "racket-edge-tennis-api"}


# ---------------------------------------------------------------------------
# Tennis routes (RacketEdge 3.0). Data access lives in tennis_data.py; every route is
# one indexed read against the racketedge database. Literal paths must be declared
# before the /v1/tennis/{date} wildcard at the bottom.
# ---------------------------------------------------------------------------

_SCORE_NOTE = """Each match carries the full score (`score.set_scores` with tiebreak points), the winner, the
match `outcome` (`completed`, `retired`, `walkover`, `defaulted`, …) and an `availability` block saying
whether statistics (`stats_status`: `ok` / `suspect` / `none`) and point-by-point (`pbp_status`:
`complete` / `partial` / `none`) exist for it."""

_CIRCUIT = Query(None, description="Filter by circuit, e.g. `ATP`, `WTA`, `Challenger`, `WTA 125`, `ITF Men`.")


def _utc_today() -> date:
	return datetime.now(timezone.utc).date()


def _parse_date(value: str) -> date:
	try:
		return datetime.strptime(value, "%d-%m-%Y").date()
	except ValueError:
		raise HTTPException(status_code=400, detail="invalid_date_format — expected dd-mm-YYYY e.g. 14-01-2026")


@app.get(
	"/v1/tennis/today",
	response_model=ApiMatchesResponse,
	dependencies=[Depends(endpoint_guard("today"))],
	summary="Singles matches today (UTC)",
	description="All singles matches scheduled for the current UTC day: upcoming, live and finished.\n\n" + _SCORE_NOTE,
)
def get_tennis_today(circuit: Optional[str] = _CIRCUIT, _key=Depends(require_api_key)) -> ApiMatchesResponse:
	day = _utc_today()
	matches = tennis_data.matches_on(day, circuit)
	return ApiMatchesResponse(date=day.isoformat(), total=len(matches), matches=matches)


@app.get(
	"/v1/tennis/live",
	response_model=ApiMatchesResponse,
	dependencies=[Depends(endpoint_guard("live"))],
	summary="Live singles matches",
	description="Singles matches currently in progress (`status.phase = \"live\"`). Updated by the live poller "
				"every ~30 seconds during play; empty when nothing is being played.\n\n" + _SCORE_NOTE,
)
def get_tennis_live(circuit: Optional[str] = _CIRCUIT, _key=Depends(require_api_key)) -> ApiMatchesResponse:
	matches = tennis_data.live_matches(circuit)
	return ApiMatchesResponse(date=_utc_today().isoformat(), total=len(matches), matches=matches)


@app.get(
	"/v1/tennis/doubles/today",
	response_model=ApiDoublesMatchesResponse,
	dependencies=[Depends(endpoint_guard("doubles"))],
	summary="Doubles matches today (UTC)",
	description="All doubles matches for the current UTC day. Each side lists both players (with their "
				"RacketEdge player ids, the same ids used for singles).\n\n" + _SCORE_NOTE,
)
def get_doubles_today(circuit: Optional[str] = _CIRCUIT, _key=Depends(require_api_key)) -> ApiDoublesMatchesResponse:
	day = _utc_today()
	matches = tennis_data.matches_on(day, circuit, doubles=True)
	return ApiDoublesMatchesResponse(date=day.isoformat(), total=len(matches), matches=matches)


@app.get(
	"/v1/tennis/doubles/live",
	response_model=ApiDoublesMatchesResponse,
	dependencies=[Depends(endpoint_guard("doubles"))],
	summary="Live doubles matches",
	description="Doubles matches currently in progress.\n\n" + _SCORE_NOTE,
)
def get_doubles_live(circuit: Optional[str] = _CIRCUIT, _key=Depends(require_api_key)) -> ApiDoublesMatchesResponse:
	matches = tennis_data.live_matches(circuit, doubles=True)
	return ApiDoublesMatchesResponse(date=_utc_today().isoformat(), total=len(matches), matches=matches)


@app.get(
	"/v1/tennis/doubles/{date}",
	response_model=ApiDoublesMatchesResponse,
	dependencies=[Depends(endpoint_guard("doubles"))],
	summary="Doubles matches for a date",
	description="All doubles matches on the given UTC date. **Date format**: `dd-mm-YYYY`, e.g. `14-01-2026`.\n\n" + _SCORE_NOTE,
)
def get_doubles_by_date(date: str, circuit: Optional[str] = _CIRCUIT, _key=Depends(require_api_key)) -> ApiDoublesMatchesResponse:
	day = _parse_date(date)
	matches = tennis_data.matches_on(day, circuit, doubles=True)
	return ApiDoublesMatchesResponse(date=day.isoformat(), total=len(matches), matches=matches)


@app.get(
	"/v1/tennis/elo/leaderboard",
	response_model=LeaderboardResponse,
	dependencies=[Depends(endpoint_guard("leaderboard"))],
	summary="Elo leaderboard",
	description="""Singles Elo ranking for one tour and surface, paginated.

- `tour`: `atp` (men) or `wta` (women). Elo is only comparable within a tour.
- `surface`: `overall`, `hard` (all hard courts), `clay`, `grass`, `indoor` (indoor hard courts).
- By default only **active** players are ranked: a match in the 52 weeks before the latest data and at least
  10 rated matches. `include_inactive=true` ranks everyone with a rating.

Ratings are computed from singles results (walkovers, exhibitions and legends events excluded) and refreshed daily.""",
)
def get_elo_leaderboard(
	tour: Literal["atp", "wta"] = "atp",
	surface: Literal["overall", "hard", "clay", "grass", "indoor"] = "overall",
	include_inactive: bool = False,
	page: int = Query(1, ge=1),
	page_size: int = Query(50, ge=1, le=500),
	_key=Depends(require_api_key),
) -> LeaderboardResponse:
	return tennis_data.leaderboard(tour, surface, not include_inactive, page, page_size)


@app.post(
	"/v1/tennis/predict",
	response_model=PredictMatchResponse,
	dependencies=[Depends(endpoint_guard("predict"))],
	summary="Predict match win probability",
	description="""Model-derived win probabilities for a singles matchup.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `player_a_id` | integer | yes | RacketEdge player ID |
| `player_b_id` | integer | yes | RacketEdge player ID |
| `surface` | string | no | `hard` · `clay` · `grass` · `indoor` (case-insensitive); omit for overall |

Probabilities sum to 1.0 and do not depend on which player is passed first.
Errors: `422 validation_error` for an unknown surface, the same player on both sides, or a malformed body;
`404 player_not_found` for an unknown player ID.

> Experimental estimates — not financial advice.""",
)
def predict_match(req: PredictMatchRequest, _key=Depends(require_api_key)) -> PredictMatchResponse:
	profile_a = tennis_data.player_profile(req.player_a_id)
	profile_b = tennis_data.player_profile(req.player_b_id)
	try:
		p_a, p_b, feat_names = ml_predict.predict_two_players(req.player_a_id, req.player_b_id, req.surface)
	except Exception as e:
		raise HTTPException(status_code=500, detail=f"prediction_error: {e}")
	return PredictMatchResponse(
		player_a=PredictPlayer(id=req.player_a_id, name=profile_a.name, win_probability=round(p_a, 4)),
		player_b=PredictPlayer(id=req.player_b_id, name=profile_b.name, win_probability=round(p_b, 4)),
		surface=req.surface,
		model="RacketEdgeModelV1",
		generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		meta=PredictMatchMeta(features_used=feat_names),
	)


@app.get(
	"/v1/tennis/players/search",
	response_model=PlayerSearchResponse,
	dependencies=[Depends(endpoint_guard("player_search"))],
	summary="Search players by name",
	description="""Find players by name and get their RacketEdge IDs.

- Case- and accent-insensitive: `stepanek` finds "Radek Štěpánek".
- Every word of `q` must match the start of a word in the name: `alca` → Carlos Alcaraz, `novak djok` → Novak Djokovic.
- Ranked by exact name, then surname match, then most recent activity and Elo.

Names are not unique (several players can share one), so use the returned `id` with `/v1/tennis/player/{player_id}`.""",
)
def search_players(
	q: str = Query(..., min_length=2, max_length=64, description="Name or part of a name"),
	gender: Optional[Literal["M", "F"]] = Query(None, description="`M` or `F`"),
	country: Optional[str] = Query(None, min_length=3, max_length=3, description="ISO alpha-3 country code, e.g. `ESP`"),
	limit: int = Query(10, ge=1, le=50),
	_key=Depends(require_api_key),
) -> PlayerSearchResponse:
	return tennis_data.search_players(q, gender, country, limit)


@app.get(
	"/v1/tennis/player/{player_id}",
	response_model=PlayerProfile,
	dependencies=[Depends(endpoint_guard("player_profile"))],
	summary="Player profile",
	description="""Profile for one player: Elo per surface, singles career record and stats, per-surface record,
last-5 form and doubles record. Returns `404` for an unknown ID (use `/v1/tennis/players/search` to find IDs).

- `career.wins` / `losses` include retirements; `wins_by_retirement` / `losses_by_retirement` show how many.
  Walkovers are not counted as played and are reported separately under `walkovers`.
- Serve and return figures use only matches with reliable statistics (`career.serve.matches_with_stats`).
- `recent_form.results` is newest first and includes the most recent match.""",
)
def get_player_profile(player_id: int, _key=Depends(require_api_key)) -> PlayerProfile:
	return tennis_data.player_profile(player_id)


@app.get(
	"/v1/tennis/player/{player_id}/matches",
	response_model=PlayerMatchesResponse,
	dependencies=[Depends(endpoint_guard("player_matches"))],
	summary="Player match history",
	description="""A player's matches, newest first, paginated. `format=singles` (default) or `format=doubles`.

Each row has the tournament, opponent (or partner and opponents for doubles), the player's result
(`won` / `lost`, with `outcome` showing retirements and walkovers), the full score and `availability`.
Use `availability.has_pbp` to find matches available from `/v1/tennis/event/{event_id}/pbp`.""",
)
def get_player_matches(
	player_id: int,
	format: Literal["singles", "doubles"] = "singles",
	page: int = Query(1, ge=1),
	page_size: int = Query(50, ge=1, le=200),
	_key=Depends(require_api_key),
) -> PlayerMatchesResponse:
	return tennis_data.player_matches(player_id, format, page, page_size)


@app.get(
	"/v1/tennis/event/{event_id}/pbp",
	response_model=EventPbpResponse,
	dependencies=[Depends(endpoint_guard("pbp"))],
	summary="Point-by-point data for a match",
	description="""Every point of an ATP or WTA match (singles or doubles), in order: sets → games → points.

**Match level**
- `pbp_status`: `complete` (every point reconciles with the official score — for a live match, with the
  score so far) or `partial` (the source log has gaps).
- `summary.service`: aces and double faults from official statistics; service games and break points
  counted from the point log.

**Game fields**: `server`, `winner`, `result` (`hold` · `break` · `tiebreak`), `is_tiebreak`.

**Point fields**: `server`, `score_before`, `score_after` (`null` on the game-winning point of a regular game,
where the game ends), `winner`, and context flags `break_point`, `game_point`, `deciding_point` (no-ad scoring).

Returns `has_pbp: false` when point-by-point data is not available for the match.""",
)
def get_event_pbp(event_id: int, _key=Depends(require_api_key)) -> EventPbpResponse:
	return tennis_data.event_pbp(event_id)


@app.get(
	"/v1/tennis/h2h",
	response_model=H2HResponse,
	dependencies=[Depends(endpoint_guard("h2h"))],
	summary="Head-to-head record between two players",
	description="""Singles head-to-head between two players: `?player_a_id=…&player_b_id=…`, optionally `&surface=clay`.

- `matches_played` counts results (retirements included, shown in `retirements`); walkovers are listed separately.
- Per player: matches, sets and games won, and statistics totals over matches with reliable stats.
- `by_surface` breakdown and the last five meetings with full scores.""",
)
def get_h2h(
	player_a_id: int,
	player_b_id: int,
	surface: Optional[Literal["hard", "clay", "grass", "indoor"]] = None,
	_key=Depends(require_api_key),
) -> H2HResponse:
	return tennis_data.h2h(player_a_id, player_b_id, surface)


# NOTE: this wildcard route must be declared LAST — literal /v1/tennis/* routes above
# take precedence only because they are registered before it.
@app.get(
	"/v1/tennis/{date}",
	response_model=ApiMatchesResponse,
	dependencies=[Depends(endpoint_guard("date"))],
	summary="Singles matches for a date",
	description="All singles matches on the given UTC date — past results and upcoming fixtures.\n\n"
				"**Date format**: `dd-mm-YYYY`, e.g. `14-01-2026`. Returns `400` if malformed.\n\n" + _SCORE_NOTE,
)
def get_tennis_by_date(date: str, circuit: Optional[str] = _CIRCUIT, _key=Depends(require_api_key)) -> ApiMatchesResponse:
	day = _parse_date(date)
	matches = tennis_data.matches_on(day, circuit)
	return ApiMatchesResponse(date=day.isoformat(), total=len(matches), matches=matches)
