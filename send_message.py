"""Send a message to an existing Devin session using the v1 API.

This script provides a workaround for Teams accounts that cannot use the v3 API
for sending messages. The v1 API accepts Personal API Keys (apk_user_*) and
Service API Keys (apk_*), which are available on all account tiers.
"""

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEVIN_API_BASE_URL = "https://api.devin.ai"


def _api_post(url: str, api_key: str, payload: dict) -> dict | None:
    """Make an authenticated POST request and return parsed JSON (or None)."""
    data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, method="POST")
    request.add_header("Authorization", f"Bearer {api_key}")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")

    try:
        with urlopen(request) as response:
            body = response.read().decode()
            if not body:
                return None
            return json.loads(body)
    except HTTPError as exc:
        print(f"HTTP error {exc.code}: {exc.reason}", file=sys.stderr)
        try:
            error_body = exc.read().decode()
            print(error_body, file=sys.stderr)
        except Exception:
            pass
        sys.exit(1)
    except URLError as exc:
        print(f"Request failed: {exc.reason}", file=sys.stderr)
        sys.exit(1)


def send_message(api_key: str, session_id: str, message: str) -> dict | None:
    """Send a message to an existing Devin session via the v1 API.

    This endpoint works with Personal API Keys (apk_user_*) and Service API
    Keys (apk_*), making it available to Teams accounts that do not have
    access to the v3 send-message endpoint.

    Args:
        api_key: Devin API key (Personal: apk_user_*, or Service: apk_*).
        session_id: The ID of the session to send the message to.
        message: The message text to send to Devin.

    Returns:
        Parsed JSON response, or None on success with no body. If the session
        is already suspended, returns a dict with a ``detail`` key.
    """
    url = f"{DEVIN_API_BASE_URL}/v1/sessions/{session_id}/message"
    payload = {"message": message}
    return _api_post(url, api_key, payload)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Send a message to an existing Devin session (v1 API). "
            "Works with Teams accounts using Personal or Service API keys."
        ),
    )
    parser.add_argument(
        "api_key",
        help=(
            "Your Devin API key. Accepts Personal keys (apk_user_*) "
            "or Service keys (apk_*)."
        ),
    )
    parser.add_argument(
        "session_id",
        help="The session ID to send the message to (e.g. abc-123-def-456).",
    )
    parser.add_argument(
        "message",
        help="The message text to send to Devin in the session.",
    )
    args = parser.parse_args()

    print(f"Sending message to session {args.session_id} ...")
    result = send_message(args.api_key, args.session_id, args.message)

    if result is None:
        print("Message sent successfully.")
    elif "detail" in result:
        print(f"Server response: {result['detail']}")
    else:
        print(f"Response: {json.dumps(result, indent=2)}")


if __name__ == "__main__":
    main()
