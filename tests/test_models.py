import pytest
from pydantic import ValidationError

from decant.models import (
    DistillResult,
    ErrorCode,
    Finding,
    SearchResult,
    TerseDistillResult,
    TerseFinding,
)


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
        "config_missing_fast_model",
        "search_no_api_key",
        "search_unauthorized",
        "search_rate_limited",
        "search_unavailable",
        "search_bad_query",
    }


def test_terse_finding_has_no_quote_field():
    f = TerseFinding(context="ctx", relevance="high")
    assert f.model_dump() == {"context": "ctx", "relevance": "high"}


def test_terse_distill_result_schema_excludes_quote():
    import json as _json

    schema_text = _json.dumps(TerseDistillResult.model_json_schema())
    assert "quote" not in schema_text


def test_terse_distill_result_caps_findings_at_eight():
    findings = [TerseFinding(context="ctx", relevance="low") for _ in range(8)]
    TerseDistillResult(answer=None, page_topic="p", findings=findings)
    with pytest.raises(ValidationError):
        TerseDistillResult(
            answer=None,
            page_topic="p",
            findings=findings + [TerseFinding(context="ctx", relevance="low")],
        )


def test_search_result_age_nullable():
    r = SearchResult(
        url="https://x.com", title="t", hostname="x.com", age=None, snippets=[]
    )
    assert r.age is None
