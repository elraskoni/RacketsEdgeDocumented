-- RacketEdge 3.0 serving schema (tennis data). MySQL 8.4, database `racketedge`.
-- Every table below is written only by scripts/load_racketedge.py from the clean Parquet layer,
-- except the id_* tables, which own the public IDs and are only ever appended to.
-- Account/billing/admin tables are copied unchanged from the pre-3.0 database and are not defined here.

-- ---------------------------------------------------------------------------
-- Public ID maps (source id -> RacketEdge id). Seeded from the pre-3.0 database so
-- existing IDs stay valid; new entities get the next AUTO_INCREMENT value.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS id_players (
  player_id         INT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_player_id  BIGINT       NOT NULL,
  PRIMARY KEY (player_id),
  UNIQUE KEY uq_id_players_source (source_player_id)
);

CREATE TABLE IF NOT EXISTS id_matches (
  match_id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_event_id   BIGINT       NOT NULL,
  PRIMARY KEY (match_id),
  UNIQUE KEY uq_id_matches_source (source_event_id)
);

CREATE TABLE IF NOT EXISTS id_tournaments (
  tournament_id         INT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_tournament_id  BIGINT       NOT NULL,
  PRIMARY KEY (tournament_id),
  UNIQUE KEY uq_id_tournaments_source (source_tournament_id)
);

-- ---------------------------------------------------------------------------
-- Entities
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS players (
  player_id         INT UNSIGNED NOT NULL,
  name              VARCHAR(128) NOT NULL,
  short_name        VARCHAR(64)  NULL,
  gender            CHAR(1)      NULL,
  country           CHAR(3)      NULL,
  search_name       VARCHAR(128) NOT NULL,      -- lowercase, accents stripped
  singles_matches   INT UNSIGNED NOT NULL DEFAULT 0,
  doubles_matches   INT UNSIGNED NOT NULL DEFAULT 0,
  first_match_utc   DATETIME     NULL,
  last_match_utc    DATETIME     NULL,
  PRIMARY KEY (player_id),
  KEY idx_players_search (search_name),
  KEY idx_players_gender_country (gender, country)
);

CREATE TABLE IF NOT EXISTS tournaments (
  tournament_id         INT UNSIGNED NOT NULL,
  name                  VARCHAR(255) NOT NULL,
  unique_tournament_id  BIGINT       NULL,
  unique_name           VARCHAR(255) NULL,
  category              VARCHAR(64)  NULL,
  tier                  VARCHAR(16)  NOT NULL,
  level_points          SMALLINT     NULL,
  surface               VARCHAR(16)  NULL,
  environment           VARCHAR(8)   NULL,
  PRIMARY KEY (tournament_id)
);

-- ---------------------------------------------------------------------------
-- Matches
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS matches (
  match_id            INT UNSIGNED NOT NULL,
  start_utc           DATETIME     NULL,
  match_date          DATE         NULL,
  tournament_id       INT UNSIGNED NULL,
  tournament_name     VARCHAR(255) NULL,
  category            VARCHAR(64)  NULL,
  tier                VARCHAR(16)  NOT NULL,
  level_points        SMALLINT     NULL,
  round_name          VARCHAR(64)  NULL,
  surface             VARCHAR(16)  NULL,
  environment         VARCHAR(8)   NULL,
  home_player_id      INT UNSIGNED NULL,
  away_player_id      INT UNSIGNED NULL,
  home_name           VARCHAR(128) NULL,
  away_name           VARCHAR(128) NULL,
  home_country        CHAR(3)      NULL,
  away_country        CHAR(3)      NULL,
  player_lo_id        INT UNSIGNED NULL,
  player_hi_id        INT UNSIGNED NULL,
  phase               VARCHAR(16)  NOT NULL,
  outcome             VARCHAR(20)  NOT NULL,
  status_description  VARCHAR(64)  NULL,
  winner_side         CHAR(4)      NULL,
  winner_player_id    INT UNSIGNED NULL,
  home_sets           TINYINT      NULL,
  away_sets           TINYINT      NULL,
  home_s1 TINYINT NULL, away_s1 TINYINT NULL, home_tb1 TINYINT NULL, away_tb1 TINYINT NULL,
  home_s2 TINYINT NULL, away_s2 TINYINT NULL, home_tb2 TINYINT NULL, away_tb2 TINYINT NULL,
  home_s3 TINYINT NULL, away_s3 TINYINT NULL, home_tb3 TINYINT NULL, away_tb3 TINYINT NULL,
  home_s4 TINYINT NULL, away_s4 TINYINT NULL, home_tb4 TINYINT NULL, away_tb4 TINYINT NULL,
  home_s5 TINYINT NULL, away_s5 TINYINT NULL, home_tb5 TINYINT NULL, away_tb5 TINYINT NULL,
  match_tiebreak_set  TINYINT      NULL,
  has_stats           BOOLEAN      NOT NULL,
  stats_status        VARCHAR(8)   NOT NULL,
  has_pbp             BOOLEAN      NOT NULL,
  pbp_status          VARCHAR(8)   NOT NULL,
  points_count        SMALLINT     NULL,
  updated_utc         DATETIME     NOT NULL,
  PRIMARY KEY (match_id),
  KEY idx_matches_date (match_date, tier),
  KEY idx_matches_phase (phase, match_date),
  KEY idx_matches_home (home_player_id, start_utc),
  KEY idx_matches_away (away_player_id, start_utc),
  KEY idx_matches_pair (player_lo_id, player_hi_id, start_utc)
);

CREATE TABLE IF NOT EXISTS doubles_matches (
  match_id            INT UNSIGNED NOT NULL,
  start_utc           DATETIME     NULL,
  match_date          DATE         NULL,
  tournament_id       INT UNSIGNED NULL,
  tournament_name     VARCHAR(255) NULL,
  category            VARCHAR(64)  NULL,
  tier                VARCHAR(16)  NOT NULL,
  level_points        SMALLINT     NULL,
  round_name          VARCHAR(64)  NULL,
  surface             VARCHAR(16)  NULL,
  environment         VARCHAR(8)   NULL,
  home_player1_id     INT UNSIGNED NULL,
  home_player2_id     INT UNSIGNED NULL,
  away_player1_id     INT UNSIGNED NULL,
  away_player2_id     INT UNSIGNED NULL,
  home_player1_name   VARCHAR(128) NULL,
  home_player2_name   VARCHAR(128) NULL,
  away_player1_name   VARCHAR(128) NULL,
  away_player2_name   VARCHAR(128) NULL,
  home_team_name      VARCHAR(160) NULL,
  away_team_name      VARCHAR(160) NULL,
  phase               VARCHAR(16)  NOT NULL,
  outcome             VARCHAR(20)  NOT NULL,
  status_description  VARCHAR(64)  NULL,
  winner_side         CHAR(4)      NULL,
  home_sets           TINYINT      NULL,
  away_sets           TINYINT      NULL,
  home_s1 TINYINT NULL, away_s1 TINYINT NULL, home_tb1 TINYINT NULL, away_tb1 TINYINT NULL,
  home_s2 TINYINT NULL, away_s2 TINYINT NULL, home_tb2 TINYINT NULL, away_tb2 TINYINT NULL,
  home_s3 TINYINT NULL, away_s3 TINYINT NULL, home_tb3 TINYINT NULL, away_tb3 TINYINT NULL,
  home_s4 TINYINT NULL, away_s4 TINYINT NULL, home_tb4 TINYINT NULL, away_tb4 TINYINT NULL,
  home_s5 TINYINT NULL, away_s5 TINYINT NULL, home_tb5 TINYINT NULL, away_tb5 TINYINT NULL,
  match_tiebreak_set  TINYINT      NULL,
  has_stats           BOOLEAN      NOT NULL,
  stats_status        VARCHAR(8)   NOT NULL,
  has_pbp             BOOLEAN      NOT NULL,
  pbp_status          VARCHAR(8)   NOT NULL,
  points_count        SMALLINT     NULL,
  updated_utc         DATETIME     NOT NULL,
  PRIMARY KEY (match_id),
  KEY idx_dm_date (match_date, tier),
  KEY idx_dm_phase (phase, match_date),
  KEY idx_dm_hp1 (home_player1_id, start_utc),
  KEY idx_dm_hp2 (home_player2_id, start_utc),
  KEY idx_dm_ap1 (away_player1_id, start_utc),
  KEY idx_dm_ap2 (away_player2_id, start_utc)
);

-- One row per player per singles match.
-- Game / break-point / point counts: from the PBP log when complete, else vendor when stats_status='ok'.
-- Serve-quality stats (aces, double faults, first serve): vendor only, NULL unless stats_status='ok'.
CREATE TABLE IF NOT EXISTS player_match_stats (
  match_id                 INT UNSIGNED NOT NULL,
  player_id                INT UNSIGNED NOT NULL,
  opponent_id              INT UNSIGNED NULL,
  start_utc                DATETIME     NULL,
  side                     CHAR(4)      NOT NULL,
  result                   VARCHAR(4)   NULL,          -- won, lost, or NULL when no result
  outcome                  VARCHAR(20)  NOT NULL,
  sets_won                 TINYINT      NULL,
  sets_lost                TINYINT      NULL,
  games_won                SMALLINT     NULL,
  games_lost               SMALLINT     NULL,
  stats_status             VARCHAR(8)   NOT NULL,
  stats_source             VARCHAR(8)   NULL,          -- pbp, vendor
  aces                     SMALLINT     NULL,
  double_faults            SMALLINT     NULL,
  first_serves_in          SMALLINT     NULL,
  first_serves_total       SMALLINT     NULL,
  first_serve_points_won   SMALLINT     NULL,
  second_serve_points_won  SMALLINT     NULL,
  service_games_played     SMALLINT     NULL,
  service_games_won        SMALLINT     NULL,
  bp_faced                 SMALLINT     NULL,
  bp_saved                 SMALLINT     NULL,
  return_games_played      SMALLINT     NULL,
  return_games_won         SMALLINT     NULL,
  bp_created               SMALLINT     NULL,
  bp_converted             SMALLINT     NULL,
  service_points_played    SMALLINT     NULL,
  service_points_won       SMALLINT     NULL,
  return_points_won        SMALLINT     NULL,
  points_won               SMALLINT     NULL,
  tiebreaks_played         TINYINT      NULL,
  tiebreaks_won            TINYINT      NULL,
  PRIMARY KEY (match_id, player_id),
  KEY idx_pms_player_time (player_id, start_utc)
);

-- One row per point, ATP/WTA matches (singles and doubles) only. Read by match_id range.
CREATE TABLE IF NOT EXISTS pbp_points (
  match_id               INT UNSIGNED NOT NULL,
  point_index            SMALLINT UNSIGNED NOT NULL,
  set_no                 TINYINT      NOT NULL,
  game_no                TINYINT      NOT NULL,
  server                 CHAR(4)      NULL,
  is_tiebreak            BOOLEAN      NOT NULL,
  score_before_home      VARCHAR(3)   NULL,
  score_before_away      VARCHAR(3)   NULL,
  score_after_home       VARCHAR(3)   NULL,
  score_after_away       VARCHAR(3)   NULL,
  point_winner           CHAR(4)      NULL,
  game_winner            CHAR(4)      NULL,
  is_break_point         BOOLEAN      NOT NULL,
  is_game_point          BOOLEAN      NOT NULL,
  is_deciding_point      BOOLEAN      NOT NULL,
  is_game_winning_point  BOOLEAN      NOT NULL,
  PRIMARY KEY (match_id, point_index)
);

-- ---------------------------------------------------------------------------
-- Aggregates (computed in DuckDB from Parquet, loaded whole)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS player_summary (
  player_id               INT UNSIGNED NOT NULL,
  scope                   VARCHAR(8)   NOT NULL,   -- overall, hard, clay, grass, indoor
  elo                     DECIMAL(7,2) NULL,
  elo_matches             INT UNSIGNED NOT NULL DEFAULT 0,
  matches_played          INT UNSIGNED NOT NULL,
  wins                    INT UNSIGNED NOT NULL,
  losses                  INT UNSIGNED NOT NULL,
  wins_by_retirement      INT UNSIGNED NOT NULL,
  losses_by_retirement    INT UNSIGNED NOT NULL,
  walkovers_won           INT UNSIGNED NOT NULL,
  walkovers_lost          INT UNSIGNED NOT NULL,
  sets_won                INT UNSIGNED NULL,
  sets_lost               INT UNSIGNED NULL,
  games_won               INT UNSIGNED NULL,
  games_lost              INT UNSIGNED NULL,
  matches_with_stats      INT UNSIGNED NOT NULL,
  aces                    INT UNSIGNED NULL,
  double_faults           INT UNSIGNED NULL,
  serve_stat_games        INT UNSIGNED NULL,       -- service games in matches with vendor serve stats
  service_games_played    INT UNSIGNED NULL,
  service_games_won       INT UNSIGNED NULL,
  bp_faced                INT UNSIGNED NULL,
  bp_saved                INT UNSIGNED NULL,
  return_games_played     INT UNSIGNED NULL,
  return_games_won        INT UNSIGNED NULL,
  bp_created              INT UNSIGNED NULL,
  bp_converted            INT UNSIGNED NULL,
  hold_pct                DECIMAL(5,4) NULL,
  return_games_won_pct    DECIMAL(5,4) NULL,
  bp_save_pct             DECIMAL(5,4) NULL,
  bp_convert_pct          DECIMAL(5,4) NULL,
  aces_per_service_game   DECIMAL(6,4) NULL,
  last_match_utc          DATETIME     NULL,
  PRIMARY KEY (player_id, scope)
);

CREATE TABLE IF NOT EXISTS player_form (
  player_id        INT UNSIGNED NOT NULL,
  results          VARCHAR(16)  NOT NULL,          -- e.g. "W,W,L,W,W", newest first
  wins             TINYINT      NOT NULL,
  losses           TINYINT      NOT NULL,
  hold_pct         DECIMAL(5,4) NULL,
  doubles_matches  INT UNSIGNED NOT NULL,
  doubles_wins     INT UNSIGNED NOT NULL,
  doubles_losses   INT UNSIGNED NOT NULL,
  PRIMARY KEY (player_id)
);

CREATE TABLE IF NOT EXISTS elo_leaderboard (
  tour            VARCHAR(4)   NOT NULL,           -- atp, wta
  surface         VARCHAR(8)   NOT NULL,           -- overall, hard, clay, grass, indoor
  active_only     BOOLEAN      NOT NULL,
  rank_no         INT UNSIGNED NOT NULL,
  player_id       INT UNSIGNED NOT NULL,
  name            VARCHAR(128) NOT NULL,
  country         CHAR(3)      NULL,
  elo             DECIMAL(7,2) NOT NULL,
  matches         INT UNSIGNED NOT NULL,
  last_match_utc  DATETIME     NULL,
  PRIMARY KEY (tour, surface, active_only, rank_no)
);

CREATE TABLE IF NOT EXISTS load_runs (
  run_id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  started_utc     DATETIME     NOT NULL,
  finished_utc    DATETIME     NULL,
  status          VARCHAR(12)  NOT NULL,           -- running, success, failed
  parser_version  VARCHAR(16)  NULL,
  source_months   INT UNSIGNED NULL,
  data_through    DATETIME     NULL,               -- latest match start in the loaded data
  row_counts      JSON         NULL,
  error           TEXT         NULL,
  PRIMARY KEY (run_id)
);
