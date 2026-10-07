"""Step definitions for features/predict.feature (POST /v1/tennis/predict)."""
import json

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from .helpers import create_api_key, seed_players

pytestmark = pytest.mark.acceptance

scenarios("predict.feature")

PREDICT = "/v1/tennis/predict"


# ---- Given -------------------------------------------------------------------

@given("the following players exist:")
def players_exist(db, datatable):
	header, *rows = datatable
	seed_players(db, [dict(zip(header, row)) for row in rows])


@given("I have a valid API key")
def valid_api_key(db, ctx):
	ctx["api_key"] = create_api_key(db)


@given(parsers.parse('I use the API key "{key}"'))
def use_api_key(ctx, key):
	ctx["api_key"] = key


# ---- When --------------------------------------------------------------------

def _post(client, ctx, body):
	r = client.post(PREDICT, json=body, headers={"X-API-Key": ctx["api_key"]})
	ctx["responses"].append(r)
	ctx["response"] = r
	return r


@when(parsers.parse("I request a prediction for player {a:d} against player {b:d} on {surface}"))
def request_prediction(client, ctx, a, b, surface):
	body = {"player_a_id": a, "player_b_id": b}
	if surface != "any surface":
		body["surface"] = surface
	_post(client, ctx, body)


@when("I send a prediction request with the body:")
def send_raw_body(client, ctx, docstring):
	_post(client, ctx, json.loads(docstring))


# ---- Then --------------------------------------------------------------------

def _probabilities(response):
	body = response.json()
	return body["player_a"]["win_probability"], body["player_b"]["win_probability"]


@then(parsers.parse("the response status is {status:d}"))
def response_status(ctx, status):
	r = ctx["response"]
	assert r.status_code == status, f"expected {status}, got {r.status_code}: {r.text[:300]}"


@then("each win probability is between 0 and 1")
def probabilities_in_range(ctx):
	for p in _probabilities(ctx["response"]):
		assert 0.0 <= p <= 1.0, p


@then("the win probabilities sum to 1")
def probabilities_sum_to_one(ctx):
	p_a, p_b = _probabilities(ctx["response"])
	# Each probability is rounded to 4 decimals, so allow one unit in the last place
	assert abs(p_a + p_b - 1.0) <= 1e-4 + 1e-12, (p_a, p_b)


@then("both answers give each player the same win probability")
def order_independent(ctx):
	first, second = (r.json() for r in ctx["responses"][-2:])
	assert first["player_a"]["id"] == second["player_b"]["id"]
	assert first["player_a"]["win_probability"] == second["player_b"]["win_probability"]
	assert first["player_b"]["win_probability"] == second["player_a"]["win_probability"]


@then(parsers.parse('"{name}" is the favourite'))
def favourite_is(ctx, name):
	body = ctx["response"].json()
	fav = max((body["player_a"], body["player_b"]), key=lambda p: p["win_probability"])
	assert fav["win_probability"] > 0.5, body
	assert fav["name"] == name, body


@then("neither player is the favourite")
def no_favourite(ctx):
	p_a, p_b = _probabilities(ctx["response"])
	assert p_a == p_b == 0.5, (p_a, p_b)


@then(parsers.parse('the response reports the surface as "{surface}"'))
def reported_surface(ctx, surface):
	assert ctx["response"].json()["surface"] == surface


@then(parsers.re(r'the error is "(?P<code>[^"]+)"'))
def error_code(ctx, code):
	assert ctx["response"].json()["error"] == code, ctx["response"].text


@then(parsers.re(r'the error is "(?P<code>[^"]+)" on field "(?P<field>[^"]+)"'))
def error_code_on_field(ctx, code, field):
	body = ctx["response"].json()
	assert body["error"] == code, body
	fields = [e["field"] for e in body.get("detail") or []]
	assert any(f == f"body.{field}" or f.startswith(f"body.{field}.") for f in fields), fields
