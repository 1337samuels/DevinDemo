"""Phase 4, Part 2 --- Send a Slack summary when a report completes.

Uses Slack Incoming Webhooks to post a single summary message to a
designated channel.  The message includes:

- A link to the Notion database (report)
- Links to all cleanup PRs that were opened
- A count of total candidates processed
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _notion_url(database_id: str) -> str:
    """Build a browser-friendly Notion URL from a database ID."""
    clean_id = database_id.replace("-", "")
    return f"https://www.notion.so/{clean_id}"


def _build_summary_message(
    notion_database_id: str | None,
    pr_urls: list[dict[str, str]],
    candidates_processed: int,
    repo: str | None = None,
) -> dict[str, Any]:
    """Build a single Slack summary message using Block Kit.

    Args:
        notion_database_id: The Notion database ID (may be ``None``).
        pr_urls: List of dicts with ``candidate_id`` and ``pr_url``.
        candidates_processed: Total number of candidates in the report.
        repo: Optional repository name for the header.
    """
    # Header
    header_text = "Dead Code Report Completed"
    if repo:
        header_text = f"Dead Code Report Completed --- {repo}"

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": header_text,
                "emoji": True,
            },
        },
    ]

    # Summary stats
    stats_lines = [
        f"*Candidates processed:* {candidates_processed}",
        f"*Cleanup PRs opened:* {len(pr_urls)}",
    ]

    # Notion link
    if notion_database_id:
        url = _notion_url(notion_database_id)
        stats_lines.append(f"*Notion Report:* <{url}|View in Notion>")

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "\n".join(stats_lines),
        },
    })

    # PR links
    if pr_urls:
        blocks.append({"type": "divider"})

        pr_lines: list[str] = []
        for pr_info in pr_urls:
            cid = pr_info["candidate_id"]
            url = pr_info["pr_url"]
            pr_lines.append(f"- `{cid}`: <{url}|View PR>")

        # Slack blocks have a 3000-char text limit; chunk if needed
        pr_text = "\n".join(pr_lines)
        if len(pr_text) <= 2900:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Opened PRs:*\n{pr_text}",
                },
            })
        else:
            # Split into multiple blocks
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Opened PRs:*",
                },
            })
            chunk: list[str] = []
            chunk_len = 0
            for line in pr_lines:
                if chunk_len + len(line) + 1 > 2900:
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "\n".join(chunk),
                        },
                    })
                    chunk = []
                    chunk_len = 0
                chunk.append(line)
                chunk_len += len(line) + 1
            if chunk:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "\n".join(chunk),
                    },
                })
    elif candidates_processed > 0:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "_No cleanup PRs were opened for this report._",
            },
        })

    return {"blocks": blocks}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class SlackNotifier:
    """Send Slack report summary via incoming webhooks.

    Optionally upload files to a Slack channel using a bot token.
    """

    def __init__(
        self,
        webhook_url: str,
        bot_token: str | None = None,
        channel_id: str | None = None,
    ) -> None:
        self._webhook_url = webhook_url
        self._bot_token = bot_token
        self._channel_id = channel_id

    def notify_report_complete(
        self,
        notion_database_id: str | None,
        cleanup_results: list[dict[str, Any]] | None,
        candidates_processed: int,
        repo: str | None = None,
    ) -> None:
        """Send a single summary message when the report completes.

        Args:
            notion_database_id: Notion DB ID (or ``None`` if not used).
            cleanup_results: Phase 3 results with ``pr_url`` and
                ``candidate_ids``.
            candidates_processed: Total candidates in the report.
            repo: Optional repo name for the header.

        Raises:
            SlackNotifyError: If the webhook call fails.
        """
        # Collect all opened PR URLs
        pr_urls: list[dict[str, str]] = []
        if cleanup_results:
            for pr_info in cleanup_results:
                pr_url = pr_info.get("pr_url", "")
                if not pr_url:
                    continue
                candidate_id = pr_info.get("candidate_id", "unknown")
                pr_urls.append({
                    "candidate_id": candidate_id,
                    "pr_url": pr_url,
                })

        payload = _build_summary_message(
            notion_database_id=notion_database_id,
            pr_urls=pr_urls,
            candidates_processed=candidates_processed,
            repo=repo,
        )
        self._send(payload)
        print(
            f"[slack] Sent report summary "
            f"({candidates_processed} candidates, {len(pr_urls)} PRs)"
        )

    def upload_files(
        self,
        file_paths: list[str],
        comment: str = "",
    ) -> None:
        """Upload one or more files to the configured Slack channel.

        Requires ``bot_token`` and ``channel_id`` to be set.  If either
        is missing the call is silently skipped (the webhook-only flow
        remains fully functional).

        Args:
            file_paths: Absolute paths to the JSON files to upload.
            comment: Optional initial comment posted with each file.

        Raises:
            SlackNotifyError: If the Slack API returns an error.
        """
        if not self._bot_token or not self._channel_id:
            print("[slack] Bot token or channel ID not configured; skipping file uploads.")
            return

        for fpath in file_paths:
            if not os.path.isfile(fpath):
                print(f"[slack] File not found, skipping: {fpath}")
                continue

            filename = os.path.basename(fpath)
            with open(fpath, "rb") as fh:
                file_content = fh.read()

            # Use Slack files.upload API (v1 — widely supported)
            boundary = "----SlackFileUploadBoundary"
            body_parts: list[bytes] = []

            # channel field
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(b"Content-Disposition: form-data; name=\"channels\"\r\n\r\n")
            body_parts.append(f"{self._channel_id}\r\n".encode())

            # filename field
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(b"Content-Disposition: form-data; name=\"filename\"\r\n\r\n")
            body_parts.append(f"{filename}\r\n".encode())

            # title field
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(b"Content-Disposition: form-data; name=\"title\"\r\n\r\n")
            body_parts.append(f"{filename}\r\n".encode())

            # initial_comment field
            if comment:
                body_parts.append(f"--{boundary}\r\n".encode())
                body_parts.append(b"Content-Disposition: form-data; name=\"initial_comment\"\r\n\r\n")
                body_parts.append(f"{comment}\r\n".encode())

            # filetype
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(b"Content-Disposition: form-data; name=\"filetype\"\r\n\r\n")
            body_parts.append(b"json\r\n")

            # file content
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(
                f"Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n".encode()
            )
            body_parts.append(b"Content-Type: application/json\r\n\r\n")
            body_parts.append(file_content)
            body_parts.append(b"\r\n")

            # closing boundary
            body_parts.append(f"--{boundary}--\r\n".encode())

            data = b"".join(body_parts)

            req = urllib.request.Request(
                "https://slack.com/api/files.upload",
                data=data,
                headers={
                    "Authorization": f"Bearer {self._bot_token}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    result = json.loads(resp.read().decode())
                    if not result.get("ok"):
                        err = result.get("error", "unknown")
                        raise SlackNotifyError(200, f"files.upload failed: {err}")
                print(f"[slack] Uploaded {filename} to channel {self._channel_id}")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode() if exc.fp else ""
                raise SlackNotifyError(exc.code, str(exc.reason), body) from exc
            except urllib.error.URLError as exc:
                raise SlackNotifyError(0, str(exc.reason)) from exc

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
