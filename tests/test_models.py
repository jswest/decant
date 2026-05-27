import pytest
from pydantic import ValidationError

from decant.models import DistillResult, ErrorCode, Finding


def test_finding_rejects_unknown_relevance():
    with pytest.raises(ValidationError):
        Finding(quote="x", context="ctx", relevance="unknown")


def test_finding_enforces_quote_max_length():
    Finding(quote="a" * 2000, context="ctx", relevance="low")
    with pytest.raises(ValidationError):
        Finding(quote="a" * 2001, context="ctx", relevance="low")


def test_distill_result_caps_findings_at_eight():
    findings = [
        Finding(quote=f"q{i}", context="ctx", relevance="low") for i in range(8)
    ]
    DistillResult(answer=None, page_topic="p", findings=findings)
    with pytest.raises(ValidationError):
        DistillResult(
            answer=None,
            page_topic="p",
            findings=findings
            + [Finding(quote="q8", context="ctx", relevance="low")],
        )


def test_distill_result_answer_nullable():
    r = DistillResult(answer=None, page_topic="x", findings=[])
    assert r.answer is None


def test_error_code_values_match_spec():
    assert {c.value for c in ErrorCode} == {
        "invalid_url",
        "robots_disallowed",
        "fetch_failed",
        "extract_empty",
        "ollama_unavailable",
        "ollama_timeout",
        "ollama_bad_json",
        "config_missing",
    }
