"""Part 4 --- Report findings and cleanup results.

Orchestrates publishing validated findings to Notion and sending Slack
notifications when cleanup PRs are opened.
"""

from __future__ import annotations

from typing import Any

from src.reporter.notion_reporter import NotionReporter, _extract_candidates
from src.reporter.slack_notifier import SlackNotifier


class DebtReporter:
    """Generate and publish tech-debt reports to Notion and Slack."""

    def __init__(
        self,
        notion_api_key: str | None = None,
        notion_database_id: str | None = None,
        notion_parent_page_id: str | None = None,
        slack_webhook_url: str | None = None,
    ) -> None:
        self._notion_api_key = notion_api_key
        self._notion_database_id = notion_database_id
        self._notion_parent_page_id = notion_parent_page_id
        self._slack_webhook_url = slack_webhook_url

    @property
    def notion_database_id(self) -> str | None:
        """Return the current Notion database ID."""
        return self._notion_database_id

    def report(
        self,
        findings: dict[str, Any],
        cleanup_results: list[dict[str, Any]] | None = None,
        *,
        targets: list[str] | None = None,
    ) -> dict[str, Any]:
        """Publish a tech-debt report to the configured targets.

        Args:
            findings: Validated findings from Phase 2 (with ``validation``
                data merged into each candidate).
            cleanup_results: Optional list of Phase 3 cleanup PR results.
                Each entry should have ``pr_url``, ``candidate_ids``, etc.
            targets: Which outputs to produce.  Valid values:
                ``"notion"``, ``"slack"``, ``"stdout"``.
                Defaults to all configured targets.

        Returns:
            A summary dict with keys like ``notion_database_id``,
            ``slack_notifications_sent``, and ``candidates_processed``.
        """
        if targets is None:
            targets = self._detect_targets()

        result: dict[str, Any] = {
            "targets": targets,
            "candidates_processed": 0,
            "notion_database_id": None,
            "slack_notifications_sent": 0,
        }

        # Extract candidates once (used by both Notion and Slack)
        candidates = _extract_candidates(findings, cleanup_results)
        result["candidates_processed"] = len(candidates)

        if not candidates:
            print("[reporter] No candidates to report.")
            return result

        # --- Notion ---
        if "notion" in targets:
            result["notion_database_id"] = self._publish_notion(
                findings, cleanup_results
            )

        # --- Slack ---
        if "slack" in targets and cleanup_results:
            result["slack_notifications_sent"] = self._notify_slack(
                candidates, cleanup_results
            )

        # --- stdout ---
        if "stdout" in targets:
            self._print_stdout(findings, candidates)

        return result

    def _detect_targets(self) -> list[str]:
        """Determine which targets are configured."""
        targets: list[str] = ["stdout"]
        if self._notion_api_key:
            targets.append("notion")
        if self._slack_webhook_url:
            targets.append("slack")
        return targets

    def _publish_notion(
        self,
        findings: dict[str, Any],
        cleanup_results: list[dict[str, Any]] | None,
    ) -> str | None:
        """Publish findings to Notion and return the database ID."""
        if not self._notion_api_key:
            print("[reporter] Notion API key not configured; skipping.")
            return None

        reporter = NotionReporter(
            api_key=self._notion_api_key,
            database_id=self._notion_database_id,
            parent_page_id=self._notion_parent_page_id,
        )
        database_id = reporter.publish(findings, cleanup_results)
        # Store for future reference
        self._notion_database_id = database_id
        return database_id

    def _notify_slack(
        self,
        candidates: list[dict[str, Any]],
        cleanup_results: list[dict[str, Any]],
    ) -> int:
        """Send Slack notifications for PRs that were opened."""
        if not self._slack_webhook_url:
            print("[reporter] Slack webhook not configured; skipping.")
            return 0

        notifier = SlackNotifier(self._slack_webhook_url)
        return notifier.notify_batch(candidates, cleanup_results)

    @staticmethod
    def _print_stdout(
        findings: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> None:
        """Print a human-readable summary to stdout."""
        report = findings.get("validation_report", {})
        counts = report.get("confidence_counts", {})

        print("\n" + "=" * 60)
        print("PHASE 4 --- REPORT SUMMARY")
        print("=" * 60)
        print(f"  Total candidates: {len(candidates)}")
        print(f"  HIGH confidence:  {counts.get('HIGH', 0)}")
        print(f"  MEDIUM confidence: {counts.get('MEDIUM', 0)}")
        print(f"  LOW confidence:   {counts.get('LOW', 0)}")
        print(f"  EXEMPT:           {counts.get('EXEMPT', 0)}")
        print()

        for row in candidates:
            pr_marker = " [PR]" if row["pr_opened"] else ""
            print(
                f"  [{row['confidence']}] {row['category']} "
                f"{row['file']}:{row['line']} "
                f"({row['validation_count']}/8 layers){pr_marker}"
            )
            print(f"         {row['summary'][:100]}")

        print("=" * 60)
