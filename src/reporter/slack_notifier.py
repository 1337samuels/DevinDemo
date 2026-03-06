"""Phase 4, Part 2 --- Send Slack notifications when cleanup PRs are opened.

Uses Slack Incoming Webhooks to post a message to a designated channel.
Each message includes the PR link, last commit date for the code, and
all reasons the code was deemed legacy.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from src.reporter.notion_reporter import LAYER_DEFINITIONS, _layer_supports_removal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_legacy_reasons(
    layer_results: dict[str, Any],
) -> list[str]:
    """Extract human-readable reasons from layer results that support removal."""
    reasons: list[str] = []

    for layer_key, layer_name, field_key in LAYER_DEFINITIONS:
        layer_data = layer_results.get(layer_key, {})
        if not layer_data:
            continue

        supports = _layer_supports_removal(layer_key, field_key, layer_data)
        if not supports:
            continue

        # Build a descriptive reason from each layer
        if layer_key == "layer_1_reconfirm":
            explanation = layer_data.get("explanation", "Detection confirmed as real code.")
            reasons.append(f"*{layer_name}*: {explanation}")

        elif layer_key == "layer_2_git_staleness":
            days = layer_data.get("days_since_last_edit", "?")
            last_date = layer_data.get("last_meaningful_edit_date", "unknown")
            reasons.append(
                f"*{layer_name}*: Code is stale — last meaningful edit "
                f"was {days} days ago ({last_date})."
            )

        elif layer_key == "layer_3_active_development":
            reasons.append(
                f"*{layer_name}*: No active development found on this code."
            )

        elif layer_key == "layer_4_static_reachability":
            reasons.append(
                f"*{layer_name}*: Code is not reachable from any entry point."
            )

        elif layer_key == "layer_5_issue_archaeology":
            sentiment = layer_data.get("overall_sentiment", "unknown")
            reasons.append(
                f"*{layer_name}*: Issue/discussion sentiment: {sentiment}."
            )

        elif layer_key == "layer_6_test_coverage":
            reasons.append(
                f"*{layer_name}*: No tests reference this candidate."
            )

        elif layer_key == "layer_7_runtime_signals":
            reasons.append(
                f"*{layer_name}*: Not referenced in infrastructure/deployment configs."
            )

        elif layer_key == "layer_8_external_consumers":
            reasons.append(
                f"*{layer_name}*: Symbol is not exported or consumed externally."
            )

    return reasons


def _build_slack_message(
    candidate: dict[str, Any],
    pr_url: str,
) -> dict[str, Any]:
    """Build the Slack message payload for a single PR notification.

    Uses Slack Block Kit for rich formatting.
    """
    candidate_id = candidate.get("candidate_id", "unknown")
    category = candidate.get("category", "unknown")
    file_path = candidate.get("file", "unknown")
    line = candidate.get("line", 0)
    confidence = candidate.get("confidence", "?")
    summary = candidate.get("summary", "No summary available.")
    last_edit = candidate.get("last_meaningful_edit_date", "N/A")
    layer_results = candidate.get("layer_results_raw", {})

    reasons = _build_legacy_reasons(layer_results)
    reasons_text = "\n".join(f"  - {r}" for r in reasons) if reasons else "  No specific reasons recorded."

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Cleanup PR Opened: {category}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Candidate:* `{candidate_id}`\n"
                    f"*File:* `{file_path}:{line}`\n"
                    f"*Confidence:* {confidence}\n"
                    f"*Last commit to this code:* {last_edit}\n"
                    f"*PR:* <{pr_url}|View Pull Request>"
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Summary:*\n{summary}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Reasons deemed legacy:*\n{reasons_text}",
            },
        },
    ]

    return {"blocks": blocks}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class SlackNotifier:
    """Send Slack notifications for cleanup PRs via incoming webhooks."""

    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url

    def notify_pr_opened(
        self,
        candidate: dict[str, Any],
        pr_url: str,
    ) -> None:
        """Send a Slack message about a single PR being opened.

        Args:
            candidate: A prepared candidate dict (from ``_extract_candidates``).
            pr_url: The URL of the opened PR.

        Raises:
            SlackNotifyError: If the webhook call fails.
        """
        payload = _build_slack_message(candidate, pr_url)
        self._send(payload)
        print(
            f"[slack] Notified about PR for candidate "
            f"{candidate.get('candidate_id', '?')}"
        )

    def notify_batch(
        self,
        candidates: list[dict[str, Any]],
        cleanup_results: list[dict[str, Any]],
    ) -> int:
        """Send Slack notifications for all candidates that have PRs.

        Only candidates whose PR URL is present in *cleanup_results* will
        generate a notification.

        Args:
            candidates: Prepared candidate rows (from ``_extract_candidates``).
            cleanup_results: Phase 3 cleanup results with ``pr_url`` and
                ``candidate_ids``.

        Returns:
            The number of notifications sent.
        """
        # Build lookup: candidate_id -> pr_url
        pr_lookup: dict[str, str] = {}
        for pr_info in cleanup_results:
            pr_url = pr_info.get("pr_url", "")
            if not pr_url:
                continue
            for cid in pr_info.get("candidate_ids", []):
                pr_lookup[cid] = pr_url

        sent = 0
        for candidate in candidates:
            cid = candidate.get("candidate_id", "")
            pr_url = pr_lookup.get(cid, "")
            if pr_url:
                self.notify_pr_opened(candidate, pr_url)
                sent += 1

        print(f"[slack] Sent {sent} notification(s).")
        return sent

    def _send(self, payload: dict[str, Any]) -> None:
        """Post a JSON payload to the Slack webhook URL."""
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode() if exc.fp else ""
            raise SlackNotifyError(exc.code, str(exc.reason), body) from exc
        except urllib.error.URLError as exc:
            raise SlackNotifyError(0, str(exc.reason)) from exc


class SlackNotifyError(Exception):
    """Raised when a Slack webhook call fails."""

    def __init__(self, status: int, message: str, body: str = "") -> None:
        self.status = status
        self.body = body
        super().__init__(f"Slack webhook {status}: {message}")
