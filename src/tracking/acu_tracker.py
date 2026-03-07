"""ACU (AI Compute Unit) cost tracking.

Records per-session ACU consumption in an append-only JSON log
(``results/acu_history.json``) so users can see how much each scan
costs and track spending over time.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any


# Default history file location (relative to project root).
_DEFAULT_HISTORY = os.path.join("results", "acu_history.json")


class ACUTracker:
    """Track ACU consumption across pipeline phases.

    Each call to :meth:`record` appends a JSON entry to the history
    file.  The file is an append-only JSON array; concurrent writes
    are serialised with a lock.

    Parameters
    ----------
    history_path:
        Path to the JSON history file.  Defaults to
        ``results/acu_history.json`` relative to the current working
        directory.
    """

    def __init__(self, history_path: str = _DEFAULT_HISTORY) -> None:
        self._path = history_path
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        session_id: str,
        phase: str,
        acu_used: float,
        repo: str = "",
    ) -> dict[str, Any]:
        """Append a single ACU usage entry to the history file.

        Parameters
        ----------
        session_id:
            The Devin session ID that consumed ACU.
        phase:
            Pipeline phase name (``"scan"``, ``"validate"``, ``"cleanup"``).
        acu_used:
            Number of ACU consumed by this session.
        repo:
            Repository in ``owner/repo`` format.

        Returns
        -------
        dict
            The entry that was written (including the timestamp).
        """
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "phase": phase,
            "repo": repo,
            "acu_used": round(acu_used, 4),
        }

        with self._lock:
            history = self._read_history()
            history.append(entry)
            self._write_history(history)

        return entry

    def get_total(self, repo: str | None = None) -> float:
        """Return the total ACU consumed, optionally filtered by *repo*."""
        history = self._read_history()
        return sum(
            e.get("acu_used", 0)
            for e in history
            if repo is None or e.get("repo") == repo
        )

    def get_by_phase(
        self, repo: str | None = None
    ) -> dict[str, float]:
        """Return ACU totals broken down by phase.

        Returns
        -------
        dict
            ``{"scan": float, "validate": float, "cleanup": float}``
        """
        history = self._read_history()
        totals: dict[str, float] = {}
        for entry in history:
            if repo is not None and entry.get("repo") != repo:
                continue
            phase = entry.get("phase", "unknown")
            totals[phase] = totals.get(phase, 0) + entry.get("acu_used", 0)
        return totals

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the last *limit* ACU history entries (newest first)."""
        history = self._read_history()
        # Sort newest-first by timestamp
        history.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return history[:limit]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_history(self) -> list[dict[str, Any]]:
        """Read the history file, returning an empty list if missing."""
        if not os.path.isfile(self._path):
            return []
        try:
            with open(self._path, "r") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return data
            return []
        except (json.JSONDecodeError, OSError):
            return []

    def _write_history(self, history: list[dict[str, Any]]) -> None:
        """Write the full history list back to disk."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(self._path, "w") as fh:
            json.dump(history, fh, indent=2)


def extract_acu_from_session(session: dict[str, Any]) -> float:
    """Extract ACU usage from a Devin API session response.

    The v3 session response may include ``total_acu``,
    ``acu_used``, or ``acu_usage``.  This helper tries each
    field name in order and returns ``0.0`` if none are found.
    """
    for field in ("total_acu", "acu_used", "acu_usage"):
        value = session.get(field)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0
