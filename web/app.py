"""Flask web GUI for running the DevinDemo pipeline phases."""

from __future__ import annotations

import os
import subprocess
import sys
import threading

from flask import Flask, render_template
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "devindemo-gui"
socketio = SocketIO(app, cors_allowed_origins="*")

# Track the running process so we can cancel it
_current_process: subprocess.Popen | None = None
_process_lock = threading.Lock()

# Path to the project root (one level up from web/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@app.route("/")
def index():
    return render_template("index.html")


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

    elif phase == "validate":
        cmd.append("validate")
        # Required args
        if args.get("api_key"):
            cmd.extend(["--api-key", args["api_key"]])
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
