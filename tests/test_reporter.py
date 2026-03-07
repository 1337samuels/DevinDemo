"""Tests for src.reporter — DebtReporter and sub-modules."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.reporter.reporter import DebtReporter
from src.reporter.slack_notifier import (
    SlackNotifier,
    SlackNotifyError,
    _build_summary_message,
    _notion_url,
)
from src.reporter.notion_reporter import (
    _build_db_title,
    _build_row_properties,
    _evaluate_layers,
    _extract_candidates,
    _layer_supports_removal,
    _truncate_text,
)


# ===========================================================================
# Slack helpers
# ===========================================================================

class TestNotionUrl:
    def test_removes_dashes(self) -> None:
        assert _notion_url("abc-def-123") == "https://www.notion.so/abcdef123"

    def test_no_dashes(self) -> None:
        assert _notion_url("abcdef") == "https://www.notion.so/abcdef"


class TestBuildSummaryMessage:
    def test_basic_structure(self) -> None:
        msg = _build_summary_message("db-123", [], 5, repo="owner/repo")
        blocks = msg["blocks"]
        assert len(blocks) >= 2  # header + stats
        # Header should contain repo
        header_text = blocks[0]["text"]["text"]
        assert "owner/repo" in header_text

    def test_with_pr_urls(self) -> None:
        prs = [{"candidate_id": "c1", "pr_url": "https://github.com/o/r/pull/1"}]
        msg = _build_summary_message("db-1", prs, 3)
        # Should have a divider and PR section
        types = [b["type"] for b in msg["blocks"]]
        assert "divider" in types

    def test_no_candidates(self) -> None:
        msg = _build_summary_message(None, [], 0)
        blocks = msg["blocks"]
        assert len(blocks) >= 1

    def test_no_notion_id(self) -> None:
        msg = _build_summary_message(None, [], 5)
        text = msg["blocks"][1]["text"]["text"]
        assert "Notion" not in text


# ===========================================================================
# Notion helpers
# ===========================================================================

class TestTruncateText:
    def test_short_text(self) -> None:
        assert _truncate_text("hello", 100) == "hello"

    def test_long_text(self) -> None:
        result = _truncate_text("a" * 50, 20)
        assert len(result) == 20
        assert result.endswith("...")


class TestBuildDbTitle:
    def test_with_repo(self) -> None:
        title = _build_db_title("owner/repo")
        assert "owner/repo" in title
        assert "Dead Code Report" in title

    def test_without_repo(self) -> None:
        title = _build_db_title(None)
        assert "Dead Code Report" in title


class TestLayerSupportsRemoval:
    def test_layer_1_confirmed(self) -> None:
        assert _layer_supports_removal("layer_1_reconfirm", "confirmed", {"confirmed": True}) is True

    def test_layer_1_not_confirmed(self) -> None:
        assert _layer_supports_removal("layer_1_reconfirm", "confirmed", {"confirmed": False}) is False

    def test_layer_2_stale(self) -> None:
        assert _layer_supports_removal("layer_2_git_staleness", "is_stale", {"is_stale": True}) is True

    def test_layer_3_not_active(self) -> None:
        # Layer 3: False means supports removal
        assert _layer_supports_removal(
            "layer_3_active_development", "actively_being_worked_on",
            {"actively_being_worked_on": False}
        ) is True

    def test_layer_3_active(self) -> None:
        assert _layer_supports_removal(
            "layer_3_active_development", "actively_being_worked_on",
            {"actively_being_worked_on": True}
        ) is False

    def test_layer_4_not_reachable(self) -> None:
        assert _layer_supports_removal(
            "layer_4_static_reachability", "is_reachable",
            {"is_reachable": False}
        ) is True

    def test_layer_5_supports_removal(self) -> None:
        assert _layer_supports_removal(
            "layer_5_issue_archaeology", "overall_sentiment",
            {"overall_sentiment": "supports_removal"}
        ) is True

    def test_layer_5_does_not_support(self) -> None:
        assert _layer_supports_removal(
            "layer_5_issue_archaeology", "overall_sentiment",
            {"overall_sentiment": "opposes_removal"}
        ) is False

    def test_layer_5_legacy_sentiment_key(self) -> None:
        """Layer 5 should fall back to 'sentiment' key."""
        assert _layer_supports_removal(
            "layer_5_issue_archaeology", "overall_sentiment",
            {"sentiment": "no_discussion"}
        ) is True

    def test_empty_layer_data(self) -> None:
        assert _layer_supports_removal("layer_1_reconfirm", "confirmed", {}) is False

    def test_missing_field(self) -> None:
        assert _layer_supports_removal("layer_6_test_coverage", "tests_reference_candidate", {}) is False


class TestEvaluateLayers:
    def test_all_support_removal(self) -> None:
        layer_results = {
            "layer_1_reconfirm": {"confirmed": True},
            "layer_2_git_staleness": {"is_stale": True},
            "layer_3_active_development": {"actively_being_worked_on": False},
            "layer_4_static_reachability": {"is_reachable": False},
            "layer_5_issue_archaeology": {"overall_sentiment": "supports_removal"},
            "layer_6_test_coverage": {"tests_reference_candidate": False},
            "layer_7_runtime_signals": {"referenced_in_infra": False},
            "layer_8_external_consumers": {"is_exported": False},
        }
        checks, count = _evaluate_layers(layer_results)
        assert count == 8
        assert all(checks.values())

    def test_none_support_removal(self) -> None:
        checks, count = _evaluate_layers({})
        assert count == 0


class TestExtractCandidates:
    def test_extracts_and_sorts(self, sample_validate_results: dict[str, Any]) -> None:
        candidates = _extract_candidates(sample_validate_results)
        assert len(candidates) == 3
        # HIGH confidence should be first
        assert candidates[0]["confidence"] == "HIGH"

    def test_with_cleanup_results(self, sample_validate_results: dict[str, Any]) -> None:
        cleanup = [
            {
                "candidate_ids": ["abc123def456"],
                "pr_url": "https://github.com/o/r/pull/1",
            }
        ]
        candidates = _extract_candidates(sample_validate_results, cleanup)
        high = [c for c in candidates if c["confidence"] == "HIGH"]
        assert high[0]["pr_opened"] is True
        assert high[0]["pr_url"] == "https://github.com/o/r/pull/1"


# ===========================================================================
# DebtReporter
# ===========================================================================

class TestDebtReporter:
    def test_detect_targets_defaults(self) -> None:
        reporter = DebtReporter()
        targets = reporter._detect_targets()
        assert "stdout" in targets
        assert "notion" not in targets
        assert "slack" not in targets

    def test_detect_targets_with_notion(self) -> None:
        reporter = DebtReporter(notion_api_key="ntn_test")
        targets = reporter._detect_targets()
        assert "notion" in targets

    def test_detect_targets_with_slack(self) -> None:
        reporter = DebtReporter(slack_webhook_url="https://hooks.slack.com/test")
        targets = reporter._detect_targets()
        assert "slack" in targets

    def test_report_stdout_only(self, sample_validate_results: dict[str, Any]) -> None:
        reporter = DebtReporter()
        result = reporter.report(sample_validate_results, targets=["stdout"])
        assert result["candidates_processed"] == 3
        assert result["notion_database_id"] is None
        assert result["slack_summary_sent"] is False

    def test_report_no_candidates(self) -> None:
        reporter = DebtReporter()
        empty_findings: dict[str, Any] = {
            "feature_flags": [],
            "dead_code": [],
            "tech_debt": [],
        }
        result = reporter.report(empty_findings, targets=["stdout"])
        assert result["candidates_processed"] == 0

    @patch("src.reporter.reporter.NotionReporter")
    def test_report_notion(
        self,
        mock_notion_cls: MagicMock,
        sample_validate_results: dict[str, Any],
    ) -> None:
        mock_instance = MagicMock()
        mock_instance.publish.return_value = "db-123"
        mock_notion_cls.return_value = mock_instance

        reporter = DebtReporter(notion_api_key="ntn_test", notion_parent_page_id="page-1")
        result = reporter.report(sample_validate_results, targets=["notion"])
        assert result["notion_database_id"] == "db-123"
        mock_instance.publish.assert_called_once()

    @patch("src.reporter.reporter.SlackNotifier")
    def test_report_slack(
        self,
        mock_slack_cls: MagicMock,
        sample_validate_results: dict[str, Any],
    ) -> None:
        mock_instance = MagicMock()
        mock_slack_cls.return_value = mock_instance

        reporter = DebtReporter(slack_webhook_url="https://hooks.slack.com/test")
        result = reporter.report(sample_validate_results, targets=["slack"])
        assert result["slack_summary_sent"] is True
        mock_instance.notify_report_complete.assert_called_once()

    @patch("src.reporter.reporter.SlackNotifier")
    def test_report_slack_failure(
        self,
        mock_slack_cls: MagicMock,
        sample_validate_results: dict[str, Any],
    ) -> None:
        mock_instance = MagicMock()
        mock_instance.notify_report_complete.side_effect = SlackNotifyError(500, "fail")
        mock_slack_cls.return_value = mock_instance

        reporter = DebtReporter(slack_webhook_url="https://hooks.slack.com/test")
        result = reporter.report(sample_validate_results, targets=["slack"])
        assert result["slack_summary_sent"] is False

    def test_notion_database_id_property(self) -> None:
        reporter = DebtReporter(notion_database_id="db-existing")
        assert reporter.notion_database_id == "db-existing"
