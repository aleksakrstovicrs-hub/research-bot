"""Podešavanja iz .env i zajedničke putanje."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # testovi rade i bez python-dotenv
    pass

DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "klijenti.db")))

# Nominatim i Overpass traže da se bot predstavi i ostavi kontakt.
USER_AGENT = os.getenv(
    "BOT_USER_AGENT",
    "ResearchBot/0.1 (+https://example.com; contact@example.com)",
)


def get(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Nedostaje {name} u .env")
    return value
