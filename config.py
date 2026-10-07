import os


def _load_env_from_file() -> None:
    """
    Load environment variables from an env file without any third-party dependency.
    Priority:
      1. ENV_FILE env var (explicit full path)
      2. env/{APP_ENV}.env  (e.g. env/prod.env when APP_ENV=prod)
    Already-set env vars are never overwritten (override=False semantics).
    In production the systemd EnvironmentFile= already populates the process
    environment, so this function is a no-op there.
    """
    env_file = os.getenv("ENV_FILE")
    if not env_file:
        app_env = os.getenv("APP_ENV", "dev")
        env_file = os.path.join("env", f"{app_env}.env")
    if not os.path.exists(env_file):
        return
    try:
        with open(env_file, "r", encoding="utf-8") as f:
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


_load_env_from_file()

# Database configuration (prefer env; no hardcoded secrets)
DB_HOST: str = os.getenv("DB_HOST", "localhost")
DB_PORT: int = int(os.getenv("DB_PORT", "3306"))
DB_NAME: str = os.getenv("DB_NAME", "Racket-Edge")

# App-scoped user for API (least privilege)
DB_APP_USER: str = os.getenv("DB_APP_USER", "")
DB_APP_PASSWORD: str = os.getenv("DB_APP_PASSWORD", "")

# Optional admin creds (for setup/migrations/loader scripts only)
DB_USER: str = os.getenv("DB_USER", "")
DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")

# API and poller settings
POLL_INTERVAL_S: int = int(os.getenv("POLL_INTERVAL_S", "30"))
API_PREFIX_TENNIS: str = "/v1/tennis"


# Auth settings
JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-insecure-secret")  # replace in prod
JWT_EXP_SECONDS: int = int(os.getenv("JWT_EXP_SECONDS", "86400"))

# Stripe billing
STRIPE_SECRET: str = os.getenv("STRIPE_SECRET", "")
STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PUBLISHABLE_KEY: str = os.getenv("STRIPE_PUBLISH", "")

# Base URLs (used in redirect links)
FRONTEND_BASE_URL: str = os.getenv("FRONTEND_ORIGINS", "http://localhost:8080").split(",")[0]

# Google OAuth (accepts either GOOGLE_ or OAUTH_ prefix)
GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID") or os.getenv("OAUTH_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET") or os.getenv("OAUTH_CLIENT_SECRET", "")

