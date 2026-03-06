"""Part 1 — Identify feature flags, dead code, and tech debt.

Uses a two-phase approach:

1. **Discovery session** — a short Devin session that lists every ``.py``
   file in the target repository and returns the file list.
2. **Batch scan sessions** — the file list is split into batches and each
   batch is scanned in its own Devin session.  This gives the caller
   precise progress tracking (batch N/M, X/Y files, Z%).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

from src.api.client import DevinAPIClient

# ---------------------------------------------------------------------------
# Phase 1 — Discovery: list all .py files in the repo
# ---------------------------------------------------------------------------

DISCOVERY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo": {
            "type": "string",
            "description": "The GitHub repository (owner/repo).",
        },
        "files": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of every .py file path relative to the repo root.",
        },
        "total_files": {
            "type": "integer",
            "description": "Total number of .py files found.",
        },
    },
    "required": ["repo", "files", "total_files"],
}

_DISCOVERY_PROMPT = """You are a file-listing utility.  For the repository **{repo}**, list every ``.py`` file.  Use ``find . -name '*.py'`` (or equivalent) in the repo root.

Return:
- ``repo``: "{repo}"
- ``files``: an array of relative file paths (e.g. ``["src/main.py", "tests/test_foo.py"]``)
- ``total_files``: the length of that array

Do NOT scan or analyse the file contents — just list them.
"""

# ---------------------------------------------------------------------------
# Phase 2 — Batch scan: analyse a subset of files
# ---------------------------------------------------------------------------

_FINDING_ITEM_PROPERTIES: dict[str, Any] = {
    "file": {"type": "string", "description": "Relative file path."},
    "line": {"type": "integer", "description": "Line number (1-indexed)."},
    "code_snippet": {
        "type": "string",
        "description": "The relevant code snippet (<=5 lines).",
    },
    "reasoning": {
        "type": "string",
        "description": "Why this was flagged.",
    },
}

BATCH_SCAN_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo": {
            "type": "string",
            "description": "The GitHub repository that was scanned (owner/repo).",
        },
        "feature_flags": {
            "type": "array",
            "description": "Feature flag occurrences found in these files.",
            "items": {
                "type": "object",
                "properties": {
                    **_FINDING_ITEM_PROPERTIES,
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
                    "flag_name": {
                        "type": "string",
                        "description": "Extracted flag / variable name.",
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
            "description": "Dead or unreachable code found in these files.",
            "items": {
                "type": "object",
                "properties": {
                    **_FINDING_ITEM_PROPERTIES,
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
            "description": "Tech debt markers found in these files.",
            "items": {
                "type": "object",
                "properties": {
                    **_FINDING_ITEM_PROPERTIES,
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
            "description": "Summary for this batch.",
            "properties": {
                "files_scanned": {"type": "integer"},
                "total_feature_flags": {"type": "integer"},
                "total_dead_code": {"type": "integer"},
                "total_tech_debt": {"type": "integer"},
                "high_priority_items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Items that should be addressed first.",
                },
            },
            "required": [
                "files_scanned",
                "total_feature_flags",
                "total_dead_code",
                "total_tech_debt",
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

_BATCH_SCAN_PROMPT = """You are a code-quality analyser.  Scan **only** the following Python files in the repository **{repo}**:

{file_list}

For each file, identify **all** of the following:

## 1. Feature Flags
Look for general feature-flag patterns — this project does NOT use a specific flag management system (e.g. LaunchDarkly).  Instead, look for:
- Environment variable checks that gate behaviour   (``os.environ.get("FEATURE_...")``, ``os.getenv(...)``).
- Boolean configuration variables used in ``if`` / ``else`` branches   (e.g. ``ENABLE_NEW_UI = True``).
- Functions whose sole purpose is to check whether a feature is enabled   (e.g. ``def is_feature_enabled(name): ...``).
- Constants or settings that act as on/off switches.

## 2. Dead Code
- Unreachable branches (e.g. ``if False:``, ``if 0:``, always-true guards).
- Unused functions or classes (defined but never called/imported elsewhere).
- Unused imports.
- Large blocks of commented-out code (>=3 consecutive commented lines that   look like former source code, NOT documentation comments).

## 3. Tech Debt
- ``TODO``, ``FIXME``, ``HACK``, ``XXX`` comments.
- Use of deprecated stdlib or third-party APIs.
- Compatibility shims for old Python versions (e.g. ``sys.version`` checks,   ``six`` library usage).

## Output
Return the structured output with all findings from these files.  Do NOT scan files outside the list above.  Do NOT truncate results.
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
    total_files: int = 0,
) -> dict[str, Any]:
    """Wrap raw Devin output with metadata and per-finding IDs.

    The enriched output is the canonical input format for Part 2
    (validation).  Each finding has:
    - ``id``: deterministic hash so Part 2 can reference individual items.
    - ``verification_status``: starts as ``"unverified"``; Part 2 will
      update to ``"verified"``, ``"false_positive"``, or ``"needs_review"``.
    """
    summary = raw.get("summary", {})
    if total_files > 0:
        summary["total_files"] = total_files
    return {
        "meta": {
            "scanner_version": "1.1.0",
            "scan_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_id": session_id,
            "repo": repo,
        },
        "repo": raw.get("repo", repo),
        "feature_flags": _enrich_findings(raw.get("feature_flags", []), "feature_flag"),
        "dead_code": _enrich_findings(raw.get("dead_code", []), "dead_code"),
        "tech_debt": _enrich_findings(raw.get("tech_debt", []), "tech_debt"),
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk_list(items: list[str], size: int) -> list[list[str]]:
    """Split *items* into sub-lists of at most *size* elements."""
    return [items[i : i + size] for i in range(0, len(items), size)]


# ---------------------------------------------------------------------------
# Progress callback type
# ---------------------------------------------------------------------------

# ``on_progress(done_files, total_files, batch_idx, total_batches,
#               batch_session_or_None)``
OnProgressCallback = Callable[[int, int, int, int, dict[str, Any] | None], None]


class FeatureFlagScanner:
    """Scan a repository for feature flags, dead code, and tech debt.

    Uses a two-phase approach:

    1. **Discovery** — a quick Devin session that lists all ``.py`` files.
    2. **Batch scan** — the file list is split into batches, each scanned
       by its own Devin session.  The caller gets precise progress
       tracking between batches.
    """

    DEFAULT_BATCH_SIZE = 10

    def __init__(self, client: DevinAPIClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def scan(
        self,
        repo: str,
        *,
        batch_size: int | None = None,
        poll_interval: int = 15,
        poll_timeout: int = 900,
        max_acu_limit: int | None = None,
        on_progress: OnProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Run the full two-phase scan and return enriched results.

        Args:
            repo: GitHub repository in ``owner/repo`` format.
            batch_size: Max files per batch session (default 10).
            poll_interval: Seconds between status polls (default 15).
            poll_timeout: Max seconds to wait **per session** (default 900).
            max_acu_limit: Optional ACU cap **per session**.
            on_progress: Optional callback invoked between batches:
                ``(done_files, total_files, batch_idx, total_batches,
                batch_session_or_None)``.

        Returns:
            Enriched result dict ready for Part 2 consumption.
        """
        if batch_size is None:
            batch_size = self.DEFAULT_BATCH_SIZE

        # ---- Phase 1: Discovery ----
        file_list = self._discover_files(
            repo, poll_interval, poll_timeout, max_acu_limit
        )
        total_files = len(file_list)
        print(f"[scanner] Discovery complete: {total_files} .py files found.")

        if total_files == 0:
            print("[scanner] No .py files found — nothing to scan.")
            return _enrich_results(
                {
                    "repo": repo,
                    "feature_flags": [],
                    "dead_code": [],
                    "tech_debt": [],
                    "summary": {
                        "files_scanned": 0,
                        "total_feature_flags": 0,
                        "total_dead_code": 0,
                        "total_tech_debt": 0,
                        "high_priority_items": [],
                    },
                },
                session_id="none",
                repo=repo,
                total_files=0,
            )

        # ---- Phase 2: Batch scanning ----
        batches = _chunk_list(file_list, batch_size)
        total_batches = len(batches)
        print(
            f"[scanner] Scanning in {total_batches} batch(es) "
            f"of up to {batch_size} files each."
        )

        # Notify caller of initial state
        if on_progress is not None:
            on_progress(0, total_files, 0, total_batches, None)

        all_flags: list[dict[str, Any]] = []
        all_dead: list[dict[str, Any]] = []
        all_debt: list[dict[str, Any]] = []
        all_high_pri: list[str] = []
        session_ids: list[str] = []
        files_done = 0

        for batch_idx, batch_files in enumerate(batches, start=1):
            print(
                f"\n[scanner] --- Batch {batch_idx}/{total_batches} "
                f"({len(batch_files)} files) ---"
            )
            batch_result, sid = self._scan_batch(
                repo, batch_files, poll_interval, poll_timeout, max_acu_limit
            )
            session_ids.append(sid)

            # Accumulate findings
            all_flags.extend(batch_result.get("feature_flags", []))
            all_dead.extend(batch_result.get("dead_code", []))
            all_debt.extend(batch_result.get("tech_debt", []))
            batch_summary = batch_result.get("summary", {})
            all_high_pri.extend(batch_summary.get("high_priority_items", []))

            files_done += len(batch_files)

            if on_progress is not None:
                on_progress(files_done, total_files, batch_idx, total_batches, None)

        # ---- Assemble final output ----
        combined: dict[str, Any] = {
            "repo": repo,
            "feature_flags": all_flags,
            "dead_code": all_dead,
            "tech_debt": all_debt,
            "summary": {
                "total_files": total_files,
                "files_scanned": files_done,
                "total_feature_flags": len(all_flags),
                "total_dead_code": len(all_dead),
                "total_tech_debt": len(all_debt),
                "high_priority_items": all_high_pri,
            },
        }

        enriched = _enrich_results(
            combined,
            session_id=",".join(session_ids),
            repo=repo,
            total_files=total_files,
        )

        print("\n[scanner] All batches complete. Results:")
        print(json.dumps(enriched, indent=2))
        return enriched

    # ------------------------------------------------------------------
    # Phase 1 — Discovery
    # ------------------------------------------------------------------

    def _discover_files(
        self,
        repo: str,
        poll_interval: int,
        poll_timeout: int,
        max_acu_limit: int | None,
    ) -> list[str]:
        """Create a Devin session that lists all .py files in the repo."""
        prompt = _DISCOVERY_PROMPT.format(repo=repo)

        print(f"[scanner] Phase 1: discovering .py files in {repo} ...")
        session = self._client.create_session(
            prompt=prompt,
            repos=[repo],
            structured_output_schema=DISCOVERY_OUTPUT_SCHEMA,
            tags=["feature-flag-scan", "discovery", "automated"],
            title=f"File discovery: {repo}",
            max_acu_limit=max_acu_limit,
        )
        session_id = session["session_id"]
        print(f"[scanner] Discovery session: {session_id}")
        print(f"[scanner] URL: {session.get('url', '')}")

        final = self._client.poll_session(
            session_id, interval=poll_interval, timeout=poll_timeout
        )

        structured = final.get("structured_output")
        if structured is None:
            raise RuntimeError(
                f"Discovery session {session_id} produced no structured output. "
                f"Status: {final.get('status')}/{final.get('status_detail')}"
            )

        files: list[str] = structured.get("files", [])
        return files

    # ------------------------------------------------------------------
    # Phase 2 — Batch scan
    # ------------------------------------------------------------------

    def _scan_batch(
        self,
        repo: str,
        files: list[str],
        poll_interval: int,
        poll_timeout: int,
        max_acu_limit: int | None,
    ) -> tuple[dict[str, Any], str]:
        """Scan a single batch of files and return (result, session_id)."""
        file_list_str = "\n".join(f"- ``{f}``" for f in files)
        prompt = _BATCH_SCAN_PROMPT.format(repo=repo, file_list=file_list_str)

        session = self._client.create_session(
            prompt=prompt,
            repos=[repo],
            structured_output_schema=BATCH_SCAN_OUTPUT_SCHEMA,
            tags=["feature-flag-scan", "batch", "automated"],
            title=f"Batch scan: {repo} ({len(files)} files)",
            max_acu_limit=max_acu_limit,
        )
        session_id = session["session_id"]
        print(f"[scanner]   Session: {session_id}")
        print(f"[scanner]   URL: {session.get('url', '')}")

        final = self._client.poll_session(
            session_id, interval=poll_interval, timeout=poll_timeout
        )

        structured = final.get("structured_output")
        if structured is None:
            raise RuntimeError(
                f"Batch session {session_id} produced no structured output. "
                f"Status: {final.get('status')}/{final.get('status_detail')}"
            )

        return structured, session_id
