"""Part 1 — Identify feature flags, dead code, and tech debt.

Uses a single Devin session with multiple prompts:

1. **Discovery prompt** — the initial session prompt asks Devin to list
   every ``.py`` file in the repository.
2. **Batch scan prompts** — follow-up messages in the same session instruct
   Devin to scan batches of files.  This gives the caller precise progress
   tracking (batch N/M, X/Y files, Z%) without the overhead of creating
   multiple sessions.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Callable

from src.api.client import DevinAPIClient

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_DISCOVERY_PROMPT = """You are a code-quality scanner.  For the repository **{repo}**, start by listing every ``.py`` file.  Use ``find . -name '*.py'`` (or equivalent) in the repo root.

Once you have the list, respond with **only** a JSON block like this (no other text before or after it):

```json
{{"files": ["path/to/file1.py", "path/to/file2.py"], "total_files": 2}}
```

Do NOT scan or analyse the file contents yet — just list them.
"""

_BATCH_SCAN_PROMPT = """Now scan **only** the following Python files:

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

## Output format
Respond with **only** a JSON block (no other text):

```json
{{
  "feature_flags": [
    {{"file": "path.py", "line": 10, "pattern_type": "env_var_check",
      "flag_name": "FEATURE_X", "code_snippet": "...", "reasoning": "..."}}
  ],
  "dead_code": [
    {{"file": "path.py", "line": 20, "category": "unused_import",
      "code_snippet": "...", "reasoning": "..."}}
  ],
  "tech_debt": [
    {{"file": "path.py", "line": 30, "category": "todo_comment",
      "code_snippet": "...", "reasoning": "..."}}
  ],
  "summary": {{
    "files_scanned": {file_count},
    "total_feature_flags": 0,
    "total_dead_code": 0,
    "total_tech_debt": 0,
    "high_priority_items": []
  }}
}}
```

Do NOT scan files outside the list above.  Do NOT truncate results.
"""

# ---------------------------------------------------------------------------
# Structured output schema (set at session creation for final output)
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

SCAN_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo": {
            "type": "string",
            "description": "The GitHub repository that was scanned (owner/repo).",
        },
        "feature_flags": {
            "type": "array",
            "description": "All feature flag occurrences found.",
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
            "description": "Dead or unreachable code found.",
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
            "description": "Tech debt markers found.",
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
            "description": "Scan summary.",
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
            "scanner_version": "1.2.0",
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


def _extract_json_block(text: str) -> dict[str, Any] | None:
    """Extract the first JSON code-fence block from *text*.

    Returns the parsed dict, or ``None`` if no valid JSON block is found.
    """
    # Try ```json ... ``` first
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
        # Find matching closing brace
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


# ---------------------------------------------------------------------------
# Progress callback type
# ---------------------------------------------------------------------------

# ``on_progress(done_files, total_files, batch_idx, total_batches,
#               batch_session_or_None)``
OnProgressCallback = Callable[[int, int, int, int, dict[str, Any] | None], None]


class FeatureFlagScanner:
    """Scan a repository for feature flags, dead code, and tech debt.

    Uses a **single Devin session** with multiple prompts:

    1. **Discovery** — the initial prompt asks Devin to list all ``.py``
       files in the repo.
    2. **Batch scans** — follow-up messages in the same session instruct
       Devin to scan batches of files.  The caller gets precise progress
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
        """Run the full scan and return enriched results.

        Creates a single Devin session, sends a discovery prompt first,
        then sends batch scan prompts as follow-up messages.

        Args:
            repo: GitHub repository in ``owner/repo`` format.
            batch_size: Max files per batch prompt (default 10).
            poll_interval: Seconds between status polls (default 15).
            poll_timeout: Max seconds to wait per prompt (default 900).
            max_acu_limit: Optional ACU cap for the session.
            on_progress: Optional callback invoked between batches:
                ``(done_files, total_files, batch_idx, total_batches,
                session_or_None)``.

        Returns:
            Enriched result dict ready for Part 2 consumption.
        """
        if batch_size is None:
            batch_size = self.DEFAULT_BATCH_SIZE

        # ---- Create session with discovery prompt ----
        discovery_prompt = _DISCOVERY_PROMPT.format(repo=repo)

        print(f"[scanner] Creating session for {repo} ...")
        session = self._client.create_session(
            prompt=discovery_prompt,
            repos=[repo],
            structured_output_schema=SCAN_OUTPUT_SCHEMA,
            tags=["feature-flag-scan", "automated"],
            title=f"Code scan: {repo}",
            max_acu_limit=max_acu_limit,
        )
        session_id = session["session_id"]
        print(f"[scanner] Session: {session_id}")
        print(f"[scanner] URL: {session.get('url', '')}")

        # ---- Phase 1: Wait for discovery response ----
        print("[scanner] Phase 1: discovering .py files ...")
        final = self._client.poll_session(
            session_id, interval=poll_interval, timeout=poll_timeout
        )

        file_list = self._parse_discovery_response(final)
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
                session_id=session_id,
                repo=repo,
                total_files=0,
            )

        # ---- Phase 2: Send batch scan prompts ----
        batches = _chunk_list(file_list, batch_size)
        total_batches = len(batches)
        print(
            f"[scanner] Scanning in {total_batches} batch(es) "
            f"of up to {batch_size} files each."
        )

        if on_progress is not None:
            on_progress(0, total_files, 0, total_batches, None)

        all_flags: list[dict[str, Any]] = []
        all_dead: list[dict[str, Any]] = []
        all_debt: list[dict[str, Any]] = []
        all_high_pri: list[str] = []
        files_done = 0

        for batch_idx, batch_files in enumerate(batches, start=1):
            print(
                f"\n[scanner] --- Batch {batch_idx}/{total_batches} "
                f"({len(batch_files)} files) ---"
            )

            # Send batch scan prompt as a follow-up message
            file_list_str = "\n".join(f"- ``{f}``" for f in batch_files)
            batch_prompt = _BATCH_SCAN_PROMPT.format(
                repo=repo, file_list=file_list_str, file_count=len(batch_files)
            )
            self._client.send_message(session_id, batch_prompt)

            # Wait for Devin to finish processing this batch
            batch_final = self._client.poll_session(
                session_id, interval=poll_interval, timeout=poll_timeout
            )

            # Try to extract batch results from structured output
            batch_result = self._parse_batch_response(batch_final)

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
            session_id=session_id,
            repo=repo,
            total_files=total_files,
        )

        print("\n[scanner] All batches complete. Results:")
        print(json.dumps(enriched, indent=2))
        return enriched

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_discovery_response(self, session: dict[str, Any]) -> list[str]:
        """Extract the file list from the discovery session response.

        Tries structured_output first, then falls back to parsing JSON
        from the session text.
        """
        # Try structured output
        structured = session.get("structured_output")
        if structured is not None:
            files = structured.get("files")
            if isinstance(files, list) and len(files) > 0:
                return files

        # Structured output may not have files (schema is for scan results).
        # Look for JSON in the last messages or status text.
        # The discovery prompt asks Devin to output a JSON block.
        last_text = session.get("last_message", "") or ""
        parsed = _extract_json_block(last_text)
        if parsed and "files" in parsed:
            return parsed["files"]

        raise RuntimeError(
            f"Could not extract file list from discovery response. "
            f"Status: {session.get('status')}/{session.get('status_detail')}"
        )

    def _parse_batch_response(self, session: dict[str, Any]) -> dict[str, Any]:
        """Extract batch scan results from the session response.

        Tries structured_output first (which accumulates across the
        session), then falls back to parsing JSON from messages.
        """
        structured = session.get("structured_output")
        if structured is not None:
            # The structured output should contain the scan results
            if structured.get("feature_flags") is not None:
                return structured

        # Fallback: empty result (Devin may update structured_output
        # only at the very end of the session)
        return {
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
        }
