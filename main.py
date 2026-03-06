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
from src.reporter.reporter import DebtReporter
from src.scanner.identifier import FeatureFlagScanner
from src.validator.validator import LegacyCodeValidator


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
            on_status_update=_status_callback,
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
    """Publish validated findings to Notion and/or Slack (Part 4)."""
    # Load Phase 2 validated findings
    try:
        with open(args.input, "r") as fh:
            findings = json.load(fh)
    except FileNotFoundError:
        print(f"Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON in {args.input}: {exc}", file=sys.stderr)
        sys.exit(1)

    # Load optional Phase 3 cleanup results
    cleanup_results: list[dict] | None = None
    if args.cleanup_results:
        try:
            with open(args.cleanup_results, "r") as fh:
                cleanup_results = json.load(fh)
        except FileNotFoundError:
            print(
                f"Cleanup results file not found: {args.cleanup_results}",
                file=sys.stderr,
            )
            sys.exit(1)
        except json.JSONDecodeError as exc:
            print(
                f"Invalid JSON in {args.cleanup_results}: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

    reporter = DebtReporter(
        notion_api_key=args.notion_api_key,
        notion_database_id=args.notion_database_id,
        notion_parent_page_id=args.notion_parent_page_id,
        slack_webhook_url=args.slack_webhook_url,
    )

    result = reporter.report(findings, cleanup_results)

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"\nReport metadata written to {args.output}")

    # Print the Notion database ID if one was created/used
    db_id = result.get("notion_database_id")
    if db_id:
        print(f"\nNotion database ID: {db_id}")
        print("Save this ID for future runs with --notion-database-id.")


def _add_devin_api_args(parser: argparse.ArgumentParser) -> None:
    """Add --api-key and --org-id arguments to a subparser (phases 1-3)."""
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Devin-powered feature-flag & tech-debt scanner.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---- scan (Part 1) ----
    scan_p = subparsers.add_parser("scan", help="Identify feature flags & tech debt.")
    _add_devin_api_args(scan_p)
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
    _add_devin_api_args(validate_p)
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
    _add_devin_api_args(cleanup_p)
    cleanup_p.set_defaults(func=cmd_cleanup)

    # ---- report (Part 4) ----
    report_p = subparsers.add_parser(
        "report", help="Publish validated findings to Notion / Slack."
    )
    report_p.add_argument(
        "--input", "-i",
        required=True,
        help="Path to the Phase 2 validated findings JSON file.",
    )
    report_p.add_argument(
        "--output", "-o",
        help="Write report metadata JSON to this file.",
    )
    report_p.add_argument(
        "--cleanup-results",
        help="Path to Phase 3 cleanup results JSON (optional).",
    )
    report_p.add_argument(
        "--notion-api-key",
        help="Notion integration API token.",
    )
    report_p.add_argument(
        "--notion-database-id",
        help=(
            "Existing Notion database ID. If omitted, a new database is "
            "created under --notion-parent-page-id."
        ),
    )
    report_p.add_argument(
        "--notion-parent-page-id",
        help=(
            "Notion page ID under which to create a new database "
            "(required on first run if --notion-database-id is not set)."
        ),
    )
    report_p.add_argument(
        "--slack-webhook-url",
        help="Slack incoming webhook URL for PR notifications.",
    )
    report_p.set_defaults(func=cmd_report)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
