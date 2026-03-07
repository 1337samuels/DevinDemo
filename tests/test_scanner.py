"""Tests for src.scanner.identifier — helper functions."""

from __future__ import annotations

import hashlib

import pytest

from src.scanner.identifier import (
    FeatureFlagScanner,
    _chunk_list,
    _enrich_findings,
    _enrich_results,
    _extract_json_block,
    _fmt_elapsed,
    _make_finding_id,
)


# ---------------------------------------------------------------------------
# _make_finding_id
# ---------------------------------------------------------------------------

class TestMakeFindingId:
    def test_deterministic(self) -> None:
        id1 = _make_finding_id("feature_flag", "src/config.py", 10)
        id2 = _make_finding_id("feature_flag", "src/config.py", 10)
        assert id1 == id2

    def test_different_inputs_different_ids(self) -> None:
        id1 = _make_finding_id("feature_flag", "a.py", 1)
        id2 = _make_finding_id("dead_code", "a.py", 1)
        assert id1 != id2

    def test_length_is_12(self) -> None:
        result = _make_finding_id("tech_debt", "b.py", 42)
        assert len(result) == 12

    def test_matches_sha256(self) -> None:
        raw = "feature_flag:src/x.py:5"
        expected = hashlib.sha256(raw.encode()).hexdigest()[:12]
        assert _make_finding_id("feature_flag", "src/x.py", 5) == expected


# ---------------------------------------------------------------------------
# _enrich_findings
# ---------------------------------------------------------------------------

class TestEnrichFindings:
    def test_adds_id_and_status(self) -> None:
        findings = [{"file": "a.py", "line": 1, "code_snippet": "x"}]
        enriched = _enrich_findings(findings, "dead_code")
        assert len(enriched) == 1
        assert "id" in enriched[0]
        assert enriched[0]["verification_status"] == "unverified"

    def test_preserves_original_fields(self) -> None:
        findings = [{"file": "b.py", "line": 10, "reasoning": "unused"}]
        enriched = _enrich_findings(findings, "tech_debt")
        assert enriched[0]["file"] == "b.py"
        assert enriched[0]["reasoning"] == "unused"

    def test_empty_list(self) -> None:
        assert _enrich_findings([], "feature_flag") == []

    def test_multiple_items(self) -> None:
        findings = [
            {"file": "a.py", "line": 1},
            {"file": "a.py", "line": 2},
        ]
        enriched = _enrich_findings(findings, "dead_code")
        assert len(enriched) == 2
        assert enriched[0]["id"] != enriched[1]["id"]


# ---------------------------------------------------------------------------
# _enrich_results
# ---------------------------------------------------------------------------

class TestEnrichResults:
    def test_wraps_with_meta(self) -> None:
        raw = {
            "repo": "owner/repo",
            "feature_flags": [{"file": "a.py", "line": 1}],
            "dead_code": [],
            "tech_debt": [],
            "summary": {"files_scanned": 1},
        }
        result = _enrich_results(raw, session_id="ses_1", repo="owner/repo", total_files=5)
        assert result["meta"]["session_id"] == "ses_1"
        assert result["meta"]["repo"] == "owner/repo"
        assert result["summary"]["total_files"] == 5
        assert len(result["feature_flags"]) == 1
        assert "id" in result["feature_flags"][0]


# ---------------------------------------------------------------------------
# _fmt_elapsed
# ---------------------------------------------------------------------------

class TestFmtElapsed:
    def test_seconds_only(self) -> None:
        assert _fmt_elapsed(45) == "45s"

    def test_minutes_and_seconds(self) -> None:
        assert _fmt_elapsed(125) == "2m05s"

    def test_zero(self) -> None:
        assert _fmt_elapsed(0) == "0s"

    def test_exact_minute(self) -> None:
        assert _fmt_elapsed(60) == "1m00s"


# ---------------------------------------------------------------------------
# _chunk_list
# ---------------------------------------------------------------------------

class TestChunkList:
    def test_even_split(self) -> None:
        result = _chunk_list(["a", "b", "c", "d"], 2)
        assert result == [["a", "b"], ["c", "d"]]

    def test_remainder(self) -> None:
        result = _chunk_list(["a", "b", "c"], 2)
        assert result == [["a", "b"], ["c"]]

    def test_single_chunk(self) -> None:
        result = _chunk_list(["a", "b"], 10)
        assert result == [["a", "b"]]

    def test_empty_list(self) -> None:
        assert _chunk_list([], 5) == []

    def test_chunk_size_one(self) -> None:
        result = _chunk_list(["a", "b", "c"], 1)
        assert result == [["a"], ["b"], ["c"]]


# ---------------------------------------------------------------------------
# _extract_json_block
# ---------------------------------------------------------------------------

class TestExtractJsonBlock:
    def test_code_fence_json(self) -> None:
        text = 'Here is the result:\n```json\n{"files": ["a.py", "b.py"]}\n```\nDone.'
        result = _extract_json_block(text)
        assert result is not None
        assert result["files"] == ["a.py", "b.py"]

    def test_code_fence_no_language(self) -> None:
        text = 'Result:\n```\n{"count": 5}\n```'
        result = _extract_json_block(text)
        assert result is not None
        assert result["count"] == 5

    def test_raw_json_fallback(self) -> None:
        text = 'The result is {"key": "value"} and more text.'
        result = _extract_json_block(text)
        assert result is not None
        assert result["key"] == "value"

    def test_no_json(self) -> None:
        assert _extract_json_block("no json here") is None

    def test_invalid_json_in_fence(self) -> None:
        text = '```json\n{invalid json}\n```'
        # Falls through to raw fallback, which also fails
        assert _extract_json_block(text) is None

    def test_nested_objects(self) -> None:
        text = '```json\n{"summary": {"count": 3, "items": [1, 2]}}\n```'
        result = _extract_json_block(text)
        assert result is not None
        assert result["summary"]["count"] == 3


# ---------------------------------------------------------------------------
# FeatureFlagScanner static/class methods
# ---------------------------------------------------------------------------

class TestFeatureFlagScannerHelpers:
    def test_count_scanned_files(self) -> None:
        session = {
            "messages": [
                {"type": "user_message", "message": "scan these files"},
                {
                    "type": "devin_message",
                    "message": (
                        "Scanning...\n[SCANNED] src/a.py\n"
                        "[SCANNED] src/b.py\nDone."
                    ),
                },
            ]
        }
        count = FeatureFlagScanner._count_scanned_files(
            session, ["src/a.py", "src/b.py", "src/c.py"]
        )
        assert count == 2

    def test_count_scanned_files_no_matches(self) -> None:
        session = {"messages": [{"type": "devin_message", "message": "Working..."}]}
        count = FeatureFlagScanner._count_scanned_files(session, ["src/a.py"])
        assert count == 0

    def test_count_scanned_files_ignores_other_batches(self) -> None:
        """Only counts files that belong to the current batch."""
        session = {
            "messages": [
                {
                    "type": "devin_message",
                    "message": "[SCANNED] other/file.py\n[SCANNED] src/a.py",
                }
            ]
        }
        count = FeatureFlagScanner._count_scanned_files(session, ["src/a.py"])
        assert count == 1

    def test_last_devin_message(self) -> None:
        session = {
            "messages": [
                {"type": "user_message", "message": "hi"},
                {"type": "devin_message", "message": "first"},
                {"type": "devin_message", "message": "second"},
            ]
        }
        assert FeatureFlagScanner._last_devin_message(session) == "second"

    def test_last_devin_message_empty(self) -> None:
        assert FeatureFlagScanner._last_devin_message({"messages": []}) == ""
        assert FeatureFlagScanner._last_devin_message({}) == ""

    def test_parse_discovery_response_structured(self) -> None:
        scanner = FeatureFlagScanner.__new__(FeatureFlagScanner)
        session = {
            "structured_output": {"files": ["a.py", "b.py"]},
            "messages": [],
        }
        result = scanner._parse_discovery_response(session)
        assert result == ["a.py", "b.py"]

    def test_parse_discovery_response_from_message(self) -> None:
        scanner = FeatureFlagScanner.__new__(FeatureFlagScanner)
        session = {
            "structured_output": None,
            "messages": [
                {
                    "type": "devin_message",
                    "message": '```json\n{"files": ["x.py"], "total_files": 1}\n```',
                }
            ],
        }
        result = scanner._parse_discovery_response(session)
        assert result == ["x.py"]

    def test_parse_discovery_response_raises_on_failure(self) -> None:
        scanner = FeatureFlagScanner.__new__(FeatureFlagScanner)
        session = {
            "structured_output": None,
            "status": "running",
            "status_detail": "working",
            "messages": [
                {"type": "devin_message", "message": "I could not find any files."}
            ],
        }
        with pytest.raises(RuntimeError, match="Could not extract file list"):
            scanner._parse_discovery_response(session)

    def test_parse_batch_response_structured(self) -> None:
        scanner = FeatureFlagScanner.__new__(FeatureFlagScanner)
        session = {
            "structured_output": {
                "feature_flags": [{"file": "a.py", "line": 1}],
                "dead_code": [],
                "tech_debt": [],
            },
            "messages": [],
        }
        result = scanner._parse_batch_response(session)
        assert len(result["feature_flags"]) == 1

    def test_parse_batch_response_from_message(self) -> None:
        scanner = FeatureFlagScanner.__new__(FeatureFlagScanner)
        json_block = (
            '```json\n{"feature_flags": [], "dead_code": [{"file": "b.py"}], '
            '"tech_debt": [], "summary": {}}\n```'
        )
        session = {
            "structured_output": None,
            "messages": [{"type": "devin_message", "message": json_block}],
        }
        result = scanner._parse_batch_response(session)
        assert len(result["dead_code"]) == 1

    def test_parse_batch_response_fallback_empty(self) -> None:
        scanner = FeatureFlagScanner.__new__(FeatureFlagScanner)
        session = {
            "structured_output": None,
            "messages": [{"type": "devin_message", "message": "no json here"}],
        }
        result = scanner._parse_batch_response(session)
        assert result["feature_flags"] == []
        assert result["dead_code"] == []
