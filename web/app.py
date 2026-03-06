"""Flask web GUI for running the DevinDemo pipeline phases."""

from __future__ import annotations

import glob
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

# Track the running process so we can cancel it
_current_process: subprocess.Popen | None = None
_process_lock = threading.Lock()

PROJECT_ROOT = _PROJECT_ROOT
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results")

# Import secrets loader from main.py so web GUI can check for loaded secrets
sys.path.insert(0, PROJECT_ROOT)
from main import _load_secrets, _SECRETS_MAP  # noqa: E402

# Regex to parse default output filenames: <prefix>_YYYYMMDD_HHMMSS.json
_FILENAME_RE = re.compile(
    r"^(?P<prefix>scan|validate|cleanup|report)_"
    r"(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})_"
    r"(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})\.json$"
)


def _discover_result_files(prefix: str) -> list[dict]:
    """Scan the results/ directory for files matching *prefix*_YYYYMMDD_HHMMSS.json.

    For each file, parse the JSON to extract the 'repo' field and format the
    timestamp from the filename.  Returns a list of dicts sorted newest-first:
        [{"path": "results/scan_20260306_091500.json",
          "repo": "owner/repo",
          "datetime": "2026-03-06 09:15:00 UTC",
          "label": "owner/repo  -  2026-03-06 09:15:00 UTC"}, ...]
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

        # Try to extract repo from JSON content
        repo = "unknown"
        try:
            with open(fpath, "r") as fh:
                data = json.load(fh)
                repo = data.get("repo", "unknown")
        except (json.JSONDecodeError, OSError):
            pass

        results.append({
            "path": rel_path,
            "repo": repo,
            "datetime": dt_str,
            "label": f"{repo}  \u2014  {dt_str}",
        })

    # Sort newest first (by datetime string, which is lexicographically sortable)
    results.sort(key=lambda r: r["datetime"], reverse=True)
    return results


@app.route("/")
def index():
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
        "API_V3_KEY": ["scan-api-key", "validate-api-key"],
        "API_V1_KEY": ["scan-v1-api-key", "validate-v1-api-key"],
        "ORG_ID": ["scan-org-id", "validate-org-id"],
        "NOTION_SECRET": ["report-notion-api-key"],
        "NOTION_MASTER_PAGE_ID": ["report-notion-parent-page-id"],
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


@app.route("/api/results/<prefix>")
def api_results(prefix: str):
    """Return available result files for a given phase prefix."""
    if prefix not in ("scan", "validate", "cleanup", "report"):
        return jsonify({"error": "Invalid prefix"}), 400
    return jsonify(_discover_result_files(prefix))


# Keys/secrets that should be masked in console output
_SECRET_FLAGS = {
    "--api-key", "--v1-api-key", "--notion-api-key", "--slack-webhook-url",
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


def _stream_process(cmd: list[str], sid: str) -> None:
    """Run a subprocess and stream its stdout/stderr to the client via SocketIO."""
    global _current_process

    socketio.emit("console_output", {"data": f"$ {_mask_cmd(cmd)}\n"}, to=sid)

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

        with _process_lock:
            _current_process = proc

        for line in proc.stdout:
            socketio.emit("console_output", {"data": line}, to=sid)

        proc.wait()
        exit_code = proc.returncode

        if exit_code == 0:
            socketio.emit(
                "process_done",
                {"data": f"\nProcess finished successfully (exit code 0)\n", "success": True},
                to=sid,
            )
        else:
            socketio.emit(
                "process_done",
                {"data": f"\nProcess exited with code {exit_code}\n", "success": False},
                to=sid,
            )
    except Exception as exc:
        socketio.emit(
            "process_done",
            {"data": f"\nError: {exc}\n", "success": False},
            to=sid,
        )
    finally:
        with _process_lock:
            _current_process = None


@socketio.on("run_phase")
def handle_run_phase(data):
    """Handle a request to run a pipeline phase."""
    phase = data.get("phase")
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
        emit("console_output", {"data": f"Unknown phase: {phase}\n"})
        return

    # Run in a background thread so we don't block
    from flask import request as flask_request
    client_sid = flask_request.sid
    thread = threading.Thread(target=_stream_process, args=(cmd, client_sid), daemon=True)
    thread.start()


@socketio.on("stop_process")
def handle_stop():
    """Stop the currently running process."""
    global _current_process
    with _process_lock:
        if _current_process is not None and _current_process.poll() is None:
            _current_process.terminate()
            emit("console_output", {"data": "\nProcess terminated by user.\n"})
        else:
            emit("console_output", {"data": "No process is currently running.\n"})


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)
