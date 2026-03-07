"""Flask web GUI for running the DevinDemo pipeline phases."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template
from flask_socketio import SocketIO, emit

# Ensure the project root is on sys.path so ``from src.…`` works even when
# this file is executed directly (e.g. ``python web/app.py``).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.validator.validator import ALL_LAYER_NUMBERS, LAYER_LABELS  # noqa: E402

app = Flask(__name__)
app.config["SECRET_KEY"] = "devindemo-gui"
socketio = SocketIO(app, cors_allowed_origins="*")

# Per-phase process tracking — each phase can run concurrently,
# but only one run per phase at a time.
_VALID_PHASES = {"scan", "validate", "cleanup", "report"}
_phase_processes: dict[str, subprocess.Popen | None] = {p: None for p in _VALID_PHASES}
_phase_lock = threading.Lock()

# "Run All" pipeline process tracking — prevents concurrent full-pipeline runs.
_current_process: subprocess.Popen | None = None
_process_lock = threading.Lock()

PROJECT_ROOT = _PROJECT_ROOT
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results")

# Import secrets loader from main.py so web GUI can check for loaded secrets
sys.path.insert(0, PROJECT_ROOT)
from main import _load_secrets, _SECRETS_MAP  # noqa: E402

# Regex to parse default output filenames.
# Supports both legacy (no repo slug) and new (with repo slug) formats:
#   <prefix>_YYYYMMDD_HHMMSS.json
#   <prefix>_<owner>_<repo>_YYYYMMDD_HHMMSS.json
_FILENAME_RE = re.compile(
    r"^(?P<prefix>scan|validate|cleanup|report)_"
    r"(?:(?P<repo_slug>[A-Za-z0-9_.-]+_[A-Za-z0-9_.-]+)_)?"
    r"(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})_"
    r"(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})\.json$"
)


def _discover_result_files(prefix: str, repo_filter: str = "") -> list[dict]:
    """Scan the results/ directory for files matching *prefix*.

    Supports both legacy and repo-slug filenames.
    If *repo_filter* is given, only files belonging to that repo are returned.

    Returns a list of dicts sorted newest-first:
        [{"path": "results/validate_owner_repo_20260306_091500.json",
          "repo": "owner/repo",
          "datetime": "2026-03-06 09:15:00 UTC",
          "label": "owner/repo  \u2014  2026-03-06 09:15:00 UTC"}, ...]
    """
    results: list[dict] = []
    if not os.path.isdir(RESULTS_DIR):
        return results

    for fname in os.listdir(RESULTS_DIR):
        m = _FILENAME_RE.match(fname)
        if not m or m.group("prefix") != prefix:
            continue

        fpath = os.path.join(RESULTS_DIR, fname)
        rel_path = os.path.join("results", fname)

        # Parse timestamp from filename
        dt_str = (
            f"{m.group('year')}-{m.group('month')}-{m.group('day')} "
            f"{m.group('hour')}:{m.group('minute')}:{m.group('second')} UTC"
        )

        # Extract repo: prefer filename slug, fall back to JSON content
        repo_slug = m.group("repo_slug") or ""
        repo = "unknown"
        if repo_slug:
            # Convert owner_repo back to owner/repo
            # The slug has exactly one underscore separating owner and repo name
            # but repo names may contain underscores, so we split on the first _
            parts = repo_slug.split("_", 1)
            repo = "/".join(parts) if len(parts) == 2 else repo_slug

        # Always try JSON content for authoritative repo value
        try:
            with open(fpath, "r") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    json_repo = data.get("repo", "")
                    if json_repo:
                        repo = json_repo
        except (json.JSONDecodeError, OSError):
            pass

        # Apply repo filter if specified
        if repo_filter and repo != repo_filter:
            continue

        results.append({
            "path": rel_path,
            "repo": repo,
            "datetime": dt_str,
            "label": f"{repo}  \u2014  {dt_str}",
        })

    # Sort newest first (by datetime string, which is lexicographically sortable)
    results.sort(key=lambda r: r["datetime"], reverse=True)
    return results


def _discover_all_repos() -> list[str]:
    """Return a sorted, deduplicated list of repo names across all result files."""
    repos: set[str] = set()
    if not os.path.isdir(RESULTS_DIR):
        return []
    for fname in os.listdir(RESULTS_DIR):
        m = _FILENAME_RE.match(fname)
        if not m:
            continue
        fpath = os.path.join(RESULTS_DIR, fname)
        # Try filename slug first
        repo_slug = m.group("repo_slug") or ""
        repo = ""
        if repo_slug:
            parts = repo_slug.split("_", 1)
            repo = "/".join(parts) if len(parts) == 2 else repo_slug
        # Try JSON content for authoritative value
        try:
            with open(fpath, "r") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    json_repo = data.get("repo", "")
                    if json_repo:
                        repo = json_repo
        except (json.JSONDecodeError, OSError):
            pass
        if repo and repo != "unknown":
            repos.add(repo)
    return sorted(repos)


@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/dashboard")
def dashboard():
    return render_template("index.html")


@app.route("/api/secrets")
def api_secrets():
    """Return which secret keys were found in secrets.txt.

    Response: {"loaded_keys": ["API_V3_KEY", ...], "field_map": {"scan-api-key": true, ...}}
    The field_map maps HTML input IDs to a boolean indicating the secret is available.
    """
    secrets = _load_secrets(Path(PROJECT_ROOT) / "secrets.txt")
    loaded_keys = [k for k in _SECRETS_MAP if k in secrets]

    # Map secrets.txt keys to the HTML input element IDs they correspond to
    _KEY_TO_FIELDS: dict[str, list[str]] = {
        "API_V3_KEY": ["scan-api-key", "validate-api-key", "cleanup-api-key", "runall-api-key"],
        "API_V1_KEY": ["scan-v1-api-key", "validate-v1-api-key", "cleanup-v1-api-key", "runall-v1-api-key"],
        "ORG_ID": ["scan-org-id", "validate-org-id", "cleanup-org-id", "runall-org-id"],
        "SLACK_WEBHOOK_URL": ["report-slack-webhook-url", "runall-slack-webhook-url"],
        "SLACK_WEBHOOKS_URL": ["report-slack-webhook-url", "runall-slack-webhook-url"],
        "NOTION_SECRET": ["report-notion-api-key", "runall-notion-api-key"],
        "NOTION_MASTER_PAGE_ID": ["report-notion-parent-page-id", "runall-notion-parent-page-id"],
    }
    field_map: dict[str, bool] = {}
    for key in loaded_keys:
        for field_id in _KEY_TO_FIELDS.get(key, []):
            field_map[field_id] = True

    return jsonify({"loaded_keys": loaded_keys, "field_map": field_map})


@app.route("/api/validation-layers")
def api_validation_layers():
    """Return the available validation layers with labels."""
    return jsonify([
        {"number": n, "label": LAYER_LABELS[n]}
        for n in ALL_LAYER_NUMBERS
    ])


@app.route("/api/repos")
def api_repos():
    """Return a list of unique repos found across all result files."""
    return jsonify(_discover_all_repos())


@app.route("/api/results/<prefix>")
def api_results(prefix: str):
    """Return available result files for a given phase prefix.

    Query params:
        repo: optional repo name to filter by (e.g. ``owner/repo``).
    """
    if prefix not in ("scan", "validate", "cleanup", "report"):
        return jsonify({"error": "Invalid prefix"}), 400
    from flask import request as flask_request
    repo_filter = flask_request.args.get("repo", "").strip()
    return jsonify(_discover_result_files(prefix, repo_filter=repo_filter))


def _safe_read_json(file_path: str) -> dict | list | None:
    """Read and return parsed JSON from *file_path* (relative to PROJECT_ROOT).

    Returns ``None`` if the path escapes the project root or the file is
    unreadable.
    """
    abs_path = os.path.realpath(os.path.join(PROJECT_ROOT, file_path))
    if not abs_path.startswith(os.path.realpath(PROJECT_ROOT) + os.sep):
        return None
    if not os.path.isfile(abs_path):
        return None
    try:
        with open(abs_path, "r") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


@app.route("/api/dashboard-data")
def api_dashboard_data():
    """Return aggregated dashboard data from validate and cleanup results.

    Query params:
        repo: optional repo name to filter files by.
        file: optional path to a specific validate result file.
              If omitted, the most recent validate result (for the repo) is used.
    """
    from flask import request as flask_request

    repo_filter = flask_request.args.get("repo", "").strip()
    file_path = flask_request.args.get("file", "").strip()

    # --- Load validate data ---
    if not file_path:
        results = _discover_result_files("validate", repo_filter=repo_filter)
        if not results:
            return jsonify({"error": "No validate results found"}), 404
        file_path = results[0]["path"]

    data = _safe_read_json(file_path)
    if data is None:
        return jsonify({"error": f"Cannot read file: {file_path}"}), 404
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid validate result format"}), 400

    # --- Aggregate candidates from validate data ---
    candidates: list[dict] = []
    category_map = {
        "feature_flags": "feature_flag",
        "dead_code": "dead_code",
        "tech_debt": "tech_debt",
    }
    for key, cat_label in category_map.items():
        for item in data.get(key, []):
            entry = {
                "id": item.get("id", ""),
                "category": item.get("category", cat_label),
                "file": item.get("file", ""),
                "line": item.get("line", 0),
                "confidence": "UNKNOWN",
                "pr_url": None,
                "pr_opened": False,
                "summary": "",
            }
            validation = item.get("validation", {})
            if validation:
                entry["confidence"] = validation.get("confidence", "UNKNOWN")
                entry["summary"] = validation.get("summary", "")
                entry["pr_url"] = validation.get("pr_url")
                entry["pr_opened"] = validation.get("pr_opened", False)
                if not entry["pr_url"]:
                    entry["pr_url"] = validation.get("suggested_pr_title")
            candidates.append(entry)

    # --- Load PR links from cleanup results for the same repo ---
    pr_links: list[dict] = []
    repo_name = data.get("repo", "unknown")
    cleanup_files = _discover_result_files("cleanup", repo_filter=repo_name)
    for cf in cleanup_files:
        cleanup_data = _safe_read_json(cf["path"])
        if cleanup_data is None:
            continue
        # Cleanup results are a list of per-candidate result dicts
        items = cleanup_data if isinstance(cleanup_data, list) else []
        for item in items:
            if item.get("status") == "pr_opened" and item.get("pr_url", "").startswith("http"):
                pr_links.append({
                    "candidate_id": item.get("candidate_id", ""),
                    "file": item.get("file", ""),
                    "url": item["pr_url"],
                })

    # Also pick up PR links from validate data (inline pr_url fields)
    for c in candidates:
        if c.get("pr_url") and c["pr_url"].startswith("http"):
            # Avoid duplicates (same URL)
            existing_urls = {pl["url"] for pl in pr_links}
            if c["pr_url"] not in existing_urls:
                pr_links.append({
                    "candidate_id": c["id"],
                    "file": c["file"],
                    "url": c["pr_url"],
                })

    # --- Build aggregations ---
    by_type: dict[str, int] = {}
    by_confidence: dict[str, int] = {}

    for c in candidates:
        cat = c["category"]
        by_type[cat] = by_type.get(cat, 0) + 1

        conf = c["confidence"]
        by_confidence[conf] = by_confidence.get(conf, 0) + 1

    return jsonify({
        "file": file_path,
        "repo": repo_name,
        "total_candidates": len(candidates),
        "by_type": by_type,
        "by_confidence": by_confidence,
        "pr_links": pr_links,
        "candidates": candidates,
    })


# Keys/secrets that should be masked in console output
_SECRET_FLAGS = {
    "--api-key",
    "--v1-api-key",
    "--notion-api-key",
    "--slack-webhook-url",
}


def _mask_cmd(cmd: list[str]) -> str:
    """Return a display-safe version of *cmd* with secret values masked."""
    parts: list[str] = []
    mask_next = False
    for token in cmd:
        if mask_next:
            parts.append("****")
            mask_next = False
        elif token in _SECRET_FLAGS:
            parts.append(token)
            mask_next = True
        else:
            parts.append(token)
    return " ".join(parts)


def _stream_process(cmd: list[str], phase: str, sid: str) -> None:
    """Run a subprocess and stream its stdout/stderr to the client via SocketIO.

    Each phase emits to its own ``console_output_<phase>`` and
    ``process_done_<phase>`` events so the frontend can display them in
    separate console panels.
    """
    out_event = f"console_output_{phase}"
    done_event = f"process_done_{phase}"

    socketio.emit(out_event, {"data": f"$ {_mask_cmd(cmd)}\n"}, to=sid)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=PROJECT_ROOT,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

        with _phase_lock:
            _phase_processes[phase] = proc

        for line in proc.stdout:
            socketio.emit(out_event, {"data": line}, to=sid)

        proc.wait()
        exit_code = proc.returncode

        if exit_code == 0:
            socketio.emit(
                done_event,
                {
                    "data": "\nProcess finished successfully (exit code 0)\n",
                    "success": True,
                },
                to=sid,
            )
        else:
            socketio.emit(
                done_event,
                {"data": f"\nProcess exited with code {exit_code}\n", "success": False},
                to=sid,
            )
    except Exception as exc:
        socketio.emit(
            done_event,
            {"data": f"\nError: {exc}\n", "success": False},
            to=sid,
        )
    finally:
        with _phase_lock:
            _phase_processes[phase] = None


@socketio.on("run_phase")
def handle_run_phase(data):
    """Handle a request to run a pipeline phase."""
    phase = data.get("phase")
    args = data.get("args", {})
    sid = data.get("sid")

    if not sid:
        from flask import request as flask_request

        sid = flask_request.sid

    if phase not in _VALID_PHASES:
        emit(f"console_output_{phase}", {"data": f"Unknown phase: {phase}\n"})
        return

    # Check if this specific phase already has a running process
    with _phase_lock:
        proc = _phase_processes.get(phase)
        if proc is not None and proc.poll() is None:
            emit(
                f"console_output_{phase}",
                {
                    "data": f"Phase '{phase}' is already running. Please wait or stop it first.\n"
                },
            )
            return

    cmd = [sys.executable, "main.py"]

    if phase == "scan":
        cmd.append("scan")
        # Required args
        if args.get("api_key"):
            cmd.extend(["--api-key", args["api_key"]])
        if args.get("v1_api_key"):
            cmd.extend(["--v1-api-key", args["v1_api_key"]])
        if args.get("org_id"):
            cmd.extend(["--org-id", args["org_id"]])
        if args.get("repo"):
            cmd.append(args["repo"])
        # Optional args
        if args.get("output"):
            cmd.extend(["--output", args["output"]])
        if args.get("poll_interval"):
            cmd.extend(["--poll-interval", str(args["poll_interval"])])
        if args.get("poll_timeout"):
            cmd.extend(["--poll-timeout", str(args["poll_timeout"])])
        if args.get("max_acu"):
            cmd.extend(["--max-acu", str(args["max_acu"])])
        if args.get("batch_size"):
            cmd.extend(["--batch-size", str(args["batch_size"])])

    elif phase == "validate":
        cmd.append("validate")
        # Required args
        if args.get("api_key"):
            cmd.extend(["--api-key", args["api_key"]])
        if args.get("v1_api_key"):
            cmd.extend(["--v1-api-key", args["v1_api_key"]])
        if args.get("org_id"):
            cmd.extend(["--org-id", args["org_id"]])
        if args.get("input_file"):
            cmd.append(args["input_file"])
        # Optional args
        if args.get("output"):
            cmd.extend(["--output", args["output"]])
        if args.get("poll_interval"):
            cmd.extend(["--poll-interval", str(args["poll_interval"])])
        if args.get("poll_timeout"):
            cmd.extend(["--poll-timeout", str(args["poll_timeout"])])
        if args.get("max_acu"):
            cmd.extend(["--max-acu", str(args["max_acu"])])
        if args.get("max_batch_size"):
            cmd.extend(["--max-batch-size", str(args["max_batch_size"])])
        if args.get("staleness_days"):
            cmd.extend(["--staleness-days", str(args["staleness_days"])])
        if args.get("pr_lookback_days"):
            cmd.extend(["--pr-lookback-days", str(args["pr_lookback_days"])])
        if args.get("issue_lookback_days"):
            cmd.extend(["--issue-lookback-days", str(args["issue_lookback_days"])])
        if args.get("layers"):
            cmd.extend(["--layers", args["layers"]])

    elif phase == "cleanup":
        cmd.append("cleanup")
        # Required args
        if args.get("api_key"):
            cmd.extend(["--api-key", args["api_key"]])
        if args.get("v1_api_key"):
            cmd.extend(["--v1-api-key", args["v1_api_key"]])
        if args.get("org_id"):
            cmd.extend(["--org-id", args["org_id"]])
        if args.get("input_file"):
            cmd.append(args["input_file"])
        # Optional args
        if args.get("output"):
            cmd.extend(["--output", args["output"]])
        if args.get("poll_interval"):
            cmd.extend(["--poll-interval", str(args["poll_interval"])])
        if args.get("poll_timeout"):
            cmd.extend(["--poll-timeout", str(args["poll_timeout"])])
        if args.get("max_acu"):
            cmd.extend(["--max-acu", str(args["max_acu"])])
        if args.get("auto_merge"):
            cmd.append("--auto-merge")

    elif phase == "report":
        cmd.append("report")
        # Required
        if args.get("input_file"):
            cmd.extend(["--input", args["input_file"]])
        # Optional
        if args.get("output"):
            cmd.extend(["--output", args["output"]])
        if args.get("cleanup_results"):
            cmd.extend(["--cleanup-results", args["cleanup_results"]])
        if args.get("notion_api_key"):
            cmd.extend(["--notion-api-key", args["notion_api_key"]])
        if args.get("notion_database_id"):
            cmd.extend(["--notion-database-id", args["notion_database_id"]])
        if args.get("notion_parent_page_id"):
            cmd.extend(["--notion-parent-page-id", args["notion_parent_page_id"]])
        if args.get("slack_webhook_url"):
            cmd.extend(["--slack-webhook-url", args["slack_webhook_url"]])

    else:
        emit("error", {"data": f"Unknown phase: {phase}\n"})
        return

    # Run in a background thread so we don't block.
    # Each phase runs independently — multiple phases can execute concurrently.
    from flask import request as flask_request

    client_sid = flask_request.sid
    thread = threading.Thread(
        target=_stream_process, args=(cmd, phase, client_sid), daemon=True
    )
    thread.start()


def _find_latest_result(prefix: str) -> str | None:
    """Return the path to the newest ``results/<prefix>_*.json`` file, or *None*."""
    if not os.path.isdir(RESULTS_DIR):
        return None
    candidates: list[str] = []
    for fname in os.listdir(RESULTS_DIR):
        if fname.startswith(prefix + "_") and fname.endswith(".json"):
            candidates.append(os.path.join(RESULTS_DIR, fname))
    if not candidates:
        return None
    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def _run_phase_subprocess(cmd: list[str], sid: str, phase: str | None = None) -> int:
    """Run a subprocess, stream output to *sid*, and return the exit code.

    Unlike ``_stream_process`` this does **not** emit ``process_done`` and
    does **not** update ``_current_process`` — the caller (``_run_all_pipeline``)
    manages the process reference and decides whether to continue.

    When *phase* is provided the ``console_output`` payload includes a
    ``phase`` key so the frontend can route the output to the correct
    per-phase console panel.
    """
    global _current_process

    def _emit_console(text: str) -> None:
        payload: dict = {"data": text}
        if phase:
            payload["phase"] = phase
        socketio.emit("console_output", payload, to=sid)

    _emit_console(f"$ {_mask_cmd(cmd)}\n")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=PROJECT_ROOT,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

        # Update _current_process so handle_stop can terminate this subprocess,
        # but do NOT clear it in `finally` — the caller (_run_all_pipeline)
        # is responsible for clearing _current_process when the entire
        # pipeline finishes, preventing a race window between phases.
        with _process_lock:
            _current_process = proc

        for line in proc.stdout:
            _emit_console(line)

        proc.wait()
        return proc.returncode
    except Exception as exc:
        _emit_console(f"\nError: {exc}\n")
        return -1


_PHASE_NAMES = ["Phase 1: Scan", "Phase 2: Validate", "Phase 3: Cleanup", "Phase 4: Report"]


def _run_all_pipeline(args: dict, sid: str) -> None:
    """Run all four phases sequentially, chaining outputs.

    Stops immediately if any phase exits with a non-zero code.
    _current_process is kept set for the entire pipeline duration to prevent
    concurrent runs; it is only cleared in the ``finally`` block.
    """
    repo = args.get("repo", "")
    api_key = args.get("api_key") or None
    v1_api_key = args.get("v1_api_key") or None
    org_id = args.get("org_id") or None
    notion_api_key = args.get("notion_api_key") or None
    notion_database_id = args.get("notion_database_id") or None
    notion_parent_page_id = args.get("notion_parent_page_id") or None
    slack_webhook_url = args.get("slack_webhook_url") or None

    phase_ids = ["scan", "validate", "cleanup", "report"]

    def _header(phase_idx: int) -> None:
        name = _PHASE_NAMES[phase_idx]
        pid = phase_ids[phase_idx]
        socketio.emit("console_output", {
            "data": f"\n{'=' * 60}\n  {name}\n{'=' * 60}\n\n",
            "phase": pid,
        }, to=sid)
        socketio.emit("run_all_phase", {"phase_index": phase_idx}, to=sid)

    try:
        # ---- Phase 1: Scan ----
        _header(0)
        cmd_scan = [sys.executable, "main.py", "scan"]
        if api_key:
            cmd_scan.extend(["--api-key", api_key])
        if v1_api_key:
            cmd_scan.extend(["--v1-api-key", v1_api_key])
        if org_id:
            cmd_scan.extend(["--org-id", org_id])
        cmd_scan.append(repo)

        rc = _run_phase_subprocess(cmd_scan, sid, phase="scan")
        if rc != 0:
            socketio.emit("process_done", {
                "data": f"\nPipeline stopped: Phase 1 (Scan) failed with exit code {rc}\n",
                "success": False,
            }, to=sid)
            return

        scan_output = _find_latest_result("scan")
        if not scan_output:
            socketio.emit("process_done", {
                "data": "\nPipeline stopped: no scan output file found.\n",
                "success": False,
            }, to=sid)
            return

        # ---- Phase 2: Validate ----
        _header(1)
        cmd_validate = [sys.executable, "main.py", "validate"]
        if api_key:
            cmd_validate.extend(["--api-key", api_key])
        if v1_api_key:
            cmd_validate.extend(["--v1-api-key", v1_api_key])
        if org_id:
            cmd_validate.extend(["--org-id", org_id])
        cmd_validate.append(scan_output)

        rc = _run_phase_subprocess(cmd_validate, sid, phase="validate")
        if rc != 0:
            socketio.emit("process_done", {
                "data": f"\nPipeline stopped: Phase 2 (Validate) failed with exit code {rc}\n",
                "success": False,
            }, to=sid)
            return

        validate_output = _find_latest_result("validate")
        if not validate_output:
            socketio.emit("process_done", {
                "data": "\nPipeline stopped: no validate output file found.\n",
                "success": False,
            }, to=sid)
            return

        # ---- Phase 3: Cleanup ----
        _header(2)
        cmd_cleanup = [sys.executable, "main.py", "cleanup"]
        if api_key:
            cmd_cleanup.extend(["--api-key", api_key])
        if v1_api_key:
            cmd_cleanup.extend(["--v1-api-key", v1_api_key])
        if org_id:
            cmd_cleanup.extend(["--org-id", org_id])
        cmd_cleanup.append(validate_output)

        rc = _run_phase_subprocess(cmd_cleanup, sid, phase="cleanup")
        if rc != 0:
            socketio.emit("process_done", {
                "data": f"\nPipeline stopped: Phase 3 (Cleanup) failed with exit code {rc}\n",
                "success": False,
            }, to=sid)
            return

        cleanup_output = _find_latest_result("cleanup")

        # ---- Phase 4: Report ----
        _header(3)
        cmd_report = [sys.executable, "main.py", "report", "--input", validate_output]
        if cleanup_output:
            cmd_report.extend(["--cleanup-results", cleanup_output])
        if notion_api_key:
            cmd_report.extend(["--notion-api-key", notion_api_key])
        if notion_database_id:
            cmd_report.extend(["--notion-database-id", notion_database_id])
        if notion_parent_page_id:
            cmd_report.extend(["--notion-parent-page-id", notion_parent_page_id])
        if slack_webhook_url:
            cmd_report.extend(["--slack-webhook-url", slack_webhook_url])

        rc = _run_phase_subprocess(cmd_report, sid, phase="report")
        if rc != 0:
            socketio.emit("process_done", {
                "data": f"\nPipeline stopped: Phase 4 (Report) failed with exit code {rc}\n",
                "success": False,
            }, to=sid)
            return

        socketio.emit("process_done", {
            "data": "\n" + "=" * 60 + "\n  All 4 phases completed successfully!\n" + "=" * 60 + "\n",
            "success": True,
        }, to=sid)
    finally:
        # Always clear _current_process when the pipeline exits (success,
        # failure, or exception) so subsequent runs are not blocked.
        with _process_lock:
            _current_process = None


@socketio.on("run_all")
def handle_run_all(data):
    """Run all 4 pipeline phases sequentially."""
    args = data.get("args", {})
    sid = data.get("sid")

    if not sid:
        from flask import request as flask_request
        sid = flask_request.sid

    # Check if a process is already running
    with _process_lock:
        if _current_process is not None and _current_process.poll() is None:
            emit("console_output", {"data": "A process is already running. Please wait or stop it first.\n"})
            return

    repo = args.get("repo", "").strip()
    if not repo:
        emit("console_output", {"data": "Repository is required.\n"})
        return

    from flask import request as flask_request
    client_sid = flask_request.sid
    thread = threading.Thread(target=_run_all_pipeline, args=(args, client_sid), daemon=True)
    thread.start()


@socketio.on("stop_process")
def handle_stop(data=None):
    """Stop a running process for a specific phase."""
    phase = (data or {}).get("phase", "scan")
    out_event = f"console_output_{phase}"
    with _phase_lock:
        proc = _phase_processes.get(phase)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            emit(out_event, {"data": "\nProcess terminated by user.\n"})
        else:
            emit(
                out_event,
                {"data": f"No process is currently running for phase '{phase}'.\n"},
            )


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)
