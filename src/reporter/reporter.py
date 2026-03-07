"""Part 4 --- Report findings and cleanup results.

Orchestrates publishing validated findings to Notion and sending a Slack
summary message with a link to the Notion report and all opened PRs.
"""

from __future__ import annotations

from typing import Any

from src.reporter.notion_reporter import NotionReporter, _extract_candidates
from src.reporter.slack_notifier import SlackNotifier, SlackNotifyError


class DebtReporter:
    """Generate and publish tech-debt reports to Notion and Slack."""

    def __init__(
        self,
        notion_api_key: str | None = None,
        notion_database_id: str | None = None,
        notion_parent_page_id: str | None = None,
        slack_webhook_url: str | None = None,
        slack_bot_token: str | None = None,
        slack_channel_id: str | None = None,
    ) -> None:
        self._notion_api_key = notion_api_key
        self._notion_database_id = notion_database_id
        self._notion_parent_page_id = notion_parent_page_id
        self._slack_webhook_url = slack_webhook_url
        self._slack_bot_token = slack_bot_token
        self._slack_channel_id = slack_channel_id

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
        validate_file: str | None = None,
        cleanup_file: str | None = None,
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
            "slack_summary_sent": False,
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
        if "slack" in targets:
            result["slack_summary_sent"] = self._notify_slack(
                notion_database_id=result["notion_database_id"],
                cleanup_results=cleanup_results,
                candidates_processed=len(candidates),
                repo=findings.get("repo"),
                validate_file=validate_file,
                cleanup_file=cleanup_file,
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
        notion_database_id: str | None,
        cleanup_results: list[dict[str, Any]] | None,
        candidates_processed: int,
        repo: str | None = None,
        validate_file: str | None = None,
        cleanup_file: str | None = None,
    ) -> bool:
        """Send a single Slack summary with Notion link and PR URLs.

        When ``slack_bot_token`` and ``slack_channel_id`` are configured,
        also uploads the Phase 2 (validate) and Phase 3 (cleanup) JSON
        result files to the Slack channel.

        Returns ``True`` only when the message is actually delivered.
        """
        if not self._slack_webhook_url:
            print("[reporter] Slack webhook not configured; skipping.")
            return False

        notifier = SlackNotifier(
            self._slack_webhook_url,
            bot_token=self._slack_bot_token,
            channel_id=self._slack_channel_id,
        )
        try:
            notifier.notify_report_complete(
                notion_database_id=notion_database_id,
                cleanup_results=cleanup_results,
                candidates_processed=candidates_processed,
                repo=repo,
            )
        except SlackNotifyError as exc:
            print(f"[reporter] WARNING: Slack notification failed: {exc}")
            return False

        # Upload Phase 2 & 3 JSON files if bot token is configured
        file_paths = [p for p in [validate_file, cleanup_file] if p]
        if file_paths:
            try:
                repo_label = repo or "unknown repo"
                notifier.upload_files(
                    file_paths,
                    comment=f"DevinDemo results for {repo_label}",
                )
            except SlackNotifyError as exc:
                print(f"[reporter] WARNING: Slack file upload failed: {exc}")

        return True

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
