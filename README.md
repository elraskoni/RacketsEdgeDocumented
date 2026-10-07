# RacketEdge

A tennis data and prediction API, built end to end by one engineer: data collection, validation, storage, a REST API, rating and machine-learning models, and an automated test and delivery pipeline.

This repository contains the codebase and its documentation. Two parts are private: the parser for the production data source, and the trained models with their tuned parameters. In their place, a **synthetic source** simulates tennis point by point, so everything else runs end to end on your machine, using the same code as production: the raw-first archive, the clean Parquet build, the serving database with its atomic swap, the REST API, and the tests.

## At a glance

| | |
|---|---|
| Coverage | Every professional tier: ATP, WTA, Challenger / WTA 125, ITF, UTR, team events |
| Matches | 712,000 (568,000 singles, 143,000 doubles), January 2020 → October 2026 |
| Point-by-point | 8.6 million points with server, score and break / game / deciding-point flags |
| Players | 29,800 |
| Bookmaker odds | Opening and closing pre-match prices requested for 499,000 singles matches; 266,000 usable |
| API | 14 REST routes: live and daily matches, player profiles, head-to-head, point-by-point, Elo leaderboard, win probability |
| Tests | 19 BDD acceptance scenarios plus unit tests; GitLab CI (and a GitHub Actions mirror): lint → test → build |

## Documents

| Document | What it covers |
|---|---|
| [Architecture](docs/architecture.md) | System design from collection to customer, key design decisions, serving layer, data model, and how it maps to AWS |
| [Data sourcing](docs/data_sourcing.md) | How data is collected, archived, parsed and validated; quality flags; diagnosing a collection outage |
| [Machine learning](docs/machine_learning.md) | Elo ratings, leakage-free features, why the model is hard to improve, the comparison with the betting market, and a pre-registered forward test |
| [Testing and CI](docs/testing_and_ci.md) | Specification-first development (ATDD / BDD), the throwaway test database, the GitLab pipeline, and a bug it caught |

## Run it yourself

Requires Python 3.12+ and Docker.

```bash
pip install -r requirements-dev.txt

# Demo database, a synthetic season, and the full pipeline (landing -> archive -> Parquet -> MySQL)
docker compose -f docker/demo.compose.yml up -d --wait
export APP_ENV=demo                     # PowerShell: $env:APP_ENV="demo"
python scripts/make_synthetic_data.py --root ./library --months 6
python scripts/refresh_racketedge.py --root ./library
python scripts/train_demo_model.py

# API on http://127.0.0.1:8001 (docs at /docs)
KEY=$(python scripts/create_api_key.py)
python -m uvicorn services.api.app_tennis:app --port 8001
curl -H "X-API-Key: $KEY" "http://127.0.0.1:8001/v1/tennis/elo/leaderboard?tour=atp&page_size=5"
```

Tests (unit tests + BDD acceptance tests against a throwaway MySQL):

```bash
docker compose -f docker/test-db.compose.yml up -d --wait
python -m pytest
python -m ruff check .
bash scripts/ci_local.sh                # the whole GitLab pipeline, locally, on a clean checkout
```

## Code map

| Path | What it is |
|---|---|
| `services/api/` | FastAPI public API: routes, data access (one indexed query per endpoint), schemas, API-key auth with rate limits, endpoint flags, error format, prediction |
| `libs/racketedge/` | Raw-first archive and manifests, landing and hourly fold, clean-layer schema, serving SQL shared by the batch rebuild and the live upsert |
| `libs/parsers/synthetic/` | Source adapter for the synthetic feed: the same interface as the private production parser, with validation |
| `libs/synthetic/` | Point-by-point tennis match simulator (deuce, tiebreak serve rotation, best of 3/5, first and second serves, retirements) |
| `libs/ml/elo.py` | Elo engine: overall and surface ratings, experience-based K, tier weights, surface blending (tuned values private) |
| `scripts/` | Clean build, loader with atomic table swap, hourly refresh, synthetic data, demo key and model, local CI |
| `tests/` | BDD acceptance tests (`tests/acceptance/features/*.feature`), pipeline, simulator/adapter and Elo tests |
| `db/` | Serving schema and API-key tables |
| `.gitlab-ci.yml`, `.github/workflows/ci.yml`, `Dockerfile` | CI pipelines and the API container |

## Highlights

- **Specification first.** Acceptance criteria are written in Gherkin before the code. On the prediction endpoint the spec failed on 5 of 19 scenarios, which exposed real gaps in the API; the next commit closed them.
- **Leakage removed and tested.** An earlier model reported 79% accuracy because its features used information from after each match. The rebuilt pipeline computes every feature as it stood before the match, and a test proves a match's own result can never change its features.
- **Ratings tuned honestly.** Elo parameters were chosen on one season and confirmed on a later one never used for tuning: main-tour accuracy 61.0% → 66.1%.
- **Measured against the market.** Model probabilities were compared with bookmaker prices on every tier. The market wins head to head; the model carries extra information only in the lower tiers.
- **A claim tested before it was believed.** A profitable-looking betting edge in ITF was written down with its pass/fail rule before new data existed, then tested on five unseen months. It failed, and the documents say so.

## Stack

Python · FastAPI · MySQL 8 · DuckDB · Parquet · LightGBM / scikit-learn · pytest / pytest-bdd · Docker · GitLab CI · systemd (moving to AWS)

## Note on data

RacketEdge is a prototype built on publicly available sports data. It is designed so that a licensed data feed plugs in as a new source adapter (see `libs/parsers/synthetic` for the interface), without changes downstream. All data produced by this repository is synthetic.

## Licence

All rights reserved; see [LICENSE](LICENSE). The repository is public for portfolio and evaluation purposes.

---

Joseph Raskino · raskinojoseph@gmail.com
