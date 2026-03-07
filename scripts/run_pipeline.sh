#!/usr/bin/env bash
# run_pipeline.sh — Chain DevinDemo pipeline phases sequentially.
#
# Usage:
#   ./scripts/run_pipeline.sh --repo owner/repo --phases scan,validate,cleanup,report
#   ./scripts/run_pipeline.sh --repo owner/repo --phases validate,report --scan-input results/scan_xxx.json
#
# Environment variables (required for phases 1-3):
#   DEVIN_API_KEY_V3  — Devin v3 API key (cog_*)
#   DEVIN_API_KEY_V1  — Devin v1 API key (apk_*)
#   DEVIN_ORG_ID      — Devin organization ID (org-*)

set -euo pipefail

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
REPO=""
PHASES="scan"
SCAN_INPUT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)
            REPO="$2"
            shift 2
            ;;
        --phases)
            PHASES="$2"
            shift 2
            ;;
        --scan-input)
            SCAN_INPUT="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$REPO" ]]; then
    echo "Error: --repo is required." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_DIR="$REPO_ROOT/results"
mkdir -p "$RESULTS_DIR"

# Build common API flags for phases 1-3
API_FLAGS=()
if [[ -n "${DEVIN_API_KEY_V3:-}" ]]; then
    API_FLAGS+=(--api-key "$DEVIN_API_KEY_V3")
fi
if [[ -n "${DEVIN_API_KEY_V1:-}" ]]; then
    API_FLAGS+=(--v1-api-key "$DEVIN_API_KEY_V1")
fi
if [[ -n "${DEVIN_ORG_ID:-}" ]]; then
    API_FLAGS+=(--org-id "$DEVIN_ORG_ID")
fi

# ---------------------------------------------------------------------------
# Track output files between phases
# ---------------------------------------------------------------------------
LAST_SCAN_OUTPUT=""
LAST_VALIDATE_OUTPUT=""
LAST_CLEANUP_OUTPUT=""

# If a scan input was provided, use it as the starting point
if [[ -n "$SCAN_INPUT" ]]; then
    LAST_SCAN_OUTPUT="$SCAN_INPUT"
fi

# ---------------------------------------------------------------------------
# Run phases
# ---------------------------------------------------------------------------
IFS=',' read -ra PHASE_LIST <<< "$PHASES"

for phase in "${PHASE_LIST[@]}"; do
    # Trim whitespace
    phase="$(echo "$phase" | xargs)"

    echo ""
    echo "================================================================"
    echo "  Running phase: $phase"
    echo "================================================================"
    echo ""

    case "$phase" in
        scan)
            python "$REPO_ROOT/main.py" scan "$REPO" "${API_FLAGS[@]}"
            # Find the most recent scan output
            LAST_SCAN_OUTPUT="$(ls -t "$RESULTS_DIR"/scan_*.json 2>/dev/null | head -1)"
            if [[ -z "$LAST_SCAN_OUTPUT" ]]; then
                echo "Error: scan phase completed but no output file found in $RESULTS_DIR" >&2
                exit 1
            fi
            echo "Scan output: $LAST_SCAN_OUTPUT"
            ;;

        validate)
            if [[ -z "$LAST_SCAN_OUTPUT" ]]; then
                echo "Error: validate requires scan results. Run 'scan' first or provide --scan-input." >&2
                exit 1
            fi
            python "$REPO_ROOT/main.py" validate "$LAST_SCAN_OUTPUT" "${API_FLAGS[@]}"
            # Find the most recent validate output
            LAST_VALIDATE_OUTPUT="$(ls -t "$RESULTS_DIR"/validate_*.json 2>/dev/null | head -1)"
            if [[ -z "$LAST_VALIDATE_OUTPUT" ]]; then
                echo "Error: validate phase completed but no output file found in $RESULTS_DIR" >&2
                exit 1
            fi
            echo "Validate output: $LAST_VALIDATE_OUTPUT"
            ;;

        cleanup)
            if [[ -z "$LAST_VALIDATE_OUTPUT" ]]; then
                echo "Error: cleanup requires validated results. Run 'validate' first." >&2
                exit 1
            fi
            python "$REPO_ROOT/main.py" cleanup "$LAST_VALIDATE_OUTPUT" "${API_FLAGS[@]}"
            # Find the most recent cleanup output
            LAST_CLEANUP_OUTPUT="$(ls -t "$RESULTS_DIR"/cleanup_*.json 2>/dev/null | head -1)"
            if [[ -z "$LAST_CLEANUP_OUTPUT" ]]; then
                echo "Error: cleanup phase completed but no output file found in $RESULTS_DIR" >&2
                exit 1
            fi
            echo "Cleanup output: $LAST_CLEANUP_OUTPUT"
            ;;

        report)
            if [[ -z "$LAST_VALIDATE_OUTPUT" ]]; then
                echo "Error: report requires validated results. Run 'validate' first." >&2
                exit 1
            fi
            REPORT_FLAGS=(--input "$LAST_VALIDATE_OUTPUT")
            if [[ -n "$LAST_CLEANUP_OUTPUT" ]]; then
                REPORT_FLAGS+=(--cleanup-results "$LAST_CLEANUP_OUTPUT")
            fi
            python "$REPO_ROOT/main.py" report "${REPORT_FLAGS[@]}"
            ;;

        *)
            echo "Error: unknown phase '$phase'. Valid phases: scan, validate, cleanup, report" >&2
            exit 1
            ;;
    esac

    echo ""
    echo "Phase '$phase' completed successfully."
done

echo ""
echo "================================================================"
echo "  Pipeline finished — all phases completed."
echo "================================================================"
