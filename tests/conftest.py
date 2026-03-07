"""Shared fixtures for the DevinDemo test suite."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture()
def mock_api_client():
    """Return a DevinAPIClient with fake credentials (for unit tests only)."""
    from src.api.client import DevinAPIClient

    return DevinAPIClient(
        api_key="cog_test_key",
        org_id="org-test-org-id",
        v1_api_key="apk_test_v1_key",
    )


@pytest.fixture()
def sample_scan_results() -> dict[str, Any]:
    """Return a minimal Phase 1 scan result dict."""
    return {
        "meta": {
            "scanner_version": "1.4.0",
            "scan_timestamp": "2026-03-06T12:00:00Z",
            "session_id": "ses_test123",
            "repo": "owner/repo",
        },
        "repo": "owner/repo",
        "feature_flags": [
            {
                "id": "abc123def456",
                "verification_status": "unverified",
                "file": "src/config.py",
                "line": 10,
                "pattern_type": "env_var_check",
                "flag_name": "FEATURE_NEW_UI",
                "code_snippet": 'os.getenv("FEATURE_NEW_UI", "false")',
                "reasoning": "Environment variable feature gate.",
            }
        ],
        "dead_code": [
            {
                "id": "dead111222333",
                "verification_status": "unverified",
                "file": "src/utils.py",
                "line": 42,
                "category": "unused_function",
                "code_snippet": "def _old_helper():\n    pass",
                "reasoning": "Function defined but never called.",
            }
        ],
        "tech_debt": [
            {
                "id": "debt444555666",
                "verification_status": "unverified",
                "file": "src/main.py",
                "line": 100,
                "category": "todo_comment",
                "code_snippet": "# TODO: remove after v2 migration",
                "reasoning": "Stale TODO referencing completed migration.",
            }
        ],
        "summary": {
            "files_scanned": 3,
            "total_feature_flags": 1,
            "total_dead_code": 1,
            "total_tech_debt": 1,
            "high_priority_items": ["Remove stale FEATURE_NEW_UI flag"],
        },
    }


@pytest.fixture()
def sample_validate_results(sample_scan_results: dict[str, Any]) -> dict[str, Any]:
    """Return a minimal Phase 2 validated result dict.

    Builds on ``sample_scan_results`` with validation data merged in.
    """
    results = sample_scan_results.copy()

    # Add validation data to each finding
    for item in results["feature_flags"]:
        item["verification_status"] = "verified"
        item["validation"] = {
            "candidate_id": item["id"],
            "confidence": "HIGH",
            "summary": "Confirmed stale feature flag.",
            "layer_results": {
                "layer_1_reconfirm": {"confirmed": True, "method": "grep", "explanation": "Found"},
                "layer_2_git_staleness": {"last_meaningful_edit_date": "2024-01-01", "days_since_last_edit": 800, "is_stale": True},
                "layer_3_active_development": {"actively_being_worked_on": False},
                "layer_4_static_reachability": {"is_reachable": False, "framework_exemption": False},
                "layer_5_issue_archaeology": {"overall_sentiment": "supports_removal"},
                "layer_6_test_coverage": {"tests_reference_candidate": False},
                "layer_7_runtime_signals": {"flag_platform_available": False, "apm_available": False, "referenced_in_infra": False},
                "layer_8_external_consumers": {"is_exported": False, "in_published_package": False, "is_api_endpoint": False},
            },
            "suggested_pr_title": "Remove stale FEATURE_NEW_UI flag",
            "suggested_pr_description": "Remove the unused FEATURE_NEW_UI feature flag.",
        }

    for item in results["dead_code"]:
        item["verification_status"] = "needs_review"
        item["validation"] = {
            "candidate_id": item["id"],
            "confidence": "MEDIUM",
            "summary": "Likely dead code but needs human review.",
            "layer_results": {},
            "suggested_pr_title": "Remove unused _old_helper function",
            "suggested_pr_description": "Remove dead code.",
        }

    for item in results["tech_debt"]:
        item["verification_status"] = "needs_review"
        item["validation"] = {
            "candidate_id": item["id"],
            "confidence": "LOW",
            "summary": "TODO comment may still be relevant.",
            "layer_results": {},
            "blockers": ["Migration status unclear"],
        }

    results["validation_report"] = {
        "confidence_counts": {"HIGH": 1, "MEDIUM": 1, "LOW": 1, "EXEMPT": 0},
        "high_confidence": [{"candidate_id": "abc123def456", "summary": "Confirmed stale."}],
        "medium_confidence": [{"candidate_id": "dead111222333", "summary": "Likely dead."}],
        "low_confidence": [{"candidate_id": "debt444555666", "summary": "Unclear."}],
        "exempt": [],
        "patterns_observed": [],
        "recommendations": [],
    }

    return results
