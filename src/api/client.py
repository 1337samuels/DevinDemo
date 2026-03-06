"""Devin API v3 client wrapper for creating and polling sessions."""

from __future__ import annotations

import time
from typing import Any

import requests

DEVIN_API_BASE_URL = "https://api.devin.ai"

# Terminal statuses — the session will not change after reaching one of these.
_TERMINAL_STATUSES = {"exit", "error", "suspended"}


class DevinAPIError(Exception):
    """Raised when a Devin API call fails."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class DevinAPIClient:
    """Thin wrapper around the Devin v3 REST API.

    Args:
        api_key: Service user API key (starts with ``cog_``).
        org_id: Organization ID (starts with ``org-``).
    """

    def __init__(self, api_key: str, org_id: str) -> None:
        self._api_key = api_key
        self._org_id = org_id
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{DEVIN_API_BASE_URL}/v3/organizations/{self._org_id}{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        _retries: int = 3,
        _backoff: float = 2.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send an HTTP request with automatic retry on transient 5xx errors.

        Args:
            _retries: Max number of retry attempts for 5xx responses.
            _backoff: Base delay in seconds (doubles each retry).
        """
        last_exc: DevinAPIError | None = None
        for attempt in range(_retries + 1):
            resp = self._session.request(method, self._url(path), **kwargs)
            if resp.ok:
                return resp.json()

            detail = resp.text
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass

            # Retry on transient server errors (502, 503, 504)
            if resp.status_code in {502, 503, 504} and attempt < _retries:
                wait = _backoff * (2**attempt)
                print(
                    f"[api] {method} {path} returned {resp.status_code}, "
                    f"retrying in {wait:.0f}s ({attempt + 1}/{_retries}) …"
                )
                time.sleep(wait)
                last_exc = DevinAPIError(resp.status_code, str(detail))
                continue

            raise DevinAPIError(resp.status_code, str(detail))

        # Should not reach here, but satisfy the type checker.
        assert last_exc is not None  # noqa: S101
        raise last_exc

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(
        self,
        prompt: str,
        *,
        repos: list[str] | None = None,
        structured_output_schema: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        title: str | None = None,
        max_acu_limit: int | None = None,
    ) -> dict[str, Any]:
        """Create a new Devin session.

        Returns the full ``SessionResponse`` dict from the API.
        """
        body: dict[str, Any] = {"prompt": prompt}
        if repos is not None:
            body["repos"] = repos
        if structured_output_schema is not None:
            body["structured_output_schema"] = structured_output_schema
        if tags is not None:
            body["tags"] = tags
        if title is not None:
            body["title"] = title
        if max_acu_limit is not None:
            body["max_acu_limit"] = max_acu_limit
        return self._request("POST", "/sessions", json=body)

    def get_session(self, session_id: str) -> dict[str, Any]:
        """Retrieve the current state of a session.

        Tries the dedicated GET endpoint first.  If the service user
        lacks ``ViewOrgSessions`` permission (403), it falls back to
        the LIST endpoint which only requires ``ManageOrgSessions``.
        """
        try:
            return self._request("GET", f"/sessions/{session_id}")
        except DevinAPIError as exc:
            if exc.status_code != 403:
                raise
        # Fallback: search for the session in the paginated list.
        return self._find_session_via_list(session_id)

    def _find_session_via_list(self, session_id: str) -> dict[str, Any]:
        """Locate a session by iterating the LIST endpoint."""
        cursor: str | None = None
        while True:
            params: dict[str, str | int] = {"first": 200}
            if cursor is not None:
                params["after"] = cursor
            data = self._request("GET", "/sessions", params=params)
            for item in data.get("items", []):
                if item.get("session_id") == session_id:
                    return item
            if not data.get("has_next_page"):
                break
            cursor = data.get("end_cursor")
        raise DevinAPIError(404, f"Session {session_id} not found via list endpoint.")

    def send_message(self, session_id: str, message: str) -> dict[str, Any]:
        """Send a follow-up message to a running session."""
        return self._request(
            "POST",
            f"/sessions/{session_id}/messages",
            json={"message": message},
        )

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def poll_session(
        self,
        session_id: str,
        *,
        interval: int = 15,
        timeout: int = 600,
        on_update: Any | None = None,
        on_waiting_for_user: Any | None = None,
    ) -> dict[str, Any]:
        """Poll a session until it reaches a terminal status.

        Args:
            session_id: The session to poll.
            interval: Seconds between polls (default 15).
            timeout: Maximum total seconds to wait (default 600).
            on_update: Optional callback ``(session_dict) -> None``
                       called after each poll.
            on_waiting_for_user: Optional callback
                ``(client, session_dict) -> bool`` called when the session
                enters ``waiting_for_user`` **without** structured output.
                The callback can send a follow-up message via
                ``client.send_message()`` and should return ``True`` to
                keep polling or ``False`` to return immediately.
                If not provided, waiting-for-user with structured output
                returns the session; without structured output it keeps
                polling until timeout.

        Returns:
            The final session dict once a terminal status is reached.

        Raises:
            TimeoutError: If *timeout* seconds elapse before completion.
        """
        deadline = time.monotonic() + timeout
        while True:
            session = self.get_session(session_id)
            status = session.get("status", "")
            status_detail = session.get("status_detail", "")

            if on_update is not None:
                on_update(session)

            if status in _TERMINAL_STATUSES:
                return session

            # Also treat "running" + "finished" as terminal
            if status == "running" and status_detail == "finished":
                return session

            # The session is waiting for a follow-up message.  In the
            # single-session multi-prompt flow this means Devin finished
            # the current prompt.  If a callback is provided, let it
            # react (e.g. send a nudge) before we decide whether to
            # return or keep polling.
            if status == "running" and status_detail == "waiting_for_user":
                if on_waiting_for_user is not None:
                    keep_polling = on_waiting_for_user(self, session)
                    if keep_polling:
                        # Callback asked us to keep polling (e.g. it
                        # sent a nudge message and wants to wait for
                        # the session to resume).
                        if time.monotonic() < deadline:
                            time.sleep(interval)
                            continue
                # Default: return immediately so the caller can act.
                return session

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Session {session_id} did not finish within {timeout}s "
                    f"(last status: {status}/{status_detail})"
                )

            time.sleep(interval)
