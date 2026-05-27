from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    INVALID_URL = "invalid_url"
    ROBOTS_DISALLOWED = "robots_disallowed"
    FETCH_FAILED = "fetch_failed"
    EXTRACT_EMPTY = "extract_empty"
    OLLAMA_UNAVAILABLE = "ollama_unavailable"
    OLLAMA_TIMEOUT = "ollama_timeout"
    OLLAMA_BAD_JSON = "ollama_bad_json"
    CONFIG_MISSING = "config_missing"


class Finding(BaseModel):
    quote: str = Field(..., max_length=2000)
    context: str
    relevance: Literal["high", "medium", "low"]


class DistillResult(BaseModel):
    answer: str | None
    page_topic: str
    findings: list[Finding] = Field(..., max_length=8)
