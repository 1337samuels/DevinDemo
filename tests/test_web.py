"""Tests for web.app — Flask API endpoints."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any
from unittest.mock import patch

import pytest

# web.app imports main._load_secrets at module level, so we need to ensure
# the project root is on sys.path before importing.
import sys
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from web.app import (
    _discover_all_repos,
    _discover_result_files,
    _mask_cmd,
    _safe_read_json,
    app,
)


@pytest.fixture()
def flask_client():
    """Create a Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture()
def results_dir(tmp_path):
    """Create a temporary results directory with sample files."""
    results = tmp_path / "results"
    results.mkdir()

    # Create a scan result file
    scan_file = results / "scan_owner_repo_20260306_091500.json"
    scan_data = {
        "repo": "owner/repo",
        "feature_flags": [],
        "dead_code": [],
        "tech_debt": [],
        "summary": {"files_scanned": 3},
    }
    scan_file.write_text(json.dumps(scan_data))

    # Create a validate result file
    validate_file = results / "validate_owner_repo_20260306_100000.json"
    validate_data = {
        "repo": "owner/repo",
        "feature_flags": [
            {
                "id": "f1",
                "file": "src/a.py",
                "line": 10,
                "category": "feature_flag",
                "validation": {
                    "confidence": "HIGH",
                    "summary": "Stale flag",
                    "pr_url": None,
                    "pr_opened": False,
                },
            }
        ],
        "dead_code": [],
        "tech_debt": [],
        "validation_report": {
            "confidence_counts": {"HIGH": 1, "MEDIUM": 0, "LOW": 0, "EXEMPT": 0},
        },
    }
    validate_file.write_text(json.dumps(validate_data))

    # Create a cleanup result file
    cleanup_file = results / "cleanup_owner_repo_20260306_110000.json"
    cleanup_data = [
        {
            "candidate_id": "f1",
            "candidate_ids": ["f1"],
            "pr_url": "https://github.com/owner/repo/pull/1",
            "status": "pr_opened",
            "file": "src/a.py",
        }
    ]
    cleanup_file.write_text(json.dumps(cleanup_data))

    return results


# ---------------------------------------------------------------------------
# _mask_cmd
# ---------------------------------------------------------------------------

class TestMaskCmd:
    def test_masks_api_key(self) -> None:
        cmd = ["python", "main.py", "scan", "--api-key", "cog_secret123", "owner/repo"]
        masked = _mask_cmd(cmd)
        assert "cog_secret123" not in masked
        assert "****" in masked

    def test_masks_v1_api_key(self) -> None:
        cmd = ["python", "main.py", "scan", "--v1-api-key", "apk_secret"]
        masked = _mask_cmd(cmd)
        assert "apk_secret" not in masked

    def test_no_secret_flags(self) -> None:
        cmd = ["python", "main.py", "scan", "owner/repo"]
        masked = _mask_cmd(cmd)
        assert masked == "python main.py scan owner/repo"


# ---------------------------------------------------------------------------
# _safe_read_json
# ---------------------------------------------------------------------------

class TestSafeReadJson:
    def test_reads_valid_json(self, results_dir) -> None:
        with patch("web.app.PROJECT_ROOT", str(results_dir.parent)):
            data = _safe_read_json("results/scan_owner_repo_20260306_091500.json")
        assert data is not None
        assert data["repo"] == "owner/repo"

    def test_path_traversal_blocked(self, results_dir) -> None:
        with patch("web.app.PROJECT_ROOT", str(results_dir.parent)):
            data = _safe_read_json("../../etc/passwd")
        assert data is None

    def test_nonexistent_file(self, results_dir) -> None:
        with patch("web.app.PROJECT_ROOT", str(results_dir.parent)):
            data = _safe_read_json("results/nonexistent.json")
        assert data is None


# ---------------------------------------------------------------------------
# _discover_result_files
# ---------------------------------------------------------------------------

class TestDiscoverResultFiles:
    def test_discovers_scan_files(self, results_dir) -> None:
        with patch("web.app.RESULTS_DIR", str(results_dir)):
            files = _discover_result_files("scan")
        assert len(files) == 1
        assert files[0]["repo"] == "owner/repo"

    def test_discovers_validate_files(self, results_dir) -> None:
        with patch("web.app.RESULTS_DIR", str(results_dir)):
            files = _discover_result_files("validate")
        assert len(files) == 1

    def test_repo_filter(self, results_dir) -> None:
        with patch("web.app.RESULTS_DIR", str(results_dir)):
            files = _discover_result_files("scan", repo_filter="other/repo")
        assert len(files) == 0

    def test_nonexistent_directory(self) -> None:
        with patch("web.app.RESULTS_DIR", "/nonexistent/path"):
            files = _discover_result_files("scan")
        assert files == []


# ---------------------------------------------------------------------------
# _discover_all_repos
# ---------------------------------------------------------------------------

class TestDiscoverAllRepos:
    def test_finds_repos(self, results_dir) -> None:
        with patch("web.app.RESULTS_DIR", str(results_dir)):
            repos = _discover_all_repos()
        assert "owner/repo" in repos

    def test_no_results_dir(self) -> None:
        with patch("web.app.RESULTS_DIR", "/nonexistent"):
            repos = _discover_all_repos()
        assert repos == []


# ---------------------------------------------------------------------------
# Flask API endpoints
# ---------------------------------------------------------------------------

class TestApiSecrets:
    def test_returns_json(self, flask_client) -> None:
        with patch("web.app._load_secrets", return_value={}):
            resp = flask_client.get("/api/secrets")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "loaded_keys" in data
        assert "field_map" in data

    def test_loaded_keys(self, flask_client) -> None:
        with patch("web.app._load_secrets", return_value={"API_V3_KEY": "val"}):
            resp = flask_client.get("/api/secrets")
        data = resp.get_json()
        assert "API_V3_KEY" in data["loaded_keys"]


class TestApiRepos:
    def test_returns_list(self, flask_client, results_dir) -> None:
        with patch("web.app.RESULTS_DIR", str(results_dir)):
            resp = flask_client.get("/api/repos")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)


class TestApiResults:
    def test_valid_prefix(self, flask_client, results_dir) -> None:
        with patch("web.app.RESULTS_DIR", str(results_dir)):
            resp = flask_client.get("/api/results/scan")
        assert resp.status_code == 200

    def test_invalid_prefix(self, flask_client) -> None:
        resp = flask_client.get("/api/results/invalid")
        assert resp.status_code == 400


class TestApiDashboardData:
    def test_returns_aggregated_data(self, flask_client, results_dir) -> None:
        with (
            patch("web.app.RESULTS_DIR", str(results_dir)),
            patch("web.app.PROJECT_ROOT", str(results_dir.parent)),
        ):
            resp = flask_client.get("/api/dashboard-data")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total_candidates" in data
        assert "by_type" in data
        assert "by_confidence" in data

    def test_no_validate_results(self, flask_client) -> None:
        with patch("web.app.RESULTS_DIR", "/nonexistent"):
            resp = flask_client.get("/api/dashboard-data")
        assert resp.status_code == 404

    def test_with_specific_file(self, flask_client, results_dir) -> None:
        with (
            patch("web.app.RESULTS_DIR", str(results_dir)),
            patch("web.app.PROJECT_ROOT", str(results_dir.parent)),
        ):
            resp = flask_client.get(
                "/api/dashboard-data?file=results/validate_owner_repo_20260306_100000.json"
            )
        assert resp.status_code == 200
