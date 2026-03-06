"""Part 1 — Identify feature flags, dead code, and tech debt.

Creates a Devin session that scans a target Python repository and returns
structured findings via the ``structured_output`` mechanism.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from src.api.client import DevinAPIClient

# ---------------------------------------------------------------------------
# Structured output schema (JSON Schema Draft 7)
# ---------------------------------------------------------------------------
# Devin will populate this structure as it analyses the repo.

SCAN_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo": {
            "type": "string",
            "description": "The GitHub repository that was scanned (owner/repo).",
        },
        "feature_flags": {
            "type": "array",
            "description": "Feature flag occurrences found in the codebase.",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Relative file path."},
                    "line": {
                        "type": "integer",
                        "description": "Line number (1-indexed).",
                    },
                    "pattern_type": {
                        "type": "string",
                        "enum": [
                            "env_var_check",
                            "boolean_config_flag",
                            "feature_gate_function",
                            "constant_flag",
                            "other",
                        ],
                        "description": "Category of the feature flag pattern.",
                    },
                    "code_snippet": {
                        "type": "string",
                        "description": "The relevant code snippet (≤5 lines).",
                    },
                    "flag_name": {
                        "type": "string",
                        "description": "Extracted flag / variable name.",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Why this is considered a feature flag.",
                    },
                },
                "required": [
                    "file",
                    "line",
                    "pattern_type",
                    "code_snippet",
                    "flag_name",
                    "reasoning",
                ],
            },
        },
        "dead_code": {
            "type": "array",
            "description": "Dead or unreachable code found in the codebase.",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "unreachable_branch",
                            "unused_function",
                            "unused_import",
                            "unused_variable",
                            "commented_out_code",
                            "other",
                        ],
                    },
                    "code_snippet": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
                "required": [
                    "file",
                    "line",
                    "category",
                    "code_snippet",
                    "reasoning",
                ],
            },
        },
        "tech_debt": {
            "type": "array",
            "description": "Tech debt markers found in the codebase.",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "todo_comment",
                            "fixme_comment",
                            "hack_comment",
                            "deprecated_usage",
                            "compatibility_shim",
                            "other",
                        ],
                    },
                    "code_snippet": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
                "required": [
                    "file",
                    "line",
                    "category",
                    "code_snippet",
                    "reasoning",
                ],
            },
        },
        "summary": {
            "type": "object",
            "description": "High-level summary of the scan.",
            "properties": {
                "total_feature_flags": {"type": "integer"},
                "total_dead_code": {"type": "integer"},
                "total_tech_debt": {"type": "integer"},
                "files_scanned": {"type": "integer"},
                "high_priority_items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Items that should be addressed first.",
                },
            },
            "required": [
                "total_feature_flags",
                "total_dead_code",
                "total_tech_debt",
                "files_scanned",
                "high_priority_items",
            ],
        },
    },
    "required": [
        "repo",
        "feature_flags",
        "dead_code",
        "tech_debt",
        "summary",
    ],
}

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_SCAN_PROMPT = """\
You are a code-quality analyser.  Scan the Python files in the repository \
**{repo}** and identify **all** of the following:

## 1. Feature Flags
Look for general feature-flag patterns — this project does NOT use a specific \
flag management system (e.g. LaunchDarkly).  Instead, look for:
- Environment variable checks that gate behaviour \
  (``os.environ.get("FEATURE_…")``, ``os.getenv(…)``).
- Boolean configuration variables used in ``if`` / ``else`` branches \
  (e.g. ``ENABLE_NEW_UI = True``).
- Functions whose sole purpose is to check whether a feature is enabled \
  (e.g. ``def is_feature_enabled(name): …``).
- Constants or settings that act as on/off switches.

## 2. Dead Code
- Unreachable branches (e.g. ``if False:``, ``if 0:``, always-true guards).
- Unused functions or classes (defined but never called/imported elsewhere).
- Unused imports.
- Large blocks of commented-out code (≥3 consecutive commented lines that \
  look like former source code, NOT documentation comments).

## 3. Tech Debt
- ``TODO``, ``FIXME``, ``HACK``, ``XXX`` comments.
- Use of deprecated stdlib or third-party APIs.
- Compatibility shims for old Python versions (e.g. ``sys.version`` checks, \
  ``six`` library usage).

## Output
Update the **structured output** as you scan.  After you finish, the \
structured output MUST conform to the JSON schema provided and include \
every finding you discovered.  Do NOT truncate results.

Be thorough — scan every ``.py`` file in the repository.
"""


# ---------------------------------------------------------------------------
# Post-processing — enrich results for Part 2 consumption
# ---------------------------------------------------------------------------


def _make_finding_id(category: str, file: str, line: int) -> str:
    """Deterministic short ID for a finding based on its location."""
    raw = f"{category}:{file}:{line}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _enrich_findings(
    findings: list[dict[str, Any]], category: str
) -> list[dict[str, Any]]:
    """Add ``id`` and ``verification_status`` to each finding."""
    enriched: list[dict[str, Any]] = []
    for item in findings:
        finding_id = _make_finding_id(
            category, item.get("file", ""), item.get("line", 0)
        )
        enriched.append(
            {
                "id": finding_id,
                "verification_status": "unverified",
                **item,
            }
        )
    return enriched


def _enrich_results(
    raw: dict[str, Any],
    *,
    session_id: str,
    repo: str,
) -> dict[str, Any]:
    """Wrap raw Devin output with metadata and per-finding IDs.

    The enriched output is the canonical input format for Part 2
    (validation).  Each finding has:
    - ``id``: deterministic hash so Part 2 can reference individual items.
    - ``verification_status``: starts as ``"unverified"``; Part 2 will
      update to ``"verified"``, ``"false_positive"``, or ``"needs_review"``.
    """
    return {
        "meta": {
            "scanner_version": "1.0.0",
            "scan_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_id": session_id,
            "repo": repo,
        },
        "repo": raw.get("repo", repo),
        "feature_flags": _enrich_findings(raw.get("feature_flags", []), "feature_flag"),
        "dead_code": _enrich_findings(raw.get("dead_code", []), "dead_code"),
        "tech_debt": _enrich_findings(raw.get("tech_debt", []), "tech_debt"),
        "summary": raw.get("summary", {}),
    }


class FeatureFlagScanner:
    """Scan a repository for feature flags, dead code, and tech debt.

    Uses the Devin API to create a session that analyses the target repo
    and returns structured findings.
    """

    def __init__(self, client: DevinAPIClient) -> None:
        self._client = client

    def scan(
        self,
        repo: str,
        *,
        poll_interval: int = 15,
        poll_timeout: int = 900,
        max_acu_limit: int | None = None,
        on_status_update: Any | None = None,
    ) -> dict[str, Any]:
        """Launch a scan and wait for the results.

        Args:
            repo: GitHub repository in ``owner/repo`` format.
            poll_interval: Seconds between status polls (default 15).
            poll_timeout: Max seconds to wait for completion (default 900).
            max_acu_limit: Optional ACU cap for the session.
            on_status_update: Optional callback ``(session_dict) -> None``.

        Returns:
            The structured output dict conforming to ``SCAN_OUTPUT_SCHEMA``.

        Raises:
            TimeoutError: If the session does not finish in time.
            RuntimeError: If the session ends without structured output.
        """
        prompt = _SCAN_PROMPT.format(repo=repo)

        print(f"[scanner] Creating Devin session to scan {repo} …")
        session = self._client.create_session(
            prompt=prompt,
            repos=[repo],
            structured_output_schema=SCAN_OUTPUT_SCHEMA,
            tags=["feature-flag-scan", "automated"],
            title=f"Feature-flag scan: {repo}",
            max_acu_limit=max_acu_limit,
        )
        session_id = session["session_id"]
        session_url = session.get("url", "")
        print(f"[scanner] Session created: {session_id}")
        print(f"[scanner] URL: {session_url}")
        print(f"[scanner] Polling every {poll_interval}s (timeout {poll_timeout}s) …")

        final = self._client.poll_session(
            session_id,
            interval=poll_interval,
            timeout=poll_timeout,
            on_update=on_status_update,
        )

        structured = final.get("structured_output")
        if structured is None:
            raise RuntimeError(
                f"Session {session_id} finished but produced no structured output. "
                f"Final status: {final.get('status')}/{final.get('status_detail')}"
            )

        enriched = _enrich_results(structured, session_id=session_id, repo=repo)

        print("[scanner] Scan complete. Results:")
        print(json.dumps(enriched, indent=2))
        return enriched
