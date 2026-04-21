"""Runtime settings.

Values are read from `backend/.env` (copy `backend/.env.example`).
NW-1004 locked `INFERENCE_IMGSZ=640` on the benchmark gate; see README.

Storage paths default to absolute paths anchored at `backend/` so the
app behaves the same whether uvicorn runs from the repo root or the
backend directory. Relative env overrides are resolved against
`backend/` by the validator.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    inference_imgsz: int = 640
    confidence_threshold: float = 0.4
    debounce_frames: int = 2
    max_upload_size_mb: int = 100

    frames_dir: Path = BACKEND_ROOT / "storage" / "frames"
    uploads_dir: Path = BACKEND_ROOT / "storage" / "uploads"
    database_url: str = f"sqlite:///{BACKEND_ROOT / 'storage' / 'neurawatch.db'}"

    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]
    )

    @field_validator("frames_dir", "uploads_dir", mode="after")
    @classmethod
    def _resolve_relative(cls, value: Path) -> Path:
        return value if value.is_absolute() else (BACKEND_ROOT / value).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
