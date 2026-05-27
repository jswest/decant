from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

CONFIG_DIR = Path.home() / ".decant"
CONFIG_PATH = CONFIG_DIR / "config.yaml"
CACHE_DIR = CONFIG_DIR / "cache"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConfigMissingError(Exception):
    """Raised when ~/.decant/config.yaml does not exist."""


class OllamaConfig(BaseModel):
    host: str = "http://localhost:11434"
    model: str = "qwen3:32b"
    request_timeout_s: int = 300


class CacheConfig(BaseModel):
    ttl_hours: int = 24


class FetchConfig(BaseModel):
    request_timeout_s: int = 60
    navigation_wait: str = "networkidle"
    block_resources: list[str] = Field(
        default_factory=lambda: ["image", "font", "media"]
    )


class Config(BaseModel):
    contact_email: str
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    fetch: FetchConfig = Field(default_factory=FetchConfig)

    @field_validator("contact_email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        if not EMAIL_RE.match(v):
            raise ValueError(f"not a plausible email: {v!r}")
        return v


def load_config(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        raise ConfigMissingError(str(path))
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    return Config.model_validate(data)


def save_config(cfg: Config, path: Path | None = None) -> None:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(cfg.model_dump(), f, sort_keys=False)
