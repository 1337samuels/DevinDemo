"""List Devin sessions and manage them (sleep/archive) using the Devin API (v3)."""

import argparse
import json
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEVIN_API_BASE_URL = "https://api.devin.ai"


def _api_request(url: str, api_key: str, method: str = "GET") -> dict:
    """Make an authenticated API request and return parsed JSON."""
    request = Request(url, method=method)
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
    return _api_request(url, api_key)


def sleep_session(api_key: str, org_id: str, session_id: str) -> dict:
    """Put a Devin session to sleep (archive it).

    Uses the v3beta1 archive endpoint which suspends a running session
    and preserves it for future reference.

    Args:
        api_key: Devin service user API key (starts with cog_).
        org_id: Your Devin organization ID (starts with org-).
        session_id: The session ID to put to sleep.

    Returns:
        Parsed JSON response with the updated session details.
    """
    url = (
        f"{DEVIN_API_BASE_URL}/v3beta1/organizations/{org_id}"
        f"/sessions/{session_id}/archive"
    )
    return _api_request(url, api_key, method="POST")


def _format_timestamp(ts: int | str | None) -> str:
    """Convert a unix timestamp (seconds) or ISO string to a readable format."""
    if ts is None:
        return "N/A"
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    return str(ts)


def _print_session(session: dict) -> None:
    """Pretty-print a single session's details."""
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
            label = (
                f"    PR: {pr_url} ({pr_state})" if pr_state else f"    PR: {pr_url}"
            )
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


def cmd_list(args: argparse.Namespace) -> None:
    """List all sessions."""
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
        _print_session(session)


def cmd_sleep(args: argparse.Namespace) -> None:
    """Put a session to sleep."""
    print(f"Putting session {args.session_id} to sleep ...")
    result = sleep_session(args.api_key, args.org_id, args.session_id)
    print("Session archived/sleeping.\n")
    _print_session(result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List and manage Devin sessions (v3 API).",
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

    subparsers = parser.add_subparsers(dest="action", required=False)

    # ---- list (default) ----
    list_p = subparsers.add_parser("list", help="List all sessions.")
    list_p.add_argument(
        "--first",
        type=int,
        default=100,
        help="Max number of sessions to return per page (default: 100, max: 200).",
    )
    list_p.add_argument(
        "--all-pages",
        action="store_true",
        help="Automatically fetch all pages of results.",
    )
    list_p.set_defaults(func=cmd_list)

    # ---- sleep ----
    sleep_p = subparsers.add_parser(
        "sleep", help="Put a session to sleep (archive it)."
    )
    sleep_p.add_argument(
        "session_id",
        help="The session ID to put to sleep.",
    )
    sleep_p.set_defaults(func=cmd_sleep)

    args = parser.parse_args()

    # Default to 'list' when no subcommand given
    if args.action is None:
        args.first = 100
        args.all_pages = False
        cmd_list(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
