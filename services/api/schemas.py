from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, field_validator, model_validator


# ---- Shared tennis building blocks (RacketEdge 3.0) ----

class ApiMatchPlayer(BaseModel):
	id: Optional[int] = None
	name: Optional[str] = None
	country: Optional[str] = None  # ISO alpha-3


class ApiTournament(BaseModel):
	id: Optional[int] = None
	name: Optional[str] = None
	circuit: Optional[str] = None      # "ATP" | "WTA" | "Challenger" | "ITF Men" | ...
	level: Optional[int] = None        # ranking points of the event: 250 / 500 / 1000 / 2000 (Slam)
	surface: Optional[str] = None      # "hard" | "clay" | "grass" | "carpet" | "synthetic"
	environment: Optional[str] = None  # "indoor" | "outdoor"
	round: Optional[str] = None


class ApiStatus(BaseModel):
	phase: str                         # "upcoming" | "live" | "completed" | "suspended" | "postponed" | "cancelled"
	outcome: str                       # "completed" | "retired" | "walkover" | "defaulted" | "scheduled" | "live" | ...
	description: Optional[str] = None


class TiebreakScore(BaseModel):
	home: int
	away: int


class SetScore(BaseModel):
	set: int
	home: int
	away: int
	tiebreak: Optional[TiebreakScore] = None


class SidesInt(BaseModel):
	home: Optional[int] = None
	away: Optional[int] = None


class MatchScore(BaseModel):
	sets: SidesInt
	set_scores: List[SetScore]
	winner: Optional[str] = None       # "home" | "away"


class ApiAvailability(BaseModel):
	has_stats: bool
	stats_status: str                  # "ok" | "suspect" | "none"
	has_pbp: bool
	pbp_status: str                    # "complete" | "partial" | "none"
	points_count: Optional[int] = None


class ApiMatch(BaseModel):
	event_id: int
	scheduled_at: Optional[str] = None  # ISO 8601 UTC
	format: str = "singles"
	tournament: ApiTournament
	home: ApiMatchPlayer
	away: ApiMatchPlayer
	status: ApiStatus
	score: MatchScore
	availability: ApiAvailability


class ApiMatchesResponse(BaseModel):
	date: str
	total: int
	matches: List[ApiMatch]


class DoublesTeam(BaseModel):
	name: Optional[str] = None
	players: List[ApiMatchPlayer]


class ApiDoublesMatch(BaseModel):
	event_id: int
	scheduled_at: Optional[str] = None
	format: str = "doubles"
	tournament: ApiTournament
	home: DoublesTeam
	away: DoublesTeam
	status: ApiStatus
	score: MatchScore
	availability: ApiAvailability


class ApiDoublesMatchesResponse(BaseModel):
	date: str
	total: int
	matches: List[ApiDoublesMatch]


# ---- ML Prediction ----
class PredictMatchRequest(BaseModel):
	player_a_id: int
	player_b_id: int
	surface: Optional[Literal["hard", "clay", "grass", "indoor"]] = None  # None = overall ratings

	@field_validator("surface", mode="before")
	@classmethod
	def normalise_surface(cls, v):
		# Case-insensitive; anything outside the four model surfaces is a 422
		return v.strip().lower() if isinstance(v, str) else v

	@model_validator(mode="after")
	def different_players(self):
		if self.player_a_id == self.player_b_id:
			raise ValueError("player_a_id and player_b_id must be different players")
		return self


class PredictPlayer(BaseModel):
	id: int
	name: Optional[str] = None
	win_probability: float  # 0..1, rounded to 4 dp


class PredictMatchMeta(BaseModel):
	features_used: List[str] = []


class PredictMatchResponse(BaseModel):
	player_a: PredictPlayer
	player_b: PredictPlayer
	surface: Optional[str] = None
	model: str
	generated_at: str  # ISO 8601 UTC
	meta: PredictMatchMeta


# ---- Player profile ----
def _round4(v):
	return round(float(v), 4) if v is not None else None


class PlayerElo(BaseModel):
	overall: Optional[float] = None
	hard: Optional[float] = None
	clay: Optional[float] = None
	grass: Optional[float] = None
	indoor: Optional[float] = None

	@field_validator("overall", "hard", "clay", "grass", "indoor", mode="before")
	@classmethod
	def round_elo(cls, v):
		return round(float(v), 2) if v is not None else None


class WalkoverCounts(BaseModel):
	won: int = 0
	lost: int = 0


class ServeStats(BaseModel):
	matches_with_stats: int = 0
	aces: Optional[int] = None
	double_faults: Optional[int] = None
	aces_per_service_game: Optional[float] = None
	hold_pct: Optional[float] = None
	bp_save_pct: Optional[float] = None

	@field_validator("aces_per_service_game", "hold_pct", "bp_save_pct", mode="before")
	@classmethod
	def r4(cls, v):
		return _round4(v)


class ReturnStats(BaseModel):
	return_games_won_pct: Optional[float] = None
	bp_convert_pct: Optional[float] = None

	@field_validator("return_games_won_pct", "bp_convert_pct", mode="before")
	@classmethod
	def r4(cls, v):
		return _round4(v)


class PlayerCareer(BaseModel):
	matches_played: int = 0
	wins: int = 0
	losses: int = 0
	wins_by_retirement: int = 0
	losses_by_retirement: int = 0
	walkovers: WalkoverCounts = WalkoverCounts()
	sets_won: Optional[int] = None
	sets_lost: Optional[int] = None
	games_won: Optional[int] = None
	games_lost: Optional[int] = None
	serve: ServeStats = ServeStats()
	return_: ReturnStats = ReturnStats()

	model_config = {"populate_by_name": True}


class SurfaceRecord(BaseModel):
	matches_played: int = 0
	wins: int = 0
	losses: int = 0
	hold_pct: Optional[float] = None
	return_games_won_pct: Optional[float] = None

	@field_validator("hold_pct", "return_games_won_pct", mode="before")
	@classmethod
	def r4(cls, v):
		return _round4(v)


class PlayerRecentForm(BaseModel):
	window: int = 5
	results: List[str] = []           # newest first, e.g. ["W", "W", "L", "W", "W"]
	wins: int = 0
	losses: int = 0
	hold_pct: Optional[float] = None

	@field_validator("hold_pct", mode="before")
	@classmethod
	def r4(cls, v):
		return _round4(v)


class DoublesRecord(BaseModel):
	matches_played: int = 0
	wins: int = 0
	losses: int = 0


class PlayerProfile(BaseModel):
	player_id: int
	name: Optional[str] = None
	short_name: Optional[str] = None
	country: Optional[str] = None
	gender: Optional[str] = None
	elo: PlayerElo
	career: Dict[str, Any]            # PlayerCareer, serialised with "return" as a key
	by_surface: Dict[str, SurfaceRecord] = {}
	recent_form: PlayerRecentForm
	doubles: DoublesRecord
	last_match_at: Optional[str] = None
	last_updated_at: Optional[str] = None


# ---- Player search ----
class PlayerSearchResult(BaseModel):
	id: int
	name: str
	short_name: Optional[str] = None
	country: Optional[str] = None
	gender: Optional[str] = None
	elo_overall: Optional[float] = None
	matches_played: int = 0
	last_match_at: Optional[str] = None

	@field_validator("elo_overall", mode="before")
	@classmethod
	def round_elo(cls, v):
		return round(float(v), 2) if v is not None else None


class PlayerSearchResponse(BaseModel):
	query: str
	total: int
	players: List[PlayerSearchResult]


# ---- Player matches ----
class MatchResult(BaseModel):
	result: Optional[str] = None       # "won" | "lost" | null (no result)
	outcome: str                       # "completed" | "retired" | "walkover" | ...
	sets_won: Optional[int] = None
	sets_lost: Optional[int] = None
	games_won: Optional[int] = None
	games_lost: Optional[int] = None


class PlayerMatchRow(BaseModel):
	event_id: int
	played_at: Optional[str] = None
	format: str
	side: str                          # "home" | "away"
	tournament: ApiTournament
	opponent: Optional[ApiMatchPlayer] = None       # singles
	partner: Optional[ApiMatchPlayer] = None        # doubles
	opponents: Optional[List[ApiMatchPlayer]] = None  # doubles
	result: MatchResult
	score: MatchScore
	availability: ApiAvailability


class PlayerMatchesResponse(BaseModel):
	player_id: int
	name: Optional[str] = None
	format: str
	page: int
	page_size: int
	total: int
	total_pages: int
	has_next_page: bool
	matches: List[PlayerMatchRow]


# ---- Elo leaderboard ----
class LeaderboardEntry(BaseModel):
	rank: int
	id: int
	name: Optional[str] = None
	country: Optional[str] = None
	elo: float
	matches: int
	last_match_at: Optional[str] = None

	@field_validator("elo", mode="before")
	@classmethod
	def round_elo(cls, v):
		return round(float(v), 2) if v is not None else v


class LeaderboardResponse(BaseModel):
	tour: str        # atp | wta
	surface: str     # overall | hard | clay | grass | indoor
	active_only: bool
	page: int
	page_size: int
	total_players: int
	total_pages: int
	has_next_page: bool
	players: List[LeaderboardEntry]


# ---- Point-by-point ----
class SidesStr(BaseModel):
	home: Optional[str] = None
	away: Optional[str] = None


class PbpPoint(BaseModel):
	point: int
	server: Optional[str] = None
	score_before: SidesStr
	score_after: Optional[SidesStr] = None  # null for the game-winning point of a regular game
	winner: Optional[str] = None
	break_point: bool
	game_point: bool
	deciding_point: bool


class PbpGame(BaseModel):
	game: int
	server: Optional[str] = None
	winner: Optional[str] = None
	result: Optional[str] = None       # "hold" | "break" | "tiebreak"
	is_tiebreak: bool
	points: List[PbpPoint]


class PbpSet(BaseModel):
	set: int
	score: SidesInt
	games: List[PbpGame]


class PbpSide(BaseModel):
	id: Optional[int] = None
	name: Optional[str] = None
	players: Optional[List[ApiMatchPlayer]] = None  # doubles only


class PbpServiceStats(BaseModel):
	aces: Optional[int] = None
	double_faults: Optional[int] = None
	service_games_played: Optional[int] = None
	service_games_won: Optional[int] = None
	bp_faced: Optional[int] = None
	bp_saved: Optional[int] = None


class PbpSummary(BaseModel):
	score: MatchScore
	winner: Optional[PbpSide] = None
	service: Dict[str, PbpServiceStats]


class EventPbpResponse(BaseModel):
	event_id: int
	format: str
	has_pbp: bool
	pbp_status: str
	points_count: Optional[int] = None
	players: Optional[Dict[str, PbpSide]] = None
	summary: Optional[PbpSummary] = None
	sets: Optional[List[PbpSet]] = None


# ---- Head-to-head ----
class H2HStats(BaseModel):
	matches_with_stats: int = 0
	aces: int = 0
	double_faults: int = 0
	return_games_won: int = 0
	bp_saved: int = 0
	bp_faced: int = 0
	bp_converted: int = 0
	bp_created: int = 0


class H2HPlayer(BaseModel):
	id: int
	name: Optional[str] = None
	matches_won: int
	sets_won: int
	games_won: int
	stats: H2HStats


class H2HSurface(BaseModel):
	matches_played: int
	player_a_wins: int
	player_b_wins: int


class H2HResponse(BaseModel):
	surface: Optional[str] = None
	matches_played: int
	walkovers: int
	retirements: int
	player_a: H2HPlayer
	player_b: H2HPlayer
	by_surface: Dict[str, H2HSurface] = {}
	last_meetings: List[ApiMatch] = []


# ---- Auth & Private API Schemas ----
class AuthUser(BaseModel):
	id: int
	email: str
	name: Optional[str]
	role: str
	org_id: int
	status: str
	created_at: Optional[str] = None


class RegisterRequest(BaseModel):
	email: str
	password: str
	name: str


class LoginRequest(BaseModel):
	email: str
	password: str


class AuthResponse(BaseModel):
	access_token: str
	token_type: str = "bearer"
	user: AuthUser


class ApiKeyOut(BaseModel):
	id: int
	org_id: int
	product_id: str
	key_prefix: str
	name: str
	status: str
	created_at: Optional[str] = None
	last_used_at: Optional[str] = None


class ApiKeyCreateRequest(BaseModel):
	product_id: str
	name: str
	prefix: Optional[str] = "rk_live"


class NotificationChannelEmail(BaseModel):
	enabled: bool = True
	recipient: Optional[str] = None  # default to user email if None


class NotificationChannelDiscord(BaseModel):
	enabled: bool = False
	webhook_url: Optional[str] = None


class NotificationChannelTelegram(BaseModel):
	enabled: bool = False
	chat_id: Optional[str] = None


class NotificationChannels(BaseModel):
	email: NotificationChannelEmail = NotificationChannelEmail()
	discord: NotificationChannelDiscord = NotificationChannelDiscord()
	telegram: NotificationChannelTelegram = NotificationChannelTelegram()


class NotificationPrefsOut(BaseModel):
	timezone: str
	send_time_local: str  # "HH:MM"
	channels: NotificationChannels
	template_text: str
	selected_fields: List[str]
	status: str
	last_sent_date: Optional[str] = None  # "YYYY-MM-DD"


class NotificationPrefsIn(BaseModel):
	timezone: str
	send_time_local: str  # "HH:MM"
	channels: NotificationChannels
	template_text: str
	selected_fields: List[str]
	status: Optional[str] = "active"


class NotificationTestRequest(BaseModel):
	channel: Optional[str] = None  # "email" | "discord" | "telegram" or None for all enabled
	override_message: Optional[str] = None


class NotificationEndpointBase(BaseModel):
	service: str  # "email" | "discord" | "telegram"
	timezone: str
	send_time_local: str  # "HH:MM"
	config: Dict[str, Any]  # per-service config: recipient/webhook_url/chat_id
	template_text: str
	selected_fields: List[str] = []
	status: Optional[str] = "active"


class NotificationEndpointCreate(NotificationEndpointBase):
	pass


class NotificationEndpointUpdate(BaseModel):
	timezone: Optional[str] = None
	send_time_local: Optional[str] = None
	config: Optional[Dict[str, Any]] = None
	template_text: Optional[str] = None
	selected_fields: Optional[List[str]] = None
	status: Optional[str] = None


class NotificationEndpointOut(NotificationEndpointBase):
	id: int
	last_sent_date: Optional[str] = None


# ---- Connected Accounts ----
class ConnectedAccountOut(BaseModel):
	id: int
	provider: str  # 'telegram' | 'discord'
	external_user_id: Optional[str] = None
	display_name: Optional[str] = None
	status: str
	metadata: Optional[Dict[str, Any]] = None
	created_at: Optional[str] = None


class DiscordConnectRequest(BaseModel):
	webhook_url: str
	name: Optional[str] = None


class TelegramConnectTokenOut(BaseModel):
	token: str
	expires_at: str
	deep_link: Optional[str] = None
	instructions: Optional[str] = None