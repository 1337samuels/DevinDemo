"""Tests for src.validator.validator — helper functions."""

from __future__ import annotations

from typing import Any

import pytest

from src.validator.validator import (
    _candidate_sort_key,
    _escape_braces,
    _extract_json_block,
    _fmt_elapsed,
    _format_candidate_block,
    _generate_recommendations,
    _merge_validation_into_findings,
    _status_from_confidence,
    build_summary_report,
    group_candidates,
    CONFIDENCE_EXEMPT,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
)


# ---------------------------------------------------------------------------
# _fmt_elapsed
# ---------------------------------------------------------------------------

class TestFmtElapsed:
    def test_seconds_only(self) -> None:
        assert _fmt_elapsed(30) == "30s"

    def test_minutes(self) -> None:
        assert _fmt_elapsed(90) == "1m30s"


# ---------------------------------------------------------------------------
# _extract_json_block (same logic as scanner, but separate function)
# ---------------------------------------------------------------------------

class TestExtractJsonBlock:
    def test_json_fence(self) -> None:
        text = 'Output:\n```json\n{"candidates": [{"id": "abc"}]}\n```'
        result = _extract_json_block(text)
        assert result is not None
        assert "candidates" in result

    def test_raw_json(self) -> None:
        result = _extract_json_block('Result: {"key": 42}')
        assert result is not None
        assert result["key"] == 42

    def test_none_on_no_json(self) -> None:
        assert _extract_json_block("nothing here") is None


# ---------------------------------------------------------------------------
# _candidate_sort_key
# ---------------------------------------------------------------------------

class TestCandidateSortKey:
    def test_with_directory(self) -> None:
        assert _candidate_sort_key({"file": "src/utils/helper.py"}) == "src/utils"

    def test_without_directory(self) -> None:
        assert _candidate_sort_key({"file": "main.py"}) == ""

    def test_empty_file(self) -> None:
        assert _candidate_sort_key({}) == ""


# ---------------------------------------------------------------------------
# _escape_braces
# ---------------------------------------------------------------------------

class TestEscapeBraces:
    def test_escapes_curly_braces(self) -> None:
        assert _escape_braces("{hello}") == "{{hello}}"

    def test_no_braces(self) -> None:
        assert _escape_braces("plain text") == "plain text"


# ---------------------------------------------------------------------------
# _format_candidate_block
# ---------------------------------------------------------------------------

class TestFormatCandidateBlock:
    def test_basic_format(self) -> None:
        candidates = [
            {
                "id": "abc123",
                "file": "src/config.py",
                "line": 10,
                "flag_name": "FEATURE_X",
                "code_snippet": "os.getenv('FEATURE_X')",
                "reasoning": "Stale flag.",
            }
        ]
        block = _format_candidate_block(candidates, "feature_flag")
        assert "### Candidate 1" in block
        assert "abc123" in block
        assert "feature_flag" in block
        assert "src/config.py" in block
        assert "FEATURE_X" in block

    def test_multiple_candidates(self) -> None:
        candidates = [
            {"id": "a", "file": "x.py", "line": 1},
            {"id": "b", "file": "y.py", "line": 2},
        ]
        block = _format_candidate_block(candidates, "dead_code")
        assert "### Candidate 1" in block
        assert "### Candidate 2" in block

    def test_escapes_braces_in_snippet(self) -> None:
        candidates = [
            {"id": "c", "file": "z.py", "line": 5, "code_snippet": "d = {1: 2}"}
        ]
        block = _format_candidate_block(candidates, "tech_debt")
        assert "{{1: 2}}" in block


# ---------------------------------------------------------------------------
# group_candidates
# ---------------------------------------------------------------------------

class TestGroupCandidates:
    def test_groups_by_category(self) -> None:
        findings: dict[str, Any] = {
            "feature_flags": [
                {"id": "f1", "file": "src/a.py", "line": 1},
                {"id": "f2", "file": "src/a.py", "line": 2},
            ],
            "dead_code": [
                {"id": "d1", "file": "src/b.py", "line": 10},
            ],
            "tech_debt": [],
        }
        batches = group_candidates(findings, max_batch_size=5)
        # Should have at least 2 batches: one for feature_flags, one for dead_code
        labels = [label for label, _ in batches]
        assert "feature_flag" in labels
        assert "dead_code" in labels

    def test_respects_max_batch_size(self) -> None:
        findings: dict[str, Any] = {
            "feature_flags": [
                {"id": f"f{i}", "file": "src/a.py", "line": i}
                for i in range(10)
            ],
            "dead_code": [],
            "tech_debt": [],
        }
        batches = group_candidates(findings, max_batch_size=3)
        for _label, items in batches:
            assert len(items) <= 3

    def test_empty_findings(self) -> None:
        findings: dict[str, Any] = {
            "feature_flags": [],
            "dead_code": [],
            "tech_debt": [],
        }
        assert group_candidates(findings) == []

    def test_missing_category_keys(self) -> None:
        assert group_candidates({}) == []


# ---------------------------------------------------------------------------
# _merge_validation_into_findings
# ---------------------------------------------------------------------------

class TestMergeValidationIntoFindings:
    def test_merges_validation_data(self) -> None:
        findings: dict[str, Any] = {
            "feature_flags": [
                {"id": "f1", "file": "a.py", "line": 1, "verification_status": "unverified"}
            ],
            "dead_code": [],
            "tech_debt": [],
        }
        validation_map = {
            "f1": {
                "candidate_id": "f1",
                "confidence": "HIGH",
                "summary": "Confirmed dead.",
            }
        }
        result = _merge_validation_into_findings(findings, validation_map)
        assert result["feature_flags"][0]["verification_status"] == "verified"
        assert result["feature_flags"][0]["validation"]["confidence"] == "HIGH"

    def test_unmatched_findings_unchanged(self) -> None:
        findings: dict[str, Any] = {
            "feature_flags": [
                {"id": "f2", "file": "b.py", "line": 5, "verification_status": "unverified"}
            ],
            "dead_code": [],
            "tech_debt": [],
        }
        result = _merge_validation_into_findings(findings, {})
        assert result["feature_flags"][0]["verification_status"] == "unverified"


# ---------------------------------------------------------------------------
# _status_from_confidence
# ---------------------------------------------------------------------------

class TestStatusFromConfidence:
    def test_high(self) -> None:
        assert _status_from_confidence(CONFIDENCE_HIGH) == "verified"

    def test_medium(self) -> None:
        assert _status_from_confidence(CONFIDENCE_MEDIUM) == "needs_review"

    def test_low(self) -> None:
        assert _status_from_confidence(CONFIDENCE_LOW) == "needs_review"

    def test_exempt(self) -> None:
        assert _status_from_confidence(CONFIDENCE_EXEMPT) == "false_positive"

    def test_unknown(self) -> None:
        assert _status_from_confidence("UNKNOWN") == "unverified"


# ---------------------------------------------------------------------------
# build_summary_report
# ---------------------------------------------------------------------------

class TestBuildSummaryReport:
    def test_counts_and_buckets(self) -> None:
        validation_map = {
            "c1": {"candidate_id": "c1", "confidence": "HIGH", "summary": "good", "suggested_pr_title": "T", "suggested_pr_description": "D"},
            "c2": {"candidate_id": "c2", "confidence": "MEDIUM", "summary": "ok", "suggested_pr_title": "T2", "suggested_pr_description": "D2"},
            "c3": {"candidate_id": "c3", "confidence": "LOW", "summary": "meh", "blockers": ["unclear"]},
            "c4": {"candidate_id": "c4", "confidence": "EXEMPT", "summary": "fp", "exempt_reason": "false positive"},
        }
        report = build_summary_report(validation_map, ["pattern1"])
        assert report["confidence_counts"]["HIGH"] == 1
        assert report["confidence_counts"]["MEDIUM"] == 1
        assert report["confidence_counts"]["LOW"] == 1
        assert report["confidence_counts"]["EXEMPT"] == 1
        assert len(report["high_confidence"]) == 1
        assert len(report["medium_confidence"]) == 1
        assert len(report["low_confidence"]) == 1
        assert len(report["exempt"]) == 1
        assert "pattern1" in report["patterns_observed"]

    def test_empty_map(self) -> None:
        report = build_summary_report({}, [])
        assert report["confidence_counts"]["HIGH"] == 0
        assert report["high_confidence"] == []

    def test_recommendations_included(self) -> None:
        validation_map = {
            "c1": {"candidate_id": "c1", "confidence": "HIGH", "summary": "ok"},
        }
        report = build_summary_report(validation_map, [])
        assert len(report["recommendations"]) >= 1


# ---------------------------------------------------------------------------
# _generate_recommendations
# ---------------------------------------------------------------------------

class TestGenerateRecommendations:
    def test_high_confidence_recommendation(self) -> None:
        counts = {"HIGH": 3, "MEDIUM": 0, "LOW": 0, "EXEMPT": 0}
        recs = _generate_recommendations(counts, [])
        assert any("HIGH" in r for r in recs)

    def test_no_recommendations_when_all_zero(self) -> None:
        counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "EXEMPT": 0}
        recs = _generate_recommendations(counts, [])
        assert recs == []

    def test_patterns_appended(self) -> None:
        counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "EXEMPT": 0}
        recs = _generate_recommendations(counts, ["Pattern A"])
        assert any("Pattern A" in r for r in recs)

    def test_all_confidence_levels(self) -> None:
        counts = {"HIGH": 1, "MEDIUM": 2, "LOW": 3, "EXEMPT": 4}
        recs = _generate_recommendations(counts, [])
        assert len(recs) == 4
