import os
import threading
from typing import Tuple

_local = threading.local()

try:
	# Prefer centralized config (with integrated defaults)
	import config  # type: ignore
except Exception:
	config = None  # fallback to env only


def _try_import_mysql_driver():
	preferred = os.getenv("DB_DRIVER", "").lower()
	if preferred == "mysql-connector":
		try:
			import mysql.connector  # type: ignore
			return "mysql-connector", mysql.connector
		except Exception:
			pass
	# Default: prefer pymysql (handles caching_sha2_password without SSL requirement)
	try:
		import pymysql  # type: ignore
		return "pymysql", pymysql
	except Exception:
		pass
	try:
		import mysql.connector  # type: ignore
		return "mysql-connector", mysql.connector
	except Exception:
		pass
	return None, None


def get_db_params() -> Tuple[str, int, str, str, str]:
	"""
	Return connection parameters for the app-scoped DB user if available;
	otherwise fall back to generic DB_USER/DB_PASSWORD.
	"""
	if config is not None:
		host = getattr(config, "DB_HOST", os.getenv("DB_HOST", "localhost"))
		port = int(getattr(config, "DB_PORT", int(os.getenv("DB_PORT", "3306"))))
		db = getattr(config, "DB_NAME", os.getenv("DB_NAME", "Racket-Edge"))
		# Prefer explicit DB_USER/DB_PASSWORD if provided; otherwise fall back to app user
		explicit_user = getattr(config, "DB_USER", os.getenv("DB_USER", "")) or ""
		explicit_pwd = getattr(config, "DB_PASSWORD", os.getenv("DB_PASSWORD", "")) or ""
		app_user = getattr(config, "DB_APP_USER", os.getenv("DB_APP_USER", "")) or ""
		app_pwd = getattr(config, "DB_APP_PASSWORD", os.getenv("DB_APP_PASSWORD", "")) or ""
		if explicit_user and explicit_pwd:
			user, pwd = explicit_user, explicit_pwd
		elif app_user and app_pwd:
			user, pwd = app_user, app_pwd
		else:
			# last resort defaults (not recommended for prod)
			user = os.getenv("DB_USER", "root")
			pwd = os.getenv("DB_PASSWORD", "")
	else:
		host = os.getenv("DB_HOST", "localhost")
		port = int(os.getenv("DB_PORT", "3306"))
		db = os.getenv("DB_NAME", "Racket-Edge")
		# Prefer explicit DB_USER/DB_PASSWORD; otherwise fall back to app user
		explicit_user = os.getenv("DB_USER", "")
		explicit_pwd = os.getenv("DB_PASSWORD", "")
		app_user = os.getenv("DB_APP_USER", "")
		app_pwd = os.getenv("DB_APP_PASSWORD", "")
		if explicit_user and explicit_pwd:
			user, pwd = explicit_user, explicit_pwd
		elif app_user and app_pwd:
			user, pwd = app_user, app_pwd
		else:
			user = os.getenv("DB_USER", "root")
			pwd = os.getenv("DB_PASSWORD", "")
	return host, port, user, pwd, db


def dict_cursor(conn):
	"""
	Return a cursor that yields dict rows regardless of DB driver.
	Use as: with dict_cursor(conn) as cur: ...
	Replaces conn.cursor(dictionary=True) which is mysql-connector-only.
	"""
	try:
		import pymysql.cursors  # type: ignore
		return conn.cursor(pymysql.cursors.DictCursor)
	except TypeError:
		pass  # Not a PyMySQL connection — fall through to mysql-connector
	return conn.cursor(dictionary=True)


def fetchall_dicts(cur) -> list:
	"""Return cursor rows as list of dicts regardless of DB driver."""
	rows = cur.fetchall() or []
	if not rows or isinstance(rows[0], dict):
		return list(rows)
	cols = [d[0] for d in cur.description]
	return [dict(zip(cols, r)) for r in rows]


def fetchone_dict(cur):
	"""Return single cursor row as dict regardless of DB driver."""
	row = cur.fetchone()
	if row is None or isinstance(row, dict):
		return row
	cols = [d[0] for d in cur.description]
	return dict(zip(cols, row))


def get_connection(read_timeout: int = 30, write_timeout: int = 30) -> object:
	"""
	Return a persistent per-thread connection, reconnecting if stale.
	Use in API handlers instead of connect() to avoid the TCP handshake
	on every request. Do NOT call close() on the returned connection.
	"""
	conn = getattr(_local, "conn", None)
	if conn is not None:
		try:
			conn.ping(reconnect=True)
			return conn
		except Exception:
			pass
	conn = connect(read_timeout=read_timeout, write_timeout=write_timeout)
	_local.conn = conn
	return conn


def connect(read_timeout: int = 30, write_timeout: int = 30) -> object:
	"""
	Connect to MySQL. read_timeout/write_timeout default to 30s (safe for API
	requests). Pass higher values (e.g. 7200) for long-running batch scripts.
	"""
	driver_name, driver_module = _try_import_mysql_driver()
	if not driver_module:
		raise RuntimeError("No MySQL driver found. Install mysql-connector-python or PyMySQL.")
	host, port, user, pwd, db = get_db_params()
	# Disable SSL for local/dev connections or when explicitly requested via env.
	# mysql-connector-python's default SSL setup calls ssl.create_default_context()
	# which enumerates the Windows certificate store on Python 3.13 and can hang.
	_local_hosts = {"localhost", "127.0.0.1", "::1"}
	_ssl_disabled = (
		os.getenv("DB_SSL_DISABLED", "").lower() in ("1", "true", "yes")
		or host in _local_hosts
	)

	if driver_name == "mysql-connector":
		kwargs: dict = dict(
			host=host, port=port, user=user, password=pwd,
			database=db, autocommit=True,
		)
		if _ssl_disabled:
			kwargs["ssl_disabled"] = True
		return driver_module.connect(**kwargs)
	else:
		return driver_module.connect(
			host=host, port=port, user=user, password=pwd,
			database=db, autocommit=True, charset="utf8mb4",
			connect_timeout=10, read_timeout=read_timeout, write_timeout=write_timeout,
		)



