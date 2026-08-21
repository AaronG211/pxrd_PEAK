import os
from pathlib import Path
from dotenv import dotenv_values

_root = Path(__file__).resolve().parent.parent
_env = dotenv_values(_root / ".env")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _read_env(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.getenv(key)
        if value is None:
            value = _env.get(key)
        if value is None:
            continue
        value = value.strip()
        if value:
            return value
    return default


GEMINI_API_KEY: str = _read_env("GEMINI_API_KEY")
GEMINI_MODEL: str = _read_env("GEMINI_MODEL", default="gemini-2.5-flash")
OPENAI_API_KEY: str = _read_env("OPENAI_API_KEY", "CHATGPT_API_KEY")
OPENAI_MODEL: str = _read_env("OPENAI_MODEL", "CHATGPT_MODEL", default="gpt-4o")

LLM_PROVIDER: str = _read_env("LLM_PROVIDER").lower()
if LLM_PROVIDER not in {"gemini", "openai"}:
    LLM_PROVIDER = "gemini" if GEMINI_API_KEY else "openai"

LLM_API_KEY: str = GEMINI_API_KEY if LLM_PROVIDER == "gemini" else OPENAI_API_KEY
LLM_MODEL: str = GEMINI_MODEL if LLM_PROVIDER == "gemini" else OPENAI_MODEL
LLM_BASE_URL: str | None = GEMINI_BASE_URL if LLM_PROVIDER == "gemini" else None

DATABASE_URL: str = _read_env(
    "DATABASE_URL",
    default=f"sqlite+aiosqlite:///{_root / 'cofs.db'}",
)
UPLOAD_DIR: Path = Path(_read_env("UPLOAD_DIR", default=str(_root / "uploads")))
SAMPLE_DIR: Path = Path(_read_env("SAMPLE_DIR", default=str(_root / "sample_papers")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
