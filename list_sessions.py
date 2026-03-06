"""List all Devin sessions for your organization using the Devin API (v3)."""

import argparse
import json
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEVIN_API_BASE_URL = "https://api.devin.ai"


def _api_get(url: str, api_key: str) -> dict:
    """Make an authenticated GET request and return parsed JSON."""
    request = Request(url, method="GET")
    request.add_header("Authorization", f"Bearer {api_key}")
    request.add_header("Accept", "application/json")

    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        print(f"HTTP error {exc.code}: {exc.reason}", file=sys.stderr)
        try:
            body = exc.read().decode()
            print(body, file=sys.stderr)
        except Exception:
            pass
        sys.exit(1)
    except URLError as exc:
        print(f"Request failed: {exc.reason}", file=sys.stderr)
        sys.exit(1)


def list_sessions(
    api_key: str,
    org_id: str,
    first: int = 100,
    after: str | None = None,
) -> dict:
    """Fetch sessions from the Devin v3 API.

    Args:
        api_key: Devin service user API key (starts with cog_).
        org_id: Your Devin organization ID (starts with org-).
        first: Maximum number of sessions to return per page (default 100, max 200).
        after: Cursor for fetching the next page of results.

    Returns:
        Parsed JSON response containing the list of sessions.
    """
    params: dict[str, str | int] = {"first": first}
    if after is not None:
        params["after"] = after

    url = f"{DEVIN_API_BASE_URL}/v3/organizations/{org_id}/sessions?{urlencode(params)}"
    return _api_get(url, api_key)


def _format_timestamp(ts: int | str | None) -> str:
    """Convert a unix timestamp (seconds) or ISO string to a readable format."""
    if ts is None:
        return "N/A"
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    return str(ts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List all Devin sessions for your organization (v3 API)."
    )
    parser.add_argument(
        "api_key",
        help="Your Devin service user API key (starts with cog_).",
    )
    parser.add_argument(
        "--org-id",
        required=True,
        help="Your Devin organization ID (starts with org-). Find it in Settings > General.",
    )
    parser.add_argument(
        "--first",
        type=int,
        default=100,
        help="Max number of sessions to return per page (default: 100, max: 200).",
    )
    parser.add_argument(
        "--all-pages",
        action="store_true",
        help="Automatically fetch all pages of results.",
    )
    args = parser.parse_args()

    all_sessions: list[dict] = []
    after: str | None = None
    page = 0

    while True:
        page += 1
        data = list_sessions(args.api_key, args.org_id, first=args.first, after=after)
        items = data.get("items", [])
        all_sessions.extend(items)

        total = data.get("total")
        has_next = data.get("has_next_page", False)

        if page == 1 and total is not None:
            print(f"Total sessions: {total}")

        if not args.all_pages or not has_next:
            break

        after = data.get("end_cursor")
        if after is None:
            break

    print(f"Fetched {len(all_sessions)} session(s) ({page} page(s)):\n")

    for session in all_sessions:
        session_id = session.get("session_id", "N/A")
        title = session.get("title") or "(untitled)"
        status = session.get("status", "unknown")
        created = _format_timestamp(session.get("created_at"))
        updated = _format_timestamp(session.get("updated_at"))
        url = session.get("url", "")

        pr_lines: list[str] = []
        for pr in session.get("pull_requests", []):
            pr_url = pr.get("pr_url", "")
            pr_state = pr.get("pr_state", "")
            if pr_url:
                label = f"    PR: {pr_url} ({pr_state})" if pr_state else f"    PR: {pr_url}"
                pr_lines.append(label)

        print(f"  [{status}] {title}")
        print(f"    ID:      {session_id}")
        print(f"    Created: {created}")
        print(f"    Updated: {updated}")
        if url:
            print(f"    URL:     {url}")
        for pr_line in pr_lines:
            print(pr_line)
        print()


if __name__ == "__main__":
    main()
