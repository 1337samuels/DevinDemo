"""CLI entrypoint for the feature-flag & tech-debt scanner.

Usage
-----
    python main.py scan <repo> --api-key <key> --org-id <org_id>

Run ``python main.py --help`` for full usage information.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from src.api.client import DevinAPIClient, DevinAPIError
from src.scanner.identifier import FeatureFlagScanner
from src.validator.validator import LegacyCodeValidator


class ProgressTracker:
    """Stateful callback that prints rich progress during polling.

    Extracts partial results from the session's ``structured_output``
    (which Devin populates incrementally) and displays:
    - Current status and elapsed time
    - Files scanned so far
    - Running counts of feature flags, dead code, and tech debt found
    - Estimated time remaining (once files_scanned starts growing)
    """

    def __init__(self) -> None:
        self._start = time.monotonic()
        self._poll_count = 0
        # Track when the first file was scanned to compute rate.
        self._first_file_time: float | None = None
        self._prev_files = 0

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        return f"{m}m{s:02d}s" if m else f"{s}s"

    def __call__(self, session: dict) -> None:  # noqa: C901
        self._poll_count += 1
        elapsed = time.monotonic() - self._start

        status = session.get("status", "?")
        detail = session.get("status_detail", "")
        status_str = f"{status} ({detail})" if detail else status

        # -- Extract progress from structured_output if available --
        output = session.get("structured_output")
        if output is None:
            print(
                f"  [{self._fmt_elapsed(elapsed)}] {status_str}"
                f"  | Waiting for scan to start …"
            )
            return

        summary = output.get("summary", {})
        total_files = summary.get("total_files", 0)
        files_scanned = summary.get("files_scanned", 0)
        n_flags = summary.get(
            "total_feature_flags", len(output.get("feature_flags", []))
        )
        n_dead = summary.get("total_dead_code", len(output.get("dead_code", [])))
        n_debt = summary.get("total_tech_debt", len(output.get("tech_debt", [])))
        total_findings = n_flags + n_dead + n_debt

        # -- Record when scanning actually started (first file done) --
        if files_scanned > 0 and self._first_file_time is None:
            self._first_file_time = time.monotonic()

        # -- Progress percentage --
        if total_files > 0 and files_scanned > 0:
            pct = min(100, int(files_scanned / total_files * 100))
            progress_str = f"{files_scanned}/{total_files} ({pct}%)"
        elif total_files > 0:
            progress_str = f"0/{total_files} (0%)"
        else:
            progress_str = str(files_scanned)

        # -- ETA based on actual scan rate --
        eta_str = "calculating …"
        if files_scanned > 0 and total_files > 0 and self._first_file_time is not None:
            scan_elapsed = time.monotonic() - self._first_file_time
            if scan_elapsed > 0 and files_scanned > 0:
                rate = files_scanned / scan_elapsed  # files per second
                remaining_files = total_files - files_scanned
                if remaining_files <= 0:
                    eta_str = "finishing up"
                else:
                    eta_seconds = remaining_files / rate
                    eta_str = f"~{self._fmt_elapsed(eta_seconds)}"

        self._prev_files = files_scanned

        # -- Build findings breakdown --
        bar_parts = []
        if n_flags:
            bar_parts.append(f"flags:{n_flags}")
        if n_dead:
            bar_parts.append(f"dead:{n_dead}")
        if n_debt:
            bar_parts.append(f"debt:{n_debt}")
        findings_str = ", ".join(bar_parts) if bar_parts else "none yet"

        print(
            f"  [{self._fmt_elapsed(elapsed)}] {status_str}"
            f"  | Files: {progress_str}"
            f"  | Findings: {total_findings} ({findings_str})"
            f"  | ETA: {eta_str}"
        )


class ValidationProgressTracker:
    """Stateful callback for validation session polling.

    Displays:
    - Elapsed time and session status
    - Candidates validated so far (from partial structured output)
    - Confidence breakdown (HIGH/MEDIUM/LOW/EXEMPT)
    - ETA based on candidate completion rate
    """

    def __init__(self, batch_idx: int, total_batches: int, batch_size: int) -> None:
        self._start = time.monotonic()
        self._poll_count = 0
        self._batch_idx = batch_idx
        self._total_batches = total_batches
        self._batch_size = batch_size
        self._first_candidate_time: float | None = None
        self._prev_candidates = 0

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        return f"{m}m{s:02d}s" if m else f"{s}s"

    def __call__(self, session: dict) -> None:
        self._poll_count += 1
        elapsed = time.monotonic() - self._start

        status = session.get("status", "?")
        detail = session.get("status_detail", "")
        status_str = f"{status} ({detail})" if detail else status

        output = session.get("structured_output")
        batch_label = f"Batch {self._batch_idx}/{self._total_batches}"

        if output is None:
            print(
                f"  [{self._fmt_elapsed(elapsed)}] {batch_label} | {status_str}"
                f"  | Waiting for validation to start \u2026"
            )
            return

        candidates_done = len(output.get("candidates", []))

        # Record when first candidate finishes
        if candidates_done > 0 and self._first_candidate_time is None:
            self._first_candidate_time = time.monotonic()

        # Confidence breakdown
        counts: dict[str, int] = {}
        for c in output.get("candidates", []):
            lvl = c.get("confidence", "?")
            counts[lvl] = counts.get(lvl, 0) + 1
        parts = [f"{k}:{v}" for k, v in sorted(counts.items()) if v > 0]
        conf_str = ", ".join(parts) if parts else "pending"

        # Progress
        progress_str = f"{candidates_done}/{self._batch_size}"

        # ETA
        eta_str = "calculating \u2026"
        if candidates_done > 0 and self._first_candidate_time is not None:
            scan_elapsed = time.monotonic() - self._first_candidate_time
            if scan_elapsed > 0:
                rate = candidates_done / scan_elapsed
                remaining = self._batch_size - candidates_done
                if remaining <= 0:
                    eta_str = "finishing up"
                else:
                    eta_str = f"~{self._fmt_elapsed(remaining / rate)}"

        self._prev_candidates = candidates_done

        print(
            f"  [{self._fmt_elapsed(elapsed)}] {batch_label} | {status_str}"
            f"  | Candidates: {progress_str}"
            f"  | Confidence: {conf_str}"
            f"  | ETA: {eta_str}"
        )


def cmd_scan(args: argparse.Namespace) -> None:
    """Run the identification scan (Part 1)."""
    client = DevinAPIClient(api_key=args.api_key, org_id=args.org_id)
    scanner = FeatureFlagScanner(client)

    tracker = ProgressTracker()
    try:
        results = scanner.scan(
            repo=args.repo,
            poll_interval=args.poll_interval,
            poll_timeout=args.poll_timeout,
            max_acu_limit=args.max_acu,
            on_status_update=tracker,
        )
    except DevinAPIError as exc:
        print(f"Devin API error: {exc}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError as exc:
        print(f"Timeout: {exc}", file=sys.stderr)
        sys.exit(2)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(3)

    # ---- pretty-print summary ----
    summary = results.get("summary", {})
    print("\n" + "=" * 60)
    print("SCAN SUMMARY")
    print("=" * 60)
    print(f"  Repository:     {results.get('repo', 'N/A')}")
    print(f"  Files scanned:  {summary.get('files_scanned', 'N/A')}")
    print(f"  Feature flags:  {summary.get('total_feature_flags', 0)}")
    print(f"  Dead code:      {summary.get('total_dead_code', 0)}")
    print(f"  Tech debt:      {summary.get('total_tech_debt', 0)}")

    high_pri = summary.get("high_priority_items", [])
    if high_pri:
        print("\n  High-priority items:")
        for item in high_pri:
            print(f"    - {item}")
    print("=" * 60)

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(results, fh, indent=2)
        print(f"\nFull results written to {args.output}")


def cmd_validate(args: argparse.Namespace) -> None:
    """Run the validation step (Part 2)."""
    # Load Part 1 results from JSON file
    try:
        with open(args.input, "r") as fh:
            findings = json.load(fh)
    except FileNotFoundError:
        print(f"Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON in {args.input}: {exc}", file=sys.stderr)
        sys.exit(1)

    client = DevinAPIClient(api_key=args.api_key, org_id=args.org_id)

    # Build optional config overrides
    config: dict[str, int] = {}
    if args.staleness_days is not None:
        config["staleness_days"] = args.staleness_days
    if args.pr_lookback_days is not None:
        config["pr_lookback_days"] = args.pr_lookback_days
    if args.issue_lookback_days is not None:
        config["issue_lookback_days"] = args.issue_lookback_days

    validator = LegacyCodeValidator(client, config=config if config else None)

    try:
        results = validator.validate(
            findings,
            poll_interval=args.poll_interval,
            poll_timeout=args.poll_timeout,
            max_acu_limit=args.max_acu,
            progress_tracker_factory=ValidationProgressTracker,
            max_batch_size=args.max_batch_size,
        )
    except DevinAPIError as exc:
        print(f"Devin API error: {exc}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError as exc:
        print(f"Timeout: {exc}", file=sys.stderr)
        sys.exit(2)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(3)

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(results, fh, indent=2)
        print(f"\nFull validated results written to {args.output}")


def cmd_cleanup(args: argparse.Namespace) -> None:
    """Generate cleanup PRs (Part 3) — not yet implemented."""
    print("Cleanup PR generation is not yet implemented.", file=sys.stderr)
    sys.exit(1)


def cmd_report(args: argparse.Namespace) -> None:
    """Publish a report (Part 4) — not yet implemented."""
    print("Reporting is not yet implemented.", file=sys.stderr)
    sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Devin-powered feature-flag & tech-debt scanner.",
    )

    # ---- shared arguments ----
    parser.add_argument(
        "--api-key",
        required=True,
        help="Devin service user API key (starts with cog_).",
    )
    parser.add_argument(
        "--org-id",
        required=True,
        help="Devin organization ID (starts with org-).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---- scan (Part 1) ----
    scan_p = subparsers.add_parser("scan", help="Identify feature flags & tech debt.")
    scan_p.add_argument("repo", help="GitHub repo in owner/repo format.")
    scan_p.add_argument("--output", "-o", help="Write full JSON results to this file.")
    scan_p.add_argument(
        "--poll-interval",
        type=int,
        default=15,
        help="Seconds between status polls (default: 15).",
    )
    scan_p.add_argument(
        "--poll-timeout",
        type=int,
        default=900,
        help="Max seconds to wait for session completion (default: 900).",
    )
    scan_p.add_argument(
        "--max-acu",
        type=int,
        default=None,
        help="Optional ACU cap for the Devin session.",
    )
    scan_p.set_defaults(func=cmd_scan)

    # ---- validate (Part 2) ----
    validate_p = subparsers.add_parser(
        "validate", help="Validate identified findings via 8-layer analysis."
    )
    validate_p.add_argument(
        "input",
        help="Path to the Part 1 JSON results file.",
    )
    validate_p.add_argument(
        "--output", "-o",
        help="Write full validated JSON results to this file.",
    )
    validate_p.add_argument(
        "--poll-interval",
        type=int,
        default=15,
        help="Seconds between status polls (default: 15).",
    )
    validate_p.add_argument(
        "--poll-timeout",
        type=int,
        default=900,
        help="Max seconds to wait per session (default: 900).",
    )
    validate_p.add_argument(
        "--max-acu",
        type=int,
        default=None,
        help="Optional ACU cap per Devin session.",
    )
    validate_p.add_argument(
        "--max-batch-size",
        type=int,
        default=5,
        help="Max candidates per DevinAPI session (default: 5).",
    )
    validate_p.add_argument(
        "--staleness-days",
        type=int,
        default=None,
        help="Override staleness threshold in days (default: 365).",
    )
    validate_p.add_argument(
        "--pr-lookback-days",
        type=int,
        default=None,
        help="Override PR lookback period in days (default: 90).",
    )
    validate_p.add_argument(
        "--issue-lookback-days",
        type=int,
        default=None,
        help="Override issue lookback period in days (default: 180).",
    )
    validate_p.set_defaults(func=cmd_validate)

    # ---- cleanup (Part 3 — stub) ----
    cleanup_p = subparsers.add_parser(
        "cleanup", help="[NOT YET IMPLEMENTED] Generate cleanup PRs."
    )
    cleanup_p.set_defaults(func=cmd_cleanup)

    # ---- report (Part 4 — stub) ----
    report_p = subparsers.add_parser(
        "report", help="[NOT YET IMPLEMENTED] Publish a tech-debt report."
    )
    report_p.set_defaults(func=cmd_report)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
