"""Flask web GUI for running the DevinDemo pipeline phases."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading

from flask import Flask, jsonify, render_template
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "devindemo-gui"
socketio = SocketIO(app, cors_allowed_origins="*")

# Per-phase process tracking — each phase can run concurrently,
# but only one run per phase at a time.
_VALID_PHASES = {"scan", "validate", "cleanup", "report"}
_phase_processes: dict[str, subprocess.Popen | None] = {p: None for p in _VALID_PHASES}
_phase_lock = threading.Lock()

# Path to the project root (one level up from web/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results")

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

        results.append(
            {
                "path": rel_path,
                "repo": repo,
                "datetime": dt_str,
                "label": f"{repo}  \u2014  {dt_str}",
            }
        )

    # Sort newest first (by datetime string, which is lexicographically sortable)
    results.sort(key=lambda r: r["datetime"], reverse=True)
    return results


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/results/<prefix>")
def api_results(prefix: str):
    """Return available result files for a given phase prefix."""
    if prefix not in ("scan", "validate", "cleanup", "report"):
        return jsonify({"error": "Invalid prefix"}), 400
    return jsonify(_discover_result_files(prefix))


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

    elif phase == "cleanup":
        cmd.append("cleanup")
        if args.get("api_key"):
            cmd.extend(["--api-key", args["api_key"]])
        if args.get("v1_api_key"):
            cmd.extend(["--v1-api-key", args["v1_api_key"]])
        if args.get("org_id"):
            cmd.extend(["--org-id", args["org_id"]])

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
        emit(f"console_output_{phase}", {"data": f"Unknown phase: {phase}\n"})
        return

    # Run in a background thread so we don't block.
    # Each phase runs independently — multiple phases can execute concurrently.
    from flask import request as flask_request

    client_sid = flask_request.sid
    thread = threading.Thread(
        target=_stream_process, args=(cmd, phase, client_sid), daemon=True
    )
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
