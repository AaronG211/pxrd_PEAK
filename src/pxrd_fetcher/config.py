"""Runtime configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    chatgpt_api_key: SecretStr = Field(
        alias="CHATGPT_API_KEY",
        validation_alias=AliasChoices("CHATGPT_API_KEY", "OPENAI_API_KEY"),
    )
    chatgpt_model: str = Field(
        alias="CHATGPT_MODEL",
        validation_alias=AliasChoices("CHATGPT_MODEL", "OPENAI_MODEL"),
    )

    output_dir: Path = Field(default=Path("outputs"))
    data_dir: Path = Field(default=Path("data"))
    database_path: Path = Field(default=Path("data/pxrd_fetcher.db"))

    render_dpi: int = 450
    ocr_backend: str = "rapidocr"
    similarity_threshold: float = 0.72
    interpolation_step_deg: float = 0.02
    smoothing_window: int = 11
    smoothing_polyorder: int = 3
    min_curve_coverage: float = 0.3
    min_resolution_px: int = 1200
    openai_timeout_seconds: float = 60.0
    ai_non_curve_assist_enabled: bool = True
    ai_non_curve_assist_overlay_threshold: float = 0.82
    ai_non_curve_assist_mask_fraction_threshold: float = 0.22
    max_curves_per_figure: int = 6

    def prepare_directories(self, root: Optional[Path] = None) -> None:
        """Ensure writable directories exist."""

        base = root or Path.cwd()
        self.resolve_output_dir(base).mkdir(parents=True, exist_ok=True)
        self.resolve_data_dir(base).mkdir(parents=True, exist_ok=True)
        self.resolve_database_path(base).parent.mkdir(parents=True, exist_ok=True)

    def resolve_output_dir(self, root: Optional[Path] = None) -> Path:
        base = root or Path.cwd()
        return (base / self.output_dir).resolve()

    def resolve_data_dir(self, root: Optional[Path] = None) -> Path:
        base = root or Path.cwd()
        return (base / self.data_dir).resolve()

    def resolve_database_path(self, root: Optional[Path] = None) -> Path:
        base = root or Path.cwd()
        return (base / self.database_path).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings for CLI entrypoints."""

    return Settings()
