"""Fixtures for the BDD acceptance tests (tests/acceptance/features/*.feature).

The API runs in-process (FastAPI TestClient) against a throwaway MySQL test database:
local `docker compose -f docker/test-db.compose.yml up -d --wait`, or the mysql service in CI.
Connection: TEST_DB_HOST / TEST_DB_PORT / TEST_DB_USER / TEST_DB_PASSWORD / TEST_DB_NAME
(defaults match the compose file). The database is dropped and rebuilt from
db/racketedge_schema.sql at the start of every run.

The real prediction model is not in the repository, so a small stand-in model with the same
features is trained at start-up. The tests check the API's behaviour (validation, probability
rules, surface handling), not the quality of the model.
"""
import os
import re
import tempfile
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

TEST_DB = {
	"DB_HOST": os.getenv("TEST_DB_HOST", "127.0.0.1"),
	"DB_PORT": os.getenv("TEST_DB_PORT", "3309"),
	"DB_USER": os.getenv("TEST_DB_USER", "root"),
	"DB_PASSWORD": os.getenv("TEST_DB_PASSWORD", "test"),
	"DB_NAME": os.getenv("TEST_DB_NAME", "racketedge_test"),
}
# Safety net: these tests drop and recreate the database they are pointed at.
if not TEST_DB["DB_NAME"].endswith("_test"):
	raise pytest.UsageError(f"TEST_DB_NAME must end in '_test' (got {TEST_DB['DB_NAME']!r}); refusing to touch it")

# Point the app at the test database before it is imported (config and the API read env at import).
os.environ["APP_ENV"] = "test"
os.environ.update(TEST_DB)
import config  # noqa: E402

for _k, _v in TEST_DB.items():
	setattr(config, _k, int(_v) if _k == "DB_PORT" else _v)
os.environ.pop("RAPIDAPI_PROXY_SECRET", None)  # an env file could have set it; it would 403 every request

import pymysql  # noqa: E402

PLAYER_TABLES = ("players", "player_summary", "player_form", "player_match_stats")


def _connect(database=None):
	return pymysql.connect(
		host=TEST_DB["DB_HOST"], port=int(TEST_DB["DB_PORT"]), user=TEST_DB["DB_USER"], password=TEST_DB["DB_PASSWORD"],
		database=database, autocommit=True, charset="utf8mb4",
	)


def _sql_statements(path):
	with open(path, encoding="utf-8") as f:
		text = re.sub(r"--[^\n]*", "", f.read())
	return [s.strip() for s in text.split(";") if s.strip()]


@pytest.fixture(scope="session")
def test_database():
	deadline = time.time() + 90
	while True:
		try:
			conn = _connect()
			break
		except pymysql.err.OperationalError as exc:
			if time.time() > deadline:
				pytest.fail(
					f"Test MySQL not reachable at {TEST_DB['DB_HOST']}:{TEST_DB['DB_PORT']} ({exc}). "
					"Start it with: docker compose -f docker/test-db.compose.yml up -d --wait"
				)
			time.sleep(2)
	name = TEST_DB["DB_NAME"]
	with conn.cursor() as cur:
		cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
		cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
		cur.execute(f"USE `{name}`")
		for path in (os.path.join(ROOT, "db", "racketedge_schema.sql"), os.path.join(os.path.dirname(__file__), "sql", "auth_tables.sql")):
			for stmt in _sql_statements(path):
				cur.execute(stmt)
		cur.execute("INSERT INTO plans (plan_id, name, rpm_limit, daily_quota) VALUES ('plan-test', 'Test', 0, 0)")
	conn.close()
	yield name


@pytest.fixture
def db(test_database):
	"""Connection to the test database; player tables are emptied before each scenario."""
	conn = _connect(test_database)
	with conn.cursor() as cur:
		for t in PLAYER_TABLES:
			cur.execute(f"DELETE FROM `{t}`")
	yield conn
	conn.close()


@pytest.fixture(scope="session")
def stand_in_model():
	"""Logistic regression on the production feature names; it learns that the Elo difference decides."""
	import joblib
	import numpy as np
	import pandas as pd
	from sklearn.linear_model import LogisticRegression

	from services.api import predict

	names = (
		[f"delta_career_{k}" for k in predict._CAREER_KEYS]
		+ [f"delta_recent5_{k}" for k in predict._RECENT_KEYS]
		+ ["delta_elo_overall", "side_home", "side_away", "side_unknown", "surface_unknown"]
	)
	rng = np.random.default_rng(7)
	X = pd.DataFrame(rng.normal(size=(2000, len(names))), columns=names)
	X["delta_elo_overall"] *= 300
	y = (X["delta_elo_overall"] + rng.normal(scale=100, size=len(X)) > 0).astype(int)
	model = LogisticRegression(max_iter=1000).fit(X, y)

	path = os.path.join(tempfile.mkdtemp(prefix="re_model_"), "stand_in.joblib")
	joblib.dump({"model": model, "metadata": {"feature_names": names}}, path)
	predict._ARTIFACT_PATH = path
	predict._MODEL_CACHE.clear()
	return path


@pytest.fixture(scope="session")
def client(test_database, stand_in_model):
	from fastapi.testclient import TestClient

	from services.api.app_tennis import app

	with TestClient(app) as c:
		yield c


@pytest.fixture
def ctx():
	"""Per-scenario state shared between steps."""
	return {"responses": []}

