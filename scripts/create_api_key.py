"""
Create an API key for the local demo (prints it once; only its SHA-256 hash is stored).

	APP_ENV=demo python scripts/create_api_key.py
"""
import hashlib
import os
import secrets
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))
from racketedge_db import connect  # noqa: E402


def main() -> int:
	raw = "re_" + secrets.token_urlsafe(24)
	conn = connect(os.getenv("APP_ENV") or "demo")
	with conn.cursor() as cur:
		cur.execute("INSERT IGNORE INTO plans (plan_id, name, rpm_limit, daily_quota) VALUES ('plan-demo', 'Demo', 60, 10000)")
		cur.execute("INSERT INTO organizations (name, plan_id) VALUES (%s, 'plan-demo')", ("demo-" + raw[-6:],))
		cur.execute("INSERT INTO api_keys (org_id, product_id, key_prefix, key_hash, name) VALUES (%s, 'tennis', %s, %s, 'demo')",
			(cur.lastrowid, raw[:8], hashlib.sha256(raw.encode("utf-8")).digest()))
	conn.close()
	print(raw)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
