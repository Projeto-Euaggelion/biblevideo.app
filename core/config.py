import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
JOBS_DIR = BASE_DIR / "jobs"
VIDEO_TEMPLATES_DIR = BASE_DIR / "templates" / "video"

for d in (UPLOADS_DIR, OUTPUT_DIR, JOBS_DIR):
    d.mkdir(parents=True, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Resolução de exportação por formato
VIDEO_DIMENSIONS = {
    "landscape": (1920, 1080),
    "vertical": (1080, 1920),
}

FRAME_FPS_FALLBACK = 1  # usado apenas para durações mínimas de segurança
