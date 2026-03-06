"""Part 3 — Generate cleanup PRs to remove legacy code.

This module is a **stub**.  Implementation is planned for a future iteration.

The cleanup generator will:
- Accept validated findings from the validator.
- Create Devin sessions that generate cleanup PRs for each finding group.
- Remove dead branches, simplify conditional logic, drop unused imports.
- Track generated PR URLs for the reporter.
"""

from __future__ import annotations

from typing import Any

from src.api.client import DevinAPIClient


class CleanupPRGenerator:
    """Generate cleanup PRs via Devin sessions."""

    def __init__(self, client: DevinAPIClient) -> None:
        self._client = client

    def generate_prs(self, validated_findings: dict[str, Any]) -> list[dict[str, Any]]:
        """Create Devin sessions that produce cleanup PRs.

        Args:
            validated_findings: Enriched findings from the validator.

        Returns:
            A list of dicts, each containing at minimum ``session_id``
            and ``pr_url`` once the session finishes.
        """
        raise NotImplementedError("Cleanup PR generation is not yet implemented.")
