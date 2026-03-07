"""Tests for src.cleanup.cleanup — helper functions."""

from __future__ import annotations

from typing import Any

import pytest

from src.cleanup.cleanup import (
    _all_layers_passed,
    _extract_high_confidence_findings,
    _extract_json_block,
    _extract_pr_url_from_text,
    _fmt_elapsed,
)


# ---------------------------------------------------------------------------
# _fmt_elapsed
# ---------------------------------------------------------------------------

class TestFmtElapsed:
    def test_seconds(self) -> None:
        assert _fmt_elapsed(5) == "5s"

    def test_minutes_seconds(self) -> None:
        assert _fmt_elapsed(130) == "2m10s"


# ---------------------------------------------------------------------------
# _extract_json_block
# ---------------------------------------------------------------------------

class TestExtractJsonBlock:
    def test_json_fence(self) -> None:
        text = 'Output:\n```json\n{"pr_url": "https://github.com/o/r/pull/1"}\n```'
        result = _extract_json_block(text)
        assert result is not None
        assert result["pr_url"] == "https://github.com/o/r/pull/1"

    def test_raw_json(self) -> None:
        result = _extract_json_block('Here: {"status": "done"}')
        assert result is not None
        assert result["status"] == "done"

    def test_no_json(self) -> None:
        assert _extract_json_block("plain text") is None


# ---------------------------------------------------------------------------
# _extract_pr_url_from_text
# ---------------------------------------------------------------------------

class TestExtractPrUrlFromText:
    def test_github_pr_url(self) -> None:
        text = "I opened a PR at https://github.com/owner/repo/pull/42 for you."
        assert _extract_pr_url_from_text(text) == "https://github.com/owner/repo/pull/42"

    def test_gitlab_mr_url(self) -> None:
        text = "MR: https://gitlab.com/group/project/merge_requests/7"
        assert _extract_pr_url_from_text(text) == "https://gitlab.com/group/project/merge_requests/7"

    def test_generic_pull_url(self) -> None:
        text = "PR at https://git.example.com/org/project/pull/99"
        assert _extract_pr_url_from_text(text) == "https://git.example.com/org/project/pull/99"

    def test_no_url(self) -> None:
        assert _extract_pr_url_from_text("no url here") == ""

    def test_url_at_end_of_sentence(self) -> None:
        text = "Created https://github.com/o/r/pull/5."
        # The dot is not part of the URL
        url = _extract_pr_url_from_text(text)
        assert url.endswith("/pull/5")

    def test_url_in_parentheses(self) -> None:
        text = "(see https://github.com/o/r/pull/3)"
        url = _extract_pr_url_from_text(text)
        assert url == "https://github.com/o/r/pull/3"

    def test_url_in_angle_brackets(self) -> None:
        text = "Link: <https://github.com/o/r/pull/8>"
        url = _extract_pr_url_from_text(text)
        assert url == "https://github.com/o/r/pull/8"


# ---------------------------------------------------------------------------
# _all_layers_passed
# ---------------------------------------------------------------------------

class TestAllLayersPassed:
    def test_all_passed(self) -> None:
        validation: dict[str, Any] = {
            "layers": {
                "layer_1": {"confirmed": True},
                "layer_2": {"confirmed": True},
            }
        }
        assert _all_layers_passed(validation) is True

    def test_one_failed(self) -> None:
        validation: dict[str, Any] = {
            "layers": {
                "layer_1": {"confirmed": True},
                "layer_2": {"confirmed": False},
            }
        }
        assert _all_layers_passed(validation) is False

    def test_no_layers_key(self) -> None:
        assert _all_layers_passed({}) is False

    def test_empty_layers(self) -> None:
        assert _all_layers_passed({"layers": {}}) is False

    def test_truthy_non_dict_layer(self) -> None:
        validation: dict[str, Any] = {"layers": {"layer_1": True, "layer_2": True}}
        assert _all_layers_passed(validation) is True

    def test_falsy_non_dict_layer(self) -> None:
        validation: dict[str, Any] = {"layers": {"layer_1": True, "layer_2": False}}
        assert _all_layers_passed(validation) is False


# ---------------------------------------------------------------------------
# _extract_high_confidence_findings
# ---------------------------------------------------------------------------

class TestExtractHighConfidenceFindings:
    def test_extracts_high_only(self) -> None:
        findings: dict[str, Any] = {
            "feature_flags": [
                {
                    "id": "f1",
                    "file": "a.py",
                    "line": 1,
                    "validation": {"confidence": "HIGH"},
                },
                {
                    "id": "f2",
                    "file": "b.py",
                    "line": 2,
                    "validation": {"confidence": "MEDIUM"},
                },
            ],
            "dead_code": [
                {
                    "id": "d1",
                    "file": "c.py",
                    "line": 3,
                    "validation": {"confidence": "HIGH"},
                },
            ],
            "tech_debt": [],
        }
        results = _extract_high_confidence_findings(findings)
        assert len(results) == 2
        ids = {r["id"] for r in results}
        assert ids == {"f1", "d1"}

    def test_enriches_with_category(self) -> None:
        findings: dict[str, Any] = {
            "feature_flags": [
                {"id": "f1", "file": "a.py", "line": 1, "validation": {"confidence": "HIGH"}},
            ],
            "dead_code": [],
            "tech_debt": [],
        }
        results = _extract_high_confidence_findings(findings)
        assert results[0]["category"] == "feature_flags"

    def test_no_high_confidence(self) -> None:
        findings: dict[str, Any] = {
            "feature_flags": [
                {"id": "f1", "validation": {"confidence": "LOW"}},
            ],
            "dead_code": [],
            "tech_debt": [],
        }
        assert _extract_high_confidence_findings(findings) == []

    def test_empty_findings(self) -> None:
        assert _extract_high_confidence_findings({}) == []

    def test_missing_validation(self) -> None:
        """Findings without validation key should not be included."""
        findings: dict[str, Any] = {
            "feature_flags": [{"id": "f1", "file": "a.py", "line": 1}],
            "dead_code": [],
            "tech_debt": [],
        }
        assert _extract_high_confidence_findings(findings) == []
