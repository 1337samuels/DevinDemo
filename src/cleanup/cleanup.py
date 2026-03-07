"""Part 3 --- Generate cleanup PRs to remove verified dead code.

Uses a **single Devin session** with sequential ``send_message()`` prompts:

1. **Setup** --- create a session with a neutral prompt and wait for it
   to become ready (identical to the Phase 2 pattern).
2. **Per-finding prompts** --- for each HIGH-confidence finding,
   ``send_message()`` instructs Devin to create a PR that removes the
   dead code.  The prompt includes the file, line, code snippet,
   suggested PR title/description from Phase 2, and the full validation
   summary so Devin can write a meaningful PR description.
3. **Parse PR URLs** --- after each prompt completes, extract the PR URL
   from Devin's response (structured output or text parsing).
4. **Sleep** --- send the session to sleep when all findings are processed.

``send_message()`` uses the **v1** API endpoint which works with ``cog_``
service-user keys without requiring ``ManageOrgSessions``.

Output
------
A list of cleanup result dicts, each containing:

- ``candidate_id``: The finding ID from Phase 2.
- ``candidate_ids``: List with the single candidate ID (for Phase 4
  compatibility).
- ``pr_url``: The URL of the opened PR (empty string if failed).
- ``session_id``: The Devin session ID.
- ``status``: ``"pr_opened"`` or ``"failed"``.
- ``error``: Error message if status is ``"failed"``.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from src.api.client import DevinAPIClient


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_CLEANUP_PROMPT = """\
You are a code cleanup assistant. Your task is to create a pull request \
that removes a piece of dead code from the repository.

**IMPORTANT**: Create exactly ONE pull request for this finding. Do NOT \
merge the PR --- only open it.

## Finding Details

- **Candidate ID**: {candidate_id}
- **Category**: {category}
- **File**: {file_path}
- **Line**: {line}
- **Code snippet**:
```
{code_snippet}
```

## Validation Summary

{validation_summary}

## Suggested PR

- **Title**: {suggested_pr_title}
- **Description**: {suggested_pr_description}

## Instructions

1. Examine the code at `{file_path}` around line {line}.
2. Identify all code related to this finding that should be removed:
   - The flagged code itself
   - Any imports that become unused after removal
   - Any related test code that tests only the removed code
   - Any configuration or constants that are only used by the removed code
3. Make the minimal set of changes needed to cleanly remove the dead code \
   without breaking anything.
4. Create a pull request with:
   - A clear title (use the suggested title as a starting point)
   - A description explaining what was removed and why, referencing the \
     validation evidence
   - Make sure the code compiles/passes basic syntax checks after removal
5. {merge_instruction}
6. Report back the PR URL.

If you cannot safely remove the code (e.g., it would break other \
functionality), explain why and report an empty PR URL.
"""

_NUDGE_MESSAGE = (
    "Please continue with creating the cleanup PR. If you have already "
    "created it, please report the PR URL. If you encountered issues, "
    "explain what went wrong."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_elapsed(seconds: float) -> str:
    """Format elapsed seconds as ``Xm YYs`` or ``Ys``."""
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def _extract_json_block(text: str) -> dict[str, Any] | None:
    """Extract the first JSON code-fence block from *text*.

    Returns the parsed dict, or ``None`` if no valid JSON block is found.
    """
    pattern = r"```(?:json)?\s*\n(\{.*?\})\s*\n```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Fallback: try to find a raw JSON object
    brace_start = text.find("{")
    if brace_start != -1:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start : i + 1])
                    except json.JSONDecodeError:
                        pass
                    break
    return None


def _extract_pr_url_from_text(text: str) -> str:
    """Try to extract a GitHub/GitLab PR URL from free text."""
    patterns = [
        r"https?://github\.com/[^\s)>\]]+/pull/\d+",
        r"https?://gitlab\.com/[^\s)>\]]+/merge_requests/\d+",
        r"https?://[^\s)>\]]+/pull/\d+",
    ]
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            return match.group(0)
    return ""


def _all_layers_passed(validation: dict[str, Any]) -> bool:
    """Check if all validation layers passed for this finding.

    Looks at the ``layers`` dict inside validation and returns ``True``
    only if every layer has a truthy ``confirmed`` / boolean value.
    If no layers data is present, returns ``False``.
    """
    layers = validation.get("layers", {})
    if not layers:
        return False
    return all(
        bool(layer_data) if not isinstance(layer_data, dict)
        else layer_data.get("confirmed", False)
        for layer_data in layers.values()
    )


def _extract_high_confidence_findings(
    findings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract all HIGH-confidence findings from the Phase 2 output.

    Returns a list of finding dicts, each enriched with ``category``.
    """
    results: list[dict[str, Any]] = []

    for category in ("feature_flags", "dead_code", "tech_debt"):
        for item in findings.get(category, []):
            validation = item.get("validation", {})
            confidence = validation.get("confidence", "")
            if confidence == "HIGH":
                enriched = {**item, "category": category}
                results.append(enriched)

    return results


# ---------------------------------------------------------------------------
# Progress tracker
# ---------------------------------------------------------------------------


class CleanupProgressTracker:
    """Print live progress updates during cleanup PR generation."""

    def __init__(
        self,
        finding_idx: int,
        total_findings: int,
        candidate_id: str,
        client: DevinAPIClient | None = None,
        session_id: str = "",
    ) -> None:
        self._finding_idx = finding_idx
        self._total = total_findings
        self._candidate_id = candidate_id
        self._client = client
        self._session_id = session_id
        self._start = time.monotonic()
        self._poll_count = 0
        self._last_printed_msg = ""

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        return f"{m}m{s:02d}s" if m else f"{s}s"

    @staticmethod
    def _last_devin_message(session: dict[str, Any]) -> str:
        """Return the text of the most recent ``devin_message``."""
        messages = session.get("messages") or []
        for msg in reversed(messages):
            if msg.get("type") == "devin_message":
                return msg.get("message", "")
        return ""

    def __call__(self, session: dict[str, Any]) -> None:
        self._poll_count += 1
        elapsed = time.monotonic() - self._start
        status = session.get("status", "")
        detail = session.get("status_detail", "")
        print(
            f"  [{self._fmt_elapsed(elapsed)}] {status}"
            f" ({detail})"
            f"  | Finding {self._finding_idx}/{self._total}"
            f"  | {self._candidate_id}"
        )

        # Every 2nd poll, fetch V1 messages and print the
        # latest Devin message so the user sees progress.
        if self._poll_count % 2 == 0 and self._client and self._session_id:
            try:
                v1 = self._client.get_session_v1(self._session_id)
                latest_msg = self._last_devin_message(v1)
                if latest_msg and latest_msg != self._last_printed_msg:
                    self._last_printed_msg = latest_msg
                    snippet = latest_msg[:200].replace("\n", " ").strip()
                    if len(latest_msg) > 200:
                        snippet += " ..."
                    print(f"    |-- Devin: {snippet}")
            except Exception:
                pass  # Non-critical


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class CleanupPRGenerator:
    """Generate cleanup PRs via a single Devin session.

    Uses the same single-session pattern as Phase 2:

    1. Create session with neutral prompt, wait until ready.
    2. For each HIGH-confidence finding, ``send_message()`` with a
       detailed cleanup prompt.
    3. Parse the PR URL from Devin's response.
    4. Send the session to sleep when done.
    """

    def __init__(self, client: DevinAPIClient) -> None:
        self._client = client

    def generate_prs(
        self,
        findings: dict[str, Any],
        *,
        poll_interval: int = 15,
        poll_timeout: int = 900,
        max_acu_limit: int | None = None,
        progress_tracker_factory: (
            Callable[..., Callable[[dict[str, Any]], None]] | None
        ) = None,
        auto_merge: bool = False,
    ) -> list[dict[str, Any]]:
        """Create cleanup PRs for all HIGH-confidence findings.

        Creates a **single** Devin session, then sends each finding as a
        follow-up message via ``send_message()``.

        Args:
            findings: The validated findings from Phase 2 (with
                ``validation`` data merged into each candidate).
            poll_interval: Seconds between status polls.
            poll_timeout: Max seconds to wait per prompt round.
            max_acu_limit: Optional ACU cap for the session.
            progress_tracker_factory: Optional callable
                ``(finding_idx, total_findings, candidate_id) -> callback``
                that returns a per-finding progress callback.

        Returns:
            A list of cleanup result dicts, each with ``candidate_id``,
            ``candidate_ids``, ``pr_url``, ``session_id``, ``status``.
        """
        high_findings = _extract_high_confidence_findings(findings)

        if not high_findings:
            print("[cleanup] No HIGH-confidence findings to clean up.")
            return []

        total = len(high_findings)
        print(f"[cleanup] {total} HIGH-confidence finding(s) to clean up.")

        # Extract repo from findings
        repo = findings.get("repo", findings.get("meta", {}).get("repo", ""))

        # ---- Create session with neutral setup prompt ----
        setup_prompt = (
            f"You are a code cleanup assistant for the repository "
            f"**{repo}**. You will be asked to create pull requests that "
            f"remove dead code from the codebase. Wait for my "
            f"instructions before doing anything."
        )

        print(f"[cleanup] Creating session for {repo} ...")
        session = self._client.create_session(
            prompt=setup_prompt,
            repos=[repo] if repo else None,
            tags=["dead-code-cleanup", "automated"],
            title=f"Dead-code cleanup: {repo}",
            max_acu_limit=max_acu_limit,
        )
        session_id = session["session_id"]
        session_url = session.get("url", "")
        print(f"[cleanup] Session: {session_id}")
        print(f"[cleanup] URL: {session_url}")

        # Wait for session to be ready
        print("[cleanup] Waiting for session to initialise ...")
        phase0_start = time.monotonic()

        def _setup_status(sess: dict[str, Any]) -> None:
            elapsed = time.monotonic() - phase0_start
            status = sess.get("status", "")
            detail = sess.get("status_detail", "")
            print(
                f"  [{_fmt_elapsed(elapsed)}] {status}"
                f" ({detail})  | Initialising session ..."
            )

        self._client.poll_session(
            session_id,
            interval=poll_interval,
            timeout=poll_timeout,
            on_update=_setup_status,
        )

        # ---- Process each HIGH-confidence finding ----
        cleanup_results: list[dict[str, Any]] = []

        for idx, finding in enumerate(high_findings, 1):
            candidate_id = finding.get("id", f"unknown_{idx}")
            category = finding.get("category", "unknown")
            file_path = finding.get("file", "unknown")
            line = finding.get("line", 0)
            code_snippet = finding.get("code_snippet", "")
            validation = finding.get("validation", {})

            print(
                f"\n[cleanup] --- Finding {idx}/{total}: "
                f"{candidate_id} ({category}) "
                f"at {file_path}:{line} ---"
            )

            # Determine merge instruction based on auto_merge flag
            # and whether the finding passed all validation layers
            all_layers_passed = _all_layers_passed(validation)
            if auto_merge and all_layers_passed:
                merge_instruction = (
                    "After opening the PR, ALSO MERGE IT. This finding "
                    "passed all validation layers with HIGH confidence."
                )
            else:
                merge_instruction = "Do NOT merge the PR. Only open it."

            # Build the cleanup prompt
            prompt = _CLEANUP_PROMPT.format(
                candidate_id=candidate_id,
                category=category,
                file_path=file_path,
                line=line,
                code_snippet=code_snippet,
                validation_summary=validation.get(
                    "summary", "No summary available."
                ),
                suggested_pr_title=validation.get(
                    "suggested_pr_title",
                    f"Remove dead code: {candidate_id}",
                ),
                suggested_pr_description=validation.get(
                    "suggested_pr_description",
                    f"Remove {category} at {file_path}:{line} "
                    f"identified as dead code with HIGH confidence.",
                ),
                merge_instruction=merge_instruction,
            )

            # Send cleanup prompt
            self._client.send_message(session_id, prompt)

            # Build progress tracker
            tracker: Callable[[dict[str, Any]], None] | None = None
            if progress_tracker_factory is not None:
                tracker = progress_tracker_factory(idx, total, candidate_id)
            else:
                tracker = CleanupProgressTracker(
                    idx, total, candidate_id,
                    client=self._client, session_id=session_id,
                )

            # Wait for Devin to finish the PR
            def _on_waiting(
                client: DevinAPIClient,
                sess: dict[str, Any],
                _idx: int = idx,
                _total: int = total,
                _sid: str = session_id,
            ) -> bool:
                """Handle waiting_for_user by reading messages first.

                When the session enters ``waiting_for_user``, Devin may
                have already finished (e.g. opened a PR and reported the
                URL).  We read the conversation to decide whether to
                stop polling (Devin is done) or nudge (Devin stalled).
                """
                print(
                    f"[cleanup]   Session waiting for input --- "
                    f"reading messages for finding {_idx}/{_total} ..."
                )

                # Fetch the full conversation via the v1 API
                v1_session = client.get_session_v1(_sid)
                messages = v1_session.get("messages") or []

                # Walk backwards through Devin's messages to see if
                # work is already done (PR URL present) or if Devin
                # reported that it cannot create the PR.
                for msg in reversed(messages):
                    if msg.get("type") != "devin_message":
                        continue
                    text = msg.get("message", "")

                    # Check for a PR URL in the message
                    parsed = _extract_json_block(text)
                    if parsed and parsed.get("pr_url"):
                        print(
                            f"[cleanup]   Found PR URL in message "
                            f"--- done for finding {_idx}/{_total}."
                        )
                        return False  # Stop polling

                    url = _extract_pr_url_from_text(text)
                    if url:
                        print(
                            f"[cleanup]   Found PR URL in message "
                            f"--- done for finding {_idx}/{_total}."
                        )
                        return False  # Stop polling

                    # If Devin explicitly says it can't create the PR,
                    # stop polling so we don't loop forever.
                    _DONE_PHRASES = [
                        "cannot safely remove",
                        "unable to create",
                        "could not create",
                        "pr url: ",
                        "empty pr url",
                    ]
                    lower = text.lower()
                    if any(phrase in lower for phrase in _DONE_PHRASES):
                        print(
                            f"[cleanup]   Devin indicated completion "
                            f"--- done for finding {_idx}/{_total}."
                        )
                        return False  # Stop polling

                    # Only inspect the most recent Devin message
                    break

                # No completion signal found --- nudge Devin
                print(
                    f"[cleanup]   No completion signal found --- "
                    f"sending nudge for finding {_idx}/{_total} ..."
                )
                client.send_message(_sid, _NUDGE_MESSAGE)
                return True  # Keep polling

            self._client.poll_session(
                session_id,
                interval=poll_interval,
                timeout=poll_timeout,
                on_update=tracker,
                expect_running_first=True,
                on_waiting_for_user=_on_waiting,
            )

            # Extract PR URL from response
            pr_url = self._extract_pr_url(session_id)

            if pr_url:
                print(f"[cleanup]   PR opened: {pr_url}")
                cleanup_results.append({
                    "candidate_id": candidate_id,
                    "candidate_ids": [candidate_id],
                    "pr_url": pr_url,
                    "session_id": session_id,
                    "status": "pr_opened",
                    "error": "",
                    "category": category,
                    "file": file_path,
                    "line": line,
                })
            else:
                error_msg = (
                    "Could not extract PR URL from session response."
                )
                print(f"[cleanup]   WARNING: {error_msg}")
                cleanup_results.append({
                    "candidate_id": candidate_id,
                    "candidate_ids": [candidate_id],
                    "pr_url": "",
                    "session_id": session_id,
                    "status": "failed",
                    "error": error_msg,
                    "category": category,
                    "file": file_path,
                    "line": line,
                })

        # ---- Record ACU usage before sleeping ----
        try:
            final_session = self._client.get_session(session_id)
            from src.tracking.acu_tracker import ACUTracker, extract_acu_from_session
            acu_used = extract_acu_from_session(final_session)
            if acu_used > 0:
                acu_tracker = ACUTracker()
                acu_tracker.record(session_id, "cleanup", acu_used, repo=repo)
                print(f"[cleanup] ACU used: {acu_used}")
            else:
                print("[cleanup] ACU used: 0 (not reported by API)")
        except Exception as exc:
            print(f"[cleanup] Warning: could not record ACU usage: {exc}")

        # ---- Send session to sleep ----
        print("\n[cleanup] Sending session to sleep ...")
        self._client.archive_session(session_id)

        # ---- Print summary ----
        self._print_summary(cleanup_results)

        return cleanup_results

    def _extract_pr_url(self, session_id: str) -> str:
        """Extract a PR URL from the session's latest response.

        Tries structured output first, then parses JSON blocks from
        Devin's last message, then falls back to regex URL extraction.
        """
        v1_session = self._client.get_session_v1(session_id)

        # Try structured output first
        structured = v1_session.get("structured_output")
        if structured and structured.get("pr_url"):
            return str(structured["pr_url"])

        # Parse from Devin's last messages
        messages = v1_session.get("messages") or []
        for msg in reversed(messages):
            if msg.get("type") == "devin_message":
                text = msg.get("message", "")
                # Try JSON block
                parsed = _extract_json_block(text)
                if parsed and parsed.get("pr_url"):
                    return str(parsed["pr_url"])
                # Try URL regex
                url = _extract_pr_url_from_text(text)
                if url:
                    return url

        return ""

    @staticmethod
    def _print_summary(results: list[dict[str, Any]]) -> None:
        """Print a human-readable summary of cleanup results."""
        opened = sum(1 for r in results if r["status"] == "pr_opened")
        failed = sum(1 for r in results if r["status"] == "failed")

        print("\n" + "=" * 60)
        print("CLEANUP SUMMARY")
        print("=" * 60)
        print(f"  Total findings processed: {len(results)}")
        print(f"  PRs opened:              {opened}")
        print(f"  Failed:                  {failed}")

        if opened > 0:
            print("\n  Opened PRs:")
            for r in results:
                if r["status"] == "pr_opened":
                    print(f"    - {r['candidate_id']}: {r['pr_url']}")

        if failed > 0:
            print("\n  Failed findings:")
            for r in results:
                if r["status"] == "failed":
                    print(
                        f"    - {r['candidate_id']}: {r['error']}"
                    )

        print("=" * 60)
