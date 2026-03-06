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

from src.api.client import DevinAPIClient, DevinAPIError
from src.scanner.identifier import FeatureFlagScanner


def _status_callback(session: dict) -> None:
    """Print a one-liner whenever the session status changes."""
    status = session.get("status", "?")
    detail = session.get("status_detail", "")
    sid = session.get("session_id", "?")
    suffix = f" ({detail})" if detail else ""
    print(f"[poll] {sid}: {status}{suffix}")


def cmd_scan(args: argparse.Namespace) -> None:
    """Run the identification scan (Part 1)."""
    client = DevinAPIClient(api_key=args.api_key, org_id=args.org_id)
    scanner = FeatureFlagScanner(client)

    try:
        results = scanner.scan(
            repo=args.repo,
            poll_interval=args.poll_interval,
            poll_timeout=args.poll_timeout,
            max_acu_limit=args.max_acu,
            on_status_update=_status_callback,
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
    """Run the validation step (Part 2) — not yet implemented."""
    print("Validation is not yet implemented.", file=sys.stderr)
    sys.exit(1)


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

    # ---- validate (Part 2 — stub) ----
    validate_p = subparsers.add_parser(
        "validate", help="[NOT YET IMPLEMENTED] Validate identified findings."
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
