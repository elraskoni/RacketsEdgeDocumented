"""Connections for RacketEdge 3.0 scripts that read one environment and write another.

services/api/db.py binds to a single APP_ENV per process. The 3.0 build scripts need two
at once in some setups, so they connect from env/<name>.env files directly.
"""
import os
from typing import Dict

import pymysql

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read_env(name: str) -> Dict[str, str]:
	path = os.path.join(_REPO_ROOT, "env", f"{name}.env")
	out: Dict[str, str] = {}
	with open(path, encoding="utf-8") as f:
		for line in f:
			line = line.strip()
			if line and not line.startswith("#") and "=" in line:
				k, v = line.split("=", 1)
				out[k.strip()] = v.strip().strip('"').strip("'")
	return out


def connect(env_name: str, admin: bool = True, **kwargs) -> pymysql.connections.Connection:
	"""Connect using env/<env_name>.env. admin=True uses DB_USER (scripts), else DB_APP_USER."""
	cfg = _read_env(env_name)
	user = cfg.get("DB_USER") if admin else cfg.get("DB_APP_USER")
	password = cfg.get("DB_PASSWORD") if admin else cfg.get("DB_APP_PASSWORD")
	return pymysql.connect(
		host=cfg.get("DB_HOST", "127.0.0.1"),
		port=int(cfg.get("DB_PORT", "3306")),
		user=user,
		password=password,
		database=cfg.get("DB_NAME"),
		charset="utf8mb4",
		autocommit=True,
		read_timeout=7200,
		write_timeout=7200,
		**kwargs,
	)
