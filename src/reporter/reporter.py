"""Part 4 — Report findings and cleanup results.

This module is a **stub**.  Implementation is planned for a future iteration.

The reporter will:
- Summarise what was found, what was cleaned, and what needs human review.
- Push reports to one or more of: Notion, Slack, GitHub Issues.
- Support configurable output targets.
"""

from __future__ import annotations

from typing import Any

from src.api.client import DevinAPIClient


class DebtReporter:
    """Generate and publish tech-debt reports."""

    def __init__(self, client: DevinAPIClient) -> None:
        self._client = client

    def report(
        self,
        findings: dict[str, Any],
        cleanup_results: list[dict[str, Any]] | None = None,
        *,
        target: str = "stdout",
    ) -> None:
        """Publish a tech-debt report.

        Args:
            findings: Scanner / validator output.
            cleanup_results: Optional list of cleanup PR results.
            target: Where to send the report. Currently only ``"stdout"``
                    is supported; ``"notion"``, ``"slack"``, and
                    ``"github_issues"`` are planned.
        """
        raise NotImplementedError("Reporting is not yet implemented.")
