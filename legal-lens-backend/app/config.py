import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", BASE_DIR / "storage"))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'legal_lens.db'}")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
TOKEN_EXPIRE_MINUTES = int(os.getenv("TOKEN_EXPIRE_MINUTES", "480"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
LAB_NAME = os.getenv("LAB_NAME", "SIEM Lab - Testing Section")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")]
SEED_DEMO_USERS = os.getenv("SEED_DEMO_USERS", "true").lower() == "true"
MAX_VIDEO_MB = int(os.getenv("MAX_VIDEO_MB", "500"))
MAX_ATTACHMENT_MB = int(os.getenv("MAX_ATTACHMENT_MB", "25"))

VIDEOS_DIR = STORAGE_DIR / "videos"
ATTACHMENTS_DIR = STORAGE_DIR / "attachments"
REPORTS_DIR = STORAGE_DIR / "reports"
for _d in (VIDEOS_DIR, ATTACHMENTS_DIR, REPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
