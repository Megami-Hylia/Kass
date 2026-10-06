import os
from pathlib import Path


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://cass:cass@localhost:5432/cass")
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", "./data/media")).resolve()
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
MAX_IMPORT_SECONDS = int(os.getenv("MAX_IMPORT_SECONDS", "1800"))
IMPORT_TIMEOUT_SECONDS = int(os.getenv("IMPORT_TIMEOUT_SECONDS", "600"))
SESSION_DAYS = 14
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
ALLOWED_ORIGINS = {s.strip().rstrip("/") for s in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000,http://localhost:8080,http://127.0.0.1:8080").split(",") if s.strip()}
FFMPEG = os.getenv("FFMPEG_BINARY", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_BINARY", "ffprobe")
PERSONAL_MODE = os.getenv("PERSONAL_MODE", "false").lower() == "true"
LOCAL_DESKTOP_MODE = os.getenv("CASS_LOCAL_DESKTOP", "false").lower() == "true"
INSTANCE_ID = os.getenv("CASS_INSTANCE_ID")
FRONTEND_DIST = Path(os.environ["FRONTEND_DIST"]).resolve() if os.getenv("FRONTEND_DIST") else None
