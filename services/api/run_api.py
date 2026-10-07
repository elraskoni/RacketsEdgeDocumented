import os


def _manual_load_env_from_file() -> None:
	env_file = os.getenv("ENV_FILE")
	if env_file and os.path.exists(env_file):
		_parse_env_file(env_file)
		return
	app_env = os.getenv("APP_ENV", "dev")
	repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
	candidate = os.path.join(repo_root, "env", f"{app_env}.env")
	if os.path.exists(candidate):
		_parse_env_file(candidate)


def _parse_env_file(path: str) -> None:
	try:
		with open(path, "r", encoding="utf-8") as f:
			for line in f:
				line = line.strip()
				if not line or line.startswith("#") or "=" not in line:
					continue
				k, v = line.split("=", 1)
				k = k.strip()
				v = v.strip().strip('"').strip("'")
				if k and k not in os.environ:
					os.environ[k] = v
	except Exception:
		pass


def main() -> None:
	_manual_load_env_from_file()
	# Ensure project root on sys.path for package imports
	import sys as _sys
	_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
	if _repo_root not in _sys.path:
		_sys.path.insert(0, _repo_root)
	from services.api.app_tennis import app  # lazy import after env set and path fix
	import uvicorn
	host = os.getenv("API_HOST", "0.0.0.0")
	port = int(os.getenv("TENNIS_API_PORT", "8001"))
	uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
	main()

