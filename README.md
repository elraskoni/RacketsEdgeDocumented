# RacketEdge

A tennis data and prediction API, built end to end by one engineer: data collection, validation, storage, a REST API, rating and machine-learning models, and an automated test and delivery pipeline.

This repository documents how it is built and what was learned. The source code is private; the documents describe the design, the engineering practices and the results, including the ones that did not work out.

## At a glance

| | |
|---|---|
| Coverage | Every professional tier: ATP, WTA, Challenger / WTA 125, ITF, UTR, team events |
| Matches | 712,000 (568,000 singles, 143,000 doubles), January 2020 → October 2026 |
| Point-by-point | 8.6 million points with server, score and break / game / deciding-point flags |
| Players | 29,800 |
| Bookmaker odds | Opening and closing pre-match prices requested for 499,000 singles matches; 266,000 usable |
| API | 14 REST routes: live and daily matches, player profiles, head-to-head, point-by-point, Elo leaderboard, win probability |
| Tests | 57 unit tests and 19 BDD acceptance scenarios; GitLab CI: lint → test → build |

## Documents

| Document | What it covers |
|---|---|
| [Architecture](docs/architecture.md) | System design from collection to customer, key design decisions, serving layer, data model, and how it maps to AWS |
| [Data sourcing](docs/data_sourcing.md) | How data is collected, archived, parsed and validated; quality flags; diagnosing a collection outage |
| [Machine learning](docs/machine_learning.md) | Elo ratings, leakage-free features, why the model is hard to improve, the comparison with the betting market, and a pre-registered forward test |
| [Testing and CI](docs/testing_and_ci.md) | Specification-first development (ATDD / BDD), the throwaway test database, the GitLab pipeline, and a bug it caught |

## Highlights

- **Specification first.** Acceptance criteria are written in Gherkin before the code. On the prediction endpoint the spec failed on 5 of 19 scenarios, which exposed real gaps in the API; the next commit closed them.
- **Leakage removed and tested.** An earlier model reported 79% accuracy because its features used information from after each match. The rebuilt pipeline computes every feature as it stood before the match, and a test proves a match's own result can never change its features.
- **Ratings tuned honestly.** Elo parameters were chosen on one season and confirmed on a later one never used for tuning: main-tour accuracy 61.0% → 66.1%.
- **Measured against the market.** Model probabilities were compared with bookmaker prices on every tier. The market wins head to head; the model carries extra information only in the lower tiers.
- **A claim tested before it was believed.** A profitable-looking betting edge in ITF was written down with its pass/fail rule before new data existed, then tested on five unseen months. It failed, and the documents say so.

## Stack

Python · FastAPI · MySQL 8 · DuckDB · Parquet · LightGBM / scikit-learn · pytest / pytest-bdd · Docker · GitLab CI · systemd (moving to AWS)

## Note on data

RacketEdge is a prototype built on publicly available sports data. It is designed so that a licensed data feed plugs in as a new source adapter, without changes downstream.

## Licence

All rights reserved; see [LICENSE](LICENSE). The repository is public for portfolio and evaluation purposes.

---

Joseph Raskino · raskinojoseph@gmail.com
