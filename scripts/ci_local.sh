#!/usr/bin/env bash
# Run the .gitlab-ci.yml jobs locally the way a GitLab runner does: a clean copy of the
# committed tree (git archive HEAD) inside each job's image; the test job gets a fresh
# mysql:8.0 service container reachable as "mysql". Keep the commands in sync with .gitlab-ci.yml.
#
#   bash scripts/ci_local.sh            # lint, test, build
#   bash scripts/ci_local.sh test       # one job
#
# Only committed changes are tested. JUnit report: reports/junit.xml.
set -uo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: don't rewrite /builds paths
cd "$(git rev-parse --show-toplevel)"
JOBS=("${@:-lint test build}")
JOBS=(${JOBS[*]})
PY=python:3.12-slim
NET=re-ci-$$
status=0

cleanup() { docker rm -f "$NET-mysql" "$NET-test" >/dev/null 2>&1; docker network rm "$NET" >/dev/null 2>&1; }
trap cleanup EXIT

run_lint() {
	git archive HEAD | docker run -i --rm "$PY" sh -ec '
		mkdir /builds && cd /builds && tar -x
		pip install -q --root-user-action=ignore ruff==0.16.10
		ruff check .'
}

run_test() {
	docker network create "$NET" >/dev/null
	docker run -d --name "$NET-mysql" --network "$NET" --network-alias mysql \
		-e MYSQL_ROOT_PASSWORD=test -e MYSQL_DATABASE=racketedge_test mysql:8.0 >/dev/null
	git archive HEAD | docker run -i --name "$NET-test" --network "$NET" \
		-e TEST_DB_HOST=mysql -e TEST_DB_PORT=3306 -e TEST_DB_USER=root -e TEST_DB_PASSWORD=test -e TEST_DB_NAME=racketedge_test \
		"$PY" sh -ec '
		mkdir /builds && cd /builds && tar -x
		apt-get update -qq && apt-get install -y -qq --no-install-recommends libgomp1 >/dev/null
		pip install -q --root-user-action=ignore -r requirements-dev.txt
		python -m pytest --junitxml=reports/junit.xml -o junit_family=xunit2'
	local rc=$?
	mkdir -p reports && docker cp "$NET-test:/builds/reports/junit.xml" reports/junit.xml >/dev/null 2>&1
	return $rc
}

run_build() {
	git archive HEAD | docker build --pull -t "racketedge-api:ci-$(git rev-parse --short HEAD)" -
}

for job in "${JOBS[@]}"; do
	echo "===== $job ====="
	if "run_$job"; then echo "===== $job: passed ====="; else echo "===== $job: FAILED ====="; status=1; break; fi
done
exit $status
