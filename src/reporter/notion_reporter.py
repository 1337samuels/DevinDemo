"""Phase 4, Part 1 --- Publish validated findings to a Notion database.

On the first run (no ``database_id`` supplied) a new database is created
under the given *parent_page_id* with all required columns.  Subsequent
runs upsert rows into the existing database.

The table displays every candidate ordered by confidence and category,
with a checkbox per validation layer and a count of how many layers
support removal.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NOTION_API_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"

# Confidence ordering for sorting (lower index = higher priority).
_CONFIDENCE_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "EXEMPT": 3}

# The 8 validation layers and their "supports removal" key in layer_results.
LAYER_DEFINITIONS: list[tuple[str, str, str]] = [
    ("layer_1_reconfirm", "Layer 1: Re-Confirm", "confirmed"),
    ("layer_2_git_staleness", "Layer 2: Git Staleness", "is_stale"),
    ("layer_3_active_development", "Layer 3: Active Dev", "actively_being_worked_on"),
    ("layer_4_static_reachability", "Layer 4: Reachability", "is_reachable"),
    ("layer_5_issue_archaeology", "Layer 5: Issue Archaeology", "overall_sentiment"),
    ("layer_6_test_coverage", "Layer 6: Test Coverage", "tests_reference_candidate"),
    ("layer_7_runtime_signals", "Layer 7: Runtime Signals", "referenced_in_infra"),
    ("layer_8_external_consumers", "Layer 8: External Consumers", "is_exported"),
]

# Database property definitions used when creating a new database.
_DB_PROPERTIES: dict[str, dict[str, Any]] = {
    "Candidate ID": {"title": {}},
    "Category": {
        "select": {
            "options": [
                {"name": "feature_flag", "color": "blue"},
                {"name": "dead_code", "color": "red"},
                {"name": "tech_debt", "color": "yellow"},
            ]
        }
    },
    "File": {"rich_text": {}},
    "Line": {"number": {"format": "number"}},
    "Confidence": {
        "select": {
            "options": [
                {"name": "HIGH", "color": "red"},
                {"name": "MEDIUM", "color": "orange"},
                {"name": "LOW", "color": "yellow"},
                {"name": "EXEMPT", "color": "gray"},
            ]
        }
    },
    "Validation Count": {"number": {"format": "number"}},
    # PR fields (empty for now, filled when Phase 3 data is available)
    "PR Opened": {"checkbox": {}},
    "PR URL": {"url": {}},
    # Remaining columns
    "Summary": {"rich_text": {}},
    # 8 layer checkboxes
    "Layer 1: Re-Confirm": {"checkbox": {}},
    "Layer 2: Git Staleness": {"checkbox": {}},
    "Layer 3: Active Dev": {"checkbox": {}},
    "Layer 4: Reachability": {"checkbox": {}},
    "Layer 5: Issue Archaeology": {"checkbox": {}},
    "Layer 6: Test Coverage": {"checkbox": {}},
    "Layer 7: Runtime Signals": {"checkbox": {}},
    "Layer 8: External Consumers": {"checkbox": {}},
    "Code Snippet": {"rich_text": {}},
    "Detection Reasoning": {"rich_text": {}},
}


# ---------------------------------------------------------------------------
# Notion API helpers
# ---------------------------------------------------------------------------


class NotionAPIError(Exception):
    """Raised when a Notion API call fails."""

    def __init__(self, status: int, message: str, body: str = "") -> None:
        self.status = status
        self.body = body
        super().__init__(f"Notion API {status}: {message}")


def _notion_request(
    method: str,
    path: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a request to the Notion API and return parsed JSON."""
    url = f"{NOTION_BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        raise NotionAPIError(exc.code, str(exc.reason), body) from exc
    except urllib.error.URLError as exc:
        raise NotionAPIError(0, str(exc.reason)) from exc


# ---------------------------------------------------------------------------
# Layer evaluation logic
# ---------------------------------------------------------------------------


def _layer_supports_removal(
    layer_key: str,
    field_key: str,
    layer_data: dict[str, Any],
) -> bool:
    """Determine whether a single validation layer supports removal.

    The semantics differ per layer — some fields are boolean-positive
    (``confirmed=True`` means "yes, this is real dead code"), while
    others are boolean-negative (``is_reachable=False`` means "supports
    removal").

    Returns ``True`` when the layer evidence supports removing the code.
    Returns ``False`` when the layer data is missing or the key field
    is absent (we never assume support for removal without evidence).
    """
    # If the layer wasn't evaluated at all, don't count it as supporting removal.
    if not layer_data:
        return False

    # If the specific field is absent, we can't make a determination.
    if field_key not in layer_data:
        return False

    value = layer_data[field_key]

    # Layers where True means "supports removal"
    if layer_key in ("layer_1_reconfirm", "layer_2_git_staleness"):
        return bool(value)

    # Layers where False means "supports removal"
    if layer_key in (
        "layer_3_active_development",
        "layer_4_static_reachability",
        "layer_6_test_coverage",
        "layer_7_runtime_signals",
        "layer_8_external_consumers",
    ):
        return not bool(value)

    # Layer 5 uses a string enum
    if layer_key == "layer_5_issue_archaeology":
        return value in ("supports_removal", "no_discussion")

    return False


def _evaluate_layers(
    layer_results: dict[str, Any],
) -> tuple[dict[str, bool], int]:
    """Evaluate all 8 layers and return per-layer booleans + count.

    Returns:
        A tuple of (layer_name -> supports_removal, total_count).
    """
    checks: dict[str, bool] = {}
    count = 0
    for layer_key, layer_name, field_key in LAYER_DEFINITIONS:
        layer_data = layer_results.get(layer_key, {})
        supports = _layer_supports_removal(layer_key, field_key, layer_data)
        checks[layer_name] = supports
        if supports:
            count += 1
    return checks, count


# ---------------------------------------------------------------------------
# Candidate preparation
# ---------------------------------------------------------------------------


def _extract_candidates(
    findings: dict[str, Any],
    cleanup_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Flatten and sort all candidates from the validated findings.

    Each returned dict has the keys needed to build a Notion row.
    """
    # Build a lookup of PR data from cleanup results (Phase 3)
    pr_lookup: dict[str, dict[str, Any]] = {}
    if cleanup_results:
        for pr_info in cleanup_results:
            for cid in pr_info.get("candidate_ids", []):
                pr_lookup[cid] = pr_info

    rows: list[dict[str, Any]] = []

    for category in ("feature_flags", "dead_code", "tech_debt"):
        category_label = {
            "feature_flags": "feature_flag",
            "dead_code": "dead_code",
            "tech_debt": "tech_debt",
        }[category]

        for item in findings.get(category, []):
            candidate_id = item.get("id", "unknown")
            validation = item.get("validation", {})
            layer_results = validation.get("layer_results", {})
            confidence = validation.get("confidence", "LOW")

            layer_checks, validation_count = _evaluate_layers(layer_results)

            # PR info from cleanup results
            pr_info = pr_lookup.get(candidate_id, {})
            pr_opened = bool(pr_info.get("pr_url"))
            pr_url = pr_info.get("pr_url", "")

            rows.append({
                "candidate_id": candidate_id,
                "category": category_label,
                "file": item.get("file", ""),
                "line": item.get("line", 0),
                "confidence": confidence,
                "summary": validation.get("summary", ""),
                "layer_checks": layer_checks,
                "validation_count": validation_count,
                "pr_opened": pr_opened,
                "pr_url": pr_url,
                "code_snippet": item.get("code_snippet", ""),
                "reasoning": item.get("reasoning", ""),
                # Extra data for Slack notifications
                "last_meaningful_edit_date": (
                    layer_results
                    .get("layer_2_git_staleness", {})
                    .get("last_meaningful_edit_date", "N/A")
                ),
                "layer_results_raw": layer_results,
            })

    # Sort: confidence (HIGH first), then category
    rows.sort(
        key=lambda r: (
            _CONFIDENCE_ORDER.get(r["confidence"], 99),
            r["category"],
        )
    )

    return rows


# ---------------------------------------------------------------------------
# Notion database creation
# ---------------------------------------------------------------------------


def _build_db_title(repo: str | None) -> str:
    """Build a descriptive database title with repo name and timestamp.

    Format: ``Dead Code Report \u2014 owner/repo \u2014 2026-03-06 16:30 UTC``
    Falls back to a generic title when *repo* is not available.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if repo:
        return f"Dead Code Report \u2014 {repo} \u2014 {ts}"
    return f"Dead Code Report \u2014 {ts}"


def create_notion_database(
    api_key: str,
    parent_page_id: str,
    title: str | None = None,
    repo: str | None = None,
) -> str:
    """Create a new Notion database under the given parent page.

    If *title* is not provided, one is generated from *repo* and the
    current timestamp so that each run produces a uniquely named DB.

    Returns:
        The database ID of the newly created database.
    """
    if not title:
        title = _build_db_title(repo)
    payload: dict[str, Any] = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": _DB_PROPERTIES,
    }

    result = _notion_request("POST", "/databases", api_key, payload)
    database_id = result["id"]
    print(f"[notion] Created database: {database_id}")
    return database_id


# ---------------------------------------------------------------------------
# Row creation / upsert
# ---------------------------------------------------------------------------


def _truncate_text(text: str, max_length: int = 2000) -> str:
    """Truncate text to fit Notion's rich_text limit."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def _build_row_properties(row: dict[str, Any]) -> dict[str, Any]:
    """Build the Notion page properties dict for a single candidate row."""
    props: dict[str, Any] = {
        "Candidate ID": {
            "title": [{"text": {"content": row["candidate_id"]}}],
        },
        "Category": {"select": {"name": row["category"]}},
        "File": {
            "rich_text": [{"text": {"content": _truncate_text(row["file"])}}],
        },
        "Line": {"number": row["line"]},
        "Confidence": {"select": {"name": row["confidence"]}},
        "Summary": {
            "rich_text": [
                {"text": {"content": _truncate_text(row["summary"])}}
            ],
        },
        "Validation Count": {"number": row["validation_count"]},
        "PR Opened": {"checkbox": row["pr_opened"]},
        "Code Snippet": {
            "rich_text": [
                {"text": {"content": _truncate_text(row["code_snippet"])}}
            ],
        },
        "Detection Reasoning": {
            "rich_text": [
                {"text": {"content": _truncate_text(row["reasoning"])}}
            ],
        },
    }

    # PR URL — only set if we have one (Notion URL property cannot be empty string)
    if row["pr_url"]:
        props["PR URL"] = {"url": row["pr_url"]}
    else:
        props["PR URL"] = {"url": None}

    # Layer checkboxes
    for _layer_key, layer_name, _field_key in LAYER_DEFINITIONS:
        props[layer_name] = {
            "checkbox": row["layer_checks"].get(layer_name, False)
        }

    return props


def _query_existing_pages(
    api_key: str,
    database_id: str,
) -> dict[str, str]:
    """Query all existing pages in the database and return {candidate_id: page_id}.

    This enables upsert behaviour — we update existing rows instead of
    creating duplicates.
    """
    existing: dict[str, str] = {}
    has_more = True
    start_cursor: str | None = None

    while has_more:
        payload: dict[str, Any] = {"page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        result = _notion_request(
            "POST",
            f"/databases/{database_id}/query",
            api_key,
            payload,
        )

        for page in result.get("results", []):
            title_prop = page.get("properties", {}).get("Candidate ID", {})
            title_items = title_prop.get("title", [])
            if title_items:
                cid = title_items[0].get("text", {}).get("content", "")
                if cid:
                    existing[cid] = page["id"]

        has_more = result.get("has_more", False)
        start_cursor = result.get("next_cursor")

    return existing


def _create_page(
    api_key: str,
    database_id: str,
    properties: dict[str, Any],
) -> str:
    """Create a new page (row) in the database."""
    payload: dict[str, Any] = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    result = _notion_request("POST", "/pages", api_key, payload)
    return result["id"]


def _update_page(
    api_key: str,
    page_id: str,
    properties: dict[str, Any],
) -> None:
    """Update an existing page's properties."""
    _notion_request("PATCH", f"/pages/{page_id}", api_key, {"properties": properties})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class NotionReporter:
    """Publish validated findings to a Notion database."""

    def __init__(
        self,
        api_key: str,
        database_id: str | None = None,
        parent_page_id: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._database_id = database_id
        self._parent_page_id = parent_page_id

    @property
    def database_id(self) -> str | None:
        """Return the current database ID (may be set after ``publish``)."""
        return self._database_id

    def publish(
        self,
        findings: dict[str, Any],
        cleanup_results: list[dict[str, Any]] | None = None,
    ) -> str:
        """Publish all candidates to Notion.

        If no database ID was provided at construction time, a new
        database is created under ``parent_page_id``.

        Args:
            findings: The validated findings from Phase 2.
            cleanup_results: Optional Phase 3 cleanup PR results.

        Returns:
            The database ID used (created or existing).

        Raises:
            ValueError: If neither database_id nor parent_page_id is set.
            NotionAPIError: On Notion API failures.
        """
        # Ensure we have a database
        if not self._database_id:
            if not self._parent_page_id:
                raise ValueError(
                    "Either database_id or parent_page_id must be provided."
                )
            repo = findings.get("repo")
            self._database_id = create_notion_database(
                self._api_key, self._parent_page_id, repo=repo
            )

        # Extract and sort candidates
        rows = _extract_candidates(findings, cleanup_results)

        if not rows:
            print("[notion] No candidates to publish.")
            return self._database_id

        # Query existing pages for upsert
        existing_pages = _query_existing_pages(self._api_key, self._database_id)
        print(
            f"[notion] Found {len(existing_pages)} existing row(s) in database."
        )

        created = 0
        updated = 0

        for row in rows:
            properties = _build_row_properties(row)
            page_id = existing_pages.get(row["candidate_id"])

            if page_id:
                _update_page(self._api_key, page_id, properties)
                updated += 1
            else:
                _create_page(self._api_key, self._database_id, properties)
                created += 1

        print(
            f"[notion] Done. Created {created} row(s), "
            f"updated {updated} row(s) in database {self._database_id}."
        )

        return self._database_id
