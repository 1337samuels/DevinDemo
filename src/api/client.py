"""Devin API client wrapper for creating and polling sessions.

Uses v3 for session creation/polling and v1 for ``send_message()``.
"""

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
    """Thin wrapper around the Devin REST API.

    Uses the **v3** API for session creation, polling, and listing,
    and the **v1** API for ``send_message()`` (which requires a
    separate legacy API key).

    Args:
        api_key: Service user API key (starts with ``cog_``) for v3.
        org_id: Organization ID (starts with ``org-``).
        v1_api_key: Legacy API key (starts with ``apk_``) for v1
            endpoints like ``send_message()``.  Required when using
            the single-session multi-prompt flow.
    """

    def __init__(
        self, api_key: str, org_id: str, *, v1_api_key: str | None = None
    ) -> None:
        self._api_key = api_key
        self._v1_api_key = v1_api_key
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

    def _url(self, path: str, *, api_version: str = "v3") -> str:
        if api_version == "v1":
            # V1 endpoints don't include the /organizations/{org_id} prefix.
            return f"{DEVIN_API_BASE_URL}/v1{path}"
        return f"{DEVIN_API_BASE_URL}/{api_version}/organizations/{self._org_id}{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        _retries: int = 3,
        _backoff: float = 2.0,
        _api_version: str = "v3",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send an HTTP request with automatic retry on transient 5xx errors.

        Args:
            _retries: Max number of retry attempts for 5xx responses.
            _backoff: Base delay in seconds (doubles each retry).
            _api_version: API version prefix (default ``v3``).
        """
        last_exc: DevinAPIError | None = None
        url = self._url(path, api_version=_api_version)

        # V1 endpoints need the legacy API key.
        headers: dict[str, str] | None = None
        if _api_version == "v1" and self._v1_api_key:
            headers = {"Authorization": f"Bearer {self._v1_api_key}"}

        for attempt in range(_retries + 1):
            resp = self._session.request(method, url, headers=headers, **kwargs)
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

    def get_session_v1(self, session_id: str) -> dict[str, Any]:
        """Retrieve session details via the **v1** API.

        Unlike the v3 GET endpoint, the v1 response includes a
        ``messages`` list with the full conversation history.  This is
        essential for extracting Devin's text responses (file lists,
        scan results, etc.) which are not available through v3.
        """
        return self._request(
            "GET",
            f"/session/{session_id}",
            _api_version="v1",
        )

    def send_message(self, session_id: str, message: str) -> dict[str, Any]:
        """Send a follow-up message to a running session.

        Uses the **v1** endpoint (``POST /v1/sessions/{id}/message``)
        which works with ``cog_`` service-user keys without requiring
        the ``ManageOrgSessions`` permission that the v3 endpoint needs.
        """
        return self._request(
            "POST",
            f"/sessions/{session_id}/message",
            _api_version="v1",
            json={"message": message},
        )

    def archive_session(self, session_id: str) -> dict[str, Any]:
        """Archive (put to sleep) a session.

        Uses the ``v3beta1`` archive endpoint to suspend a running
        session so it no longer consumes resources.
        """
        try:
            return self._request(
                "POST",
                f"/sessions/{session_id}/archive",
                _api_version="v3beta1",
            )
        except DevinAPIError as exc:
            # Non-critical — log and continue if archiving fails
            print(f"[api] Warning: could not archive session {session_id}: {exc}")
            return {}

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
        expect_running_first: bool = False,
        on_waiting_for_user: Any | None = None,
    ) -> dict[str, Any]:
        """Poll a session until it reaches a terminal or idle status.

        Uses **adaptive polling**: 2 s while the session is idle or
        initialising (``waiting_for_user``, ``claimed``, ``new``),
        switching to *interval* seconds once Devin is actively
        ``working``.  This avoids wasting time on slow polls while
        the session is transitioning between states.

        Args:
            session_id: The session to poll.
            interval: Seconds between polls while Devin is actively
                working (default 15).  Idle/transitional states always
                poll every 2 s.
            timeout: Maximum total seconds to wait (default 600).
            on_update: Optional callback ``(session_dict) -> None``
                       called after each poll.
            expect_running_first: When ``True``, ignore the initial
                ``waiting_for_user`` status (because a message was just
                sent and the session hasn't transitioned to ``running``
                yet).  Once the session enters ``running`` (or any
                non-``waiting_for_user`` state), normal exit conditions
                apply.  This prevents the poller from returning
                immediately on the *old* ``waiting_for_user`` status
                after ``send_message()``.
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
        saw_running = (
            not expect_running_first
        )  # False means: skip initial waiting_for_user
        while True:
            session = self.get_session(session_id)
            status = session.get("status", "")
            status_detail = session.get("status_detail", "")

            if on_update is not None:
                on_update(session)

            # Track whether we've moved past the initial waiting_for_user
            if not saw_running and not (
                status == "running" and status_detail == "waiting_for_user"
            ):
                saw_running = True

            if status in _TERMINAL_STATUSES:
                return session

            # Also treat "running" + "finished" as terminal
            if status == "running" and status_detail == "finished":
                return session

            # Handle waiting_for_user
            if status == "running" and status_detail == "waiting_for_user":
                # If structured output exists, we have what we need.
                if session.get("structured_output") is not None:
                    return session

                # Only act on waiting_for_user if we've seen a non-waiting
                # state first (to avoid returning on stale status after
                # send_message).
                if saw_running:
                    # If a callback was provided, let it decide what to do.
                    if on_waiting_for_user is not None:
                        keep_polling = on_waiting_for_user(self, session)
                        if not keep_polling:
                            return session
                    else:
                        return session

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Session {session_id} did not finish within {timeout}s "
                    f"(last status: {status}/{status_detail})"
                )

            # Adaptive polling: 2s while the session is idle/initialising
            # (waiting_for_user, claimed, new) so we don't miss the
            # transition to "working".  Use the full interval only once
            # Devin is actively working, to avoid unnecessary API calls.
            _FAST_POLL_DETAILS = {"waiting_for_user", "None", None, ""}
            _FAST_POLL_STATUSES = {"claimed", "new"}
            if status in _FAST_POLL_STATUSES or status_detail in _FAST_POLL_DETAILS:
                time.sleep(2)
            else:
                time.sleep(interval)
