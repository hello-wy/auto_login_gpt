import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

DB_PATH = Path(os.environ.get("CDK_PLUS_DB_PATH", BASE_DIR / "cdk_plus.sqlite3"))
CLOUDMAIL_CONFIG_PATH = Path(
    os.environ.get("CDK_PLUS_CLOUDMAIL_CONFIG", PROJECT_DIR / "cloudmail.config.json")
)

REFRESH_SECONDS = 30
CDK_LENGTH = 16
CDK_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CDK_PATTERN = r"^[A-Za-z0-9_-]{4,64}$"
CLOUDMAIL_MAX_ATTEMPTS = 1
ADMIN_PASSWORD = os.environ.get("CDK_PLUS_ADMIN_PASSWORD", "akjd345hgi345")
