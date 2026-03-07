"""Tests for src.api.client — DevinAPIClient."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import responses

from src.api.client import DEVIN_API_BASE_URL, DevinAPIClient, DevinAPIError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client() -> DevinAPIClient:
    return DevinAPIClient(
        api_key="cog_test",
        org_id="org-test",
        v1_api_key="apk_test",
    )


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------

class TestURLConstruction:
    def test_v3_url(self, client: DevinAPIClient) -> None:
        url = client._url("/sessions")
        assert url == f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions"

    def test_v1_url(self, client: DevinAPIClient) -> None:
        url = client._url("/session/ses_123", api_version="v1")
        assert url == f"{DEVIN_API_BASE_URL}/v1/session/ses_123"


# ---------------------------------------------------------------------------
# _request — happy path & retry logic
# ---------------------------------------------------------------------------

class TestRequest:
    @responses.activate
    def test_successful_get(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_1",
            json={"session_id": "ses_1", "status": "running"},
            status=200,
        )
        result = client._request("GET", "/sessions/ses_1")
        assert result["session_id"] == "ses_1"

    @responses.activate
    def test_successful_post(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.POST,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions",
            json={"session_id": "ses_new"},
            status=200,
        )
        result = client._request("POST", "/sessions", json={"prompt": "hi"})
        assert result["session_id"] == "ses_new"

    @responses.activate
    def test_4xx_raises_immediately(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_x",
            json={"detail": "Not found"},
            status=404,
        )
        with pytest.raises(DevinAPIError) as exc_info:
            client._request("GET", "/sessions/ses_x")
        assert exc_info.value.status_code == 404

    @responses.activate
    def test_502_retries_then_succeeds(self, client: DevinAPIClient) -> None:
        """502 should be retried; if a later attempt succeeds, return it."""
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_r",
            json={"detail": "Bad Gateway"},
            status=502,
        )
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_r",
            json={"session_id": "ses_r", "status": "running"},
            status=200,
        )
        with patch("time.sleep"):  # skip actual sleep
            result = client._request("GET", "/sessions/ses_r", _backoff=0.01)
        assert result["session_id"] == "ses_r"
        assert len(responses.calls) == 2

    @responses.activate
    def test_502_retries_exhausted(self, client: DevinAPIClient) -> None:
        for _ in range(4):  # 1 initial + 3 retries
            responses.add(
                responses.GET,
                f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_f",
                json={"detail": "Bad Gateway"},
                status=502,
            )
        with patch("time.sleep"):
            with pytest.raises(DevinAPIError) as exc_info:
                client._request("GET", "/sessions/ses_f", _backoff=0.01)
        assert exc_info.value.status_code == 502
        assert len(responses.calls) == 4  # 1 + 3 retries

    @responses.activate
    def test_v1_uses_v1_api_key(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v1/session/ses_v1",
            json={"session_id": "ses_v1", "messages": []},
            status=200,
        )
        client._request("GET", "/session/ses_v1", _api_version="v1")
        assert "Bearer apk_test" in responses.calls[0].request.headers["Authorization"]


# ---------------------------------------------------------------------------
# High-level session methods
# ---------------------------------------------------------------------------

class TestCreateSession:
    @responses.activate
    def test_minimal(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.POST,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions",
            json={"session_id": "ses_new", "url": "https://devin.ai/ses_new"},
            status=200,
        )
        result = client.create_session("Hello")
        assert result["session_id"] == "ses_new"
        body = responses.calls[0].request.body
        assert b'"prompt"' in body

    @responses.activate
    def test_all_optional_fields(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.POST,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions",
            json={"session_id": "ses_full"},
            status=200,
        )
        client.create_session(
            "Go",
            repos=["owner/repo"],
            tags=["test"],
            title="Test",
            max_acu_limit=5,
            structured_output_schema={"type": "object"},
        )
        import json
        body = json.loads(responses.calls[0].request.body)
        assert body["repos"] == ["owner/repo"]
        assert body["tags"] == ["test"]
        assert body["title"] == "Test"
        assert body["max_acu_limit"] == 5
        assert body["structured_output_schema"] == {"type": "object"}


class TestGetSession:
    @responses.activate
    def test_direct_get(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_g",
            json={"session_id": "ses_g", "status": "running"},
            status=200,
        )
        result = client.get_session("ses_g")
        assert result["status"] == "running"

    @responses.activate
    def test_fallback_on_403(self, client: DevinAPIClient) -> None:
        """If GET returns 403, fall back to LIST endpoint."""
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_fb",
            json={"detail": "Unauthorized"},
            status=403,
        )
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions",
            json={
                "items": [{"session_id": "ses_fb", "status": "running"}],
                "has_next_page": False,
            },
            status=200,
        )
        result = client.get_session("ses_fb")
        assert result["session_id"] == "ses_fb"

    @responses.activate
    def test_fallback_not_found(self, client: DevinAPIClient) -> None:
        """If GET returns 403 and LIST doesn't contain the session, raise 404."""
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_nf",
            json={"detail": "Unauthorized"},
            status=403,
        )
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions",
            json={"items": [], "has_next_page": False},
            status=200,
        )
        with pytest.raises(DevinAPIError) as exc_info:
            client.get_session("ses_nf")
        assert exc_info.value.status_code == 404


class TestGetSessionV1:
    @responses.activate
    def test_v1_get(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v1/session/ses_v1",
            json={"session_id": "ses_v1", "messages": [{"type": "devin_message"}]},
            status=200,
        )
        result = client.get_session_v1("ses_v1")
        assert "messages" in result


class TestSendMessage:
    @responses.activate
    def test_send_message(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.POST,
            f"{DEVIN_API_BASE_URL}/v1/sessions/ses_sm/message",
            json={"ok": True},
            status=200,
        )
        result = client.send_message("ses_sm", "hello")
        assert result["ok"] is True
        import json
        body = json.loads(responses.calls[0].request.body)
        assert body["message"] == "hello"


class TestArchiveSession:
    @responses.activate
    def test_archive_sends_sleep(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.POST,
            f"{DEVIN_API_BASE_URL}/v1/sessions/ses_arc/message",
            json={"ok": True},
            status=200,
        )
        result = client.archive_session("ses_arc")
        assert result["ok"] is True
        import json
        body = json.loads(responses.calls[0].request.body)
        assert body["message"] == "sleep"

    @responses.activate
    def test_archive_swallows_error(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.POST,
            f"{DEVIN_API_BASE_URL}/v1/sessions/ses_err/message",
            json={"detail": "Unauthorized"},
            status=403,
        )
        result = client.archive_session("ses_err")
        assert result == {}


# ---------------------------------------------------------------------------
# poll_session
# ---------------------------------------------------------------------------

class TestPollSession:
    @responses.activate
    def test_returns_on_terminal_status(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_p",
            json={"session_id": "ses_p", "status": "exit"},
            status=200,
        )
        result = client.poll_session("ses_p", timeout=10)
        assert result["status"] == "exit"

    @responses.activate
    def test_returns_on_finished(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_f",
            json={"session_id": "ses_f", "status": "running", "status_detail": "finished"},
            status=200,
        )
        result = client.poll_session("ses_f", timeout=10)
        assert result["status_detail"] == "finished"

    @responses.activate
    def test_returns_on_waiting_for_user_with_structured_output(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_w",
            json={
                "session_id": "ses_w",
                "status": "running",
                "status_detail": "waiting_for_user",
                "structured_output": {"files": ["a.py"]},
            },
            status=200,
        )
        result = client.poll_session("ses_w", timeout=10)
        assert result["structured_output"]["files"] == ["a.py"]

    @responses.activate
    def test_timeout_raises(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_t",
            json={"session_id": "ses_t", "status": "running", "status_detail": "working"},
            status=200,
        )
        with patch("time.sleep"):
            with pytest.raises(TimeoutError):
                client.poll_session("ses_t", timeout=0)

    @responses.activate
    def test_on_update_callback(self, client: DevinAPIClient) -> None:
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_cb",
            json={"session_id": "ses_cb", "status": "exit"},
            status=200,
        )
        updates: list[dict] = []
        client.poll_session("ses_cb", timeout=10, on_update=lambda s: updates.append(s))
        assert len(updates) == 1
        assert updates[0]["status"] == "exit"

    @responses.activate
    def test_expect_running_first_skips_initial_waiting(self, client: DevinAPIClient) -> None:
        """When expect_running_first=True, initial waiting_for_user is skipped."""
        # First call: still waiting (stale state)
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_er",
            json={"session_id": "ses_er", "status": "running", "status_detail": "waiting_for_user"},
            status=200,
        )
        # Second call: working
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_er",
            json={"session_id": "ses_er", "status": "running", "status_detail": "working"},
            status=200,
        )
        # Third call: done
        responses.add(
            responses.GET,
            f"{DEVIN_API_BASE_URL}/v3/organizations/org-test/sessions/ses_er",
            json={"session_id": "ses_er", "status": "running", "status_detail": "waiting_for_user"},
            status=200,
        )
        with patch("time.sleep"):
            result = client.poll_session("ses_er", timeout=60, expect_running_first=True)
        # Should have polled 3 times: skip first waiting, see working (saw_running=True), then return on next waiting
        assert len(responses.calls) == 3
        assert result["status_detail"] == "waiting_for_user"
