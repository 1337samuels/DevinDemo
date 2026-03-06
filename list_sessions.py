"""List all Devin sessions for your organization using the Devin API."""

import argparse
import json
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

DEVIN_API_BASE_URL = "https://api.devin.ai"


def list_sessions(api_key: str, limit: int = 100, offset: int = 0) -> dict:
    """Fetch all sessions from the Devin API.

    Args:
        api_key: Devin API key (personal or service key).
        limit: Maximum number of sessions to return (default 100).
        offset: Number of sessions to skip for pagination (default 0).

    Returns:
        Parsed JSON response containing the list of sessions.
    """
    url = f"{DEVIN_API_BASE_URL}/v1/sessions?limit={limit}&offset={offset}"
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List all Devin sessions for your organization."
    )
    parser.add_argument(
        "api_key",
        help="Your Devin API key (personal: apk_user_* or service: apk_*).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max number of sessions to return (default: 100).",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Number of sessions to skip for pagination (default: 0).",
    )
    args = parser.parse_args()

    data = list_sessions(args.api_key, limit=args.limit, offset=args.offset)
    sessions = data.get("sessions", [])

    print(f"Found {len(sessions)} session(s):\n")
    for session in sessions:
        session_id = session.get("session_id", "N/A")
        title = session.get("title") or "(untitled)"
        status = session.get("status_enum") or session.get("status", "unknown")
        created = session.get("created_at", "N/A")
        updated = session.get("updated_at", "N/A")
        pr_url = ""
        if session.get("pull_request"):
            pr_url = f"  PR: {session['pull_request'].get('url', '')}"

        print(f"  [{status}] {title}")
        print(f"    ID:      {session_id}")
        print(f"    Created: {created}")
        print(f"    Updated: {updated}")
        if pr_url:
            print(pr_url)
        print()


if __name__ == "__main__":
    main()
