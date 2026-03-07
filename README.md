# DevinDemo — Feature Flag & Tech Debt Scanner

A Devin-powered automation that scans Python codebases for stale feature flags,
dead code, and tech debt — then automates cleanup via PRs and reports findings.

> *"We've got feature flags from three years ago still in the codebase. Dead code
> everywhere. New engineers spend their first two weeks just figuring out what's
> real and what's abandoned."*

## How it works

The tool uses the [Devin v3 API](https://docs.devin.ai/api-reference/v3/usage-examples)
to create AI-powered sessions that analyse a target repository.  The pipeline
has four stages:

| Stage | Status | Description |
|-------|--------|-------------|
| **1. Identify** | Implemented | Fast scan — flag potential feature flags (with staleness signals), dead code (strong unused signals only), actionable tech debt |
| **2. Validate** | Stub | Deep-dive verification of each finding from Part 1 (slow, thorough) |
| **3. Cleanup**  | Stub | Generate PRs that remove dead code and simplify branches |
| **4. Report**   | Stub | Publish summaries to Notion, Slack, or GitHub Issues |

## Prerequisites

- **Python 3.10+**
- **pip** dependencies: `pip install -r requirements.txt`
- A Devin **service user** API key (starts with `cog_`) — create one in **Settings > Service users**
- Your Devin **organization ID** (starts with `org-`) — find it in **Settings > General**

### Authentication

| Script / Tool       | Key type                                  | Where to create                           |
|---------------------|-------------------------------------------|-------------------------------------------|
| `main.py` (scanner) | Service user key (`cog_*`)               | **Settings > Service users**              |
| `list_sessions.py`  | Service user key (`cog_*`)               | **Settings > Service users**              |
| `send_message.py`   | Personal key (`apk_user_*`) or Service key (`apk_*`) | **Settings > API keys** |

> **Note:** `cog_*` keys do **not** work with the v1 API, and `apk_*` / `apk_user_*` keys do **not** work with the v3 API.
> See the [authentication docs](https://docs.devin.ai/api-reference/authentication) for details.

### `secrets.txt` (recommended)

Instead of passing API keys on every command, create a `secrets.txt` file in the
project root.  The CLI will automatically load it and fill in any missing flags.

```text
NOTION_SECRET = "ntn_..."
NOTION_MASTER_PAGE_ID = "31b37812..."

API_V3_KEY = "cog_..."
ORG_ID = "org-..."
API_V1_KEY = "apk_..."
```

| Key                    | Maps to CLI flag           | Phases |
|------------------------|----------------------------|--------|
| `API_V3_KEY`           | `--api-key`                | 1-3    |
| `API_V1_KEY`           | `--v1-api-key`             | 1-3    |
| `ORG_ID`               | `--org-id`                 | 1-3    |
| `NOTION_SECRET`        | `--notion-api-key`         | 4      |
| `NOTION_MASTER_PAGE_ID`| `--notion-parent-page-id`  | 4      |

> **Important:** `secrets.txt` is listed in `.gitignore` and must **never** be
> committed.  CLI flags always take precedence over `secrets.txt` values.

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Run a scan
python main.py --api-key <YOUR_COG_KEY> --org-id <YOUR_ORG_ID> scan owner/repo
```

## Usage

### main.py (scanner)

```bash
python main.py --api-key <KEY> --org-id <ORG_ID> <command> [options]
```

### Global arguments

| Argument     | Description |
|--------------|-------------|
| `--api-key`  | Devin service user API key (starts with `cog_`) |
| `--org-id`   | Devin organization ID (starts with `org-`) |

### Commands

#### `scan` — Identify feature flags & tech debt (Part 1)

```bash
python main.py --api-key <KEY> --org-id <ORG_ID> scan owner/repo [options]
```

| Option            | Description                                  | Default |
|-------------------|----------------------------------------------|---------|
| `repo`            | GitHub repository in `owner/repo` format     | —       |
| `-o`, `--output`  | Write full JSON results to a file            | —       |
| `--poll-interval`  | Seconds between status polls                | 15      |
| `--poll-timeout`   | Max seconds to wait for session completion  | 900     |
| `--max-acu`        | Optional ACU cap for the Devin session      | —       |

#### `validate` — Validate findings (Part 2) — *not yet implemented*

#### `cleanup` — Generate cleanup PRs (Part 3) — *not yet implemented*

#### `report` — Publish tech-debt report (Part 4) — *not yet implemented*

#### Examples

Scan a repository and print results to stdout:

```bash
python main.py --api-key cog_xxx --org-id org-xxx scan myorg/myrepo
```

Scan and save full JSON results to a file:

```bash
python main.py --api-key cog_xxx --org-id org-xxx scan myorg/myrepo -o results.json
```

The output JSON from Part 1 is the input for Part 2.  Each finding has a unique
`id` and a `verification_status` field (initially `"unverified"`) that Part 2
will update to `"verified"`, `"false_positive"`, or `"needs_review"`.

### send_message.py

A v1 API workaround for **Teams accounts** that cannot use the v3 `send_message` endpoint.

```bash
python send_message.py <YOUR_DEVIN_API_KEY> <SESSION_ID> "<MESSAGE>"
```

#### Required arguments

| Argument       | Description                                                                          |
|----------------|--------------------------------------------------------------------------------------|
| `api_key`      | Your Devin API key (`apk_user_*` or `apk_*`)                                        |
| `session_id`   | The ID of the session to send the message to                                         |
| `message`      | The message text to send to Devin                                                    |

#### Examples

Send a message to a running session:

```bash
python send_message.py apk_user_your_key_here abc-123-def-456 "Please also add unit tests"
```

## Sample output

### Scanner (main.py)

```
[scanner] Creating Devin session to scan myorg/myrepo …
[scanner] Session created: abc123
[scanner] URL: https://app.devin.ai/sessions/abc123
[scanner] Polling every 15s (timeout 900s) …
[poll] abc123: running (working)
[poll] abc123: running (finished)

============================================================
SCAN SUMMARY
============================================================
  Repository:     myorg/myrepo
  Files scanned:  47
  Feature flags:  5
  Dead code:      12
  Tech debt:      8

  High-priority items:
    - ENABLE_LEGACY_AUTH flag in src/auth.py:42 — always True, dead else branch
    - 3 unused imports in src/utils.py
============================================================
```

### send_message.py

```
Sending message to session abc-123-def-456 ...
Message sent successfully.
```

### Output JSON structure

Each finding includes fields for Part 2 hand-off:

```json
{
  "meta": {
    "scanner_version": "1.0.0",
    "scan_timestamp": "2026-03-06T12:00:00Z",
    "session_id": "abc123",
    "repo": "myorg/myrepo"
  },
  "feature_flags": [
    {
      "id": "a1b2c3d4e5f6",
      "verification_status": "unverified",
      "file": "src/auth.py",
      "line": 42,
      "pattern_type": "boolean_config_flag",
      "code_snippet": "ENABLE_LEGACY_AUTH = True",
      "flag_name": "ENABLE_LEGACY_AUTH",
      "reasoning": "Boolean flag gating authentication path"
    }
  ],
  "dead_code": [ ... ],
  "tech_debt": [ ... ],
  "summary": { ... }
}
```

Part 2 will iterate through each finding by `id`, perform deep verification,
and update `verification_status` accordingly.

## Why use the v1 API for sending messages?

The v3 API's `send_message` endpoint requires the `ManageOrgSessions` permission, which is only available to **Enterprise** accounts with `cog_*` service user keys.

If you have a **Teams** account, you can use the [v1 `send_message` endpoint](https://docs.devin.ai/api-reference/v1/sessions/send-a-message-to-an-existing-devin-session) instead. It accepts Personal API Keys (`apk_user_*`) and Service API Keys (`apk_*`), which are available on all account tiers. The `send_message.py` script wraps this endpoint for convenience.

## Web GUI

A browser-based interface for running all pipeline phases with live console output streaming.

### Running the web server

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server
python web/app.py
```

The GUI is available at **http://localhost:5000**.

### Features

- **Tab navigation** across all four phases (Scan, Validate, Cleanup, Report)
- **Form inputs** for all CLI arguments, including password-masked fields for API keys
- **Live console output** streamed in real time via WebSocket as the subprocess runs
- **Stop button** to terminate a running process
- **File discovery dropdowns** — for phases that require input files (Validate, Report), the server automatically scans the `results/` directory and presents matching files in a dropdown showing `owner/repo — YYYY-MM-DD HH:MM:SS UTC`. A "Custom path…" option is available as a fallback.

## CI / Scheduled Scanning

A GitHub Actions workflow is included to run the pipeline on a schedule or on
demand.

### Setting up GitHub Secrets

Go to **Settings > Secrets and variables > Actions > Secrets** and add:

| Secret                 | Description                                    | Required for     |
|------------------------|------------------------------------------------|------------------|
| `DEVIN_API_KEY_V3`     | Devin service user API key (`cog_*`)           | Phases 1-3       |
| `DEVIN_API_KEY_V1`     | Devin v1 API key (`apk_*`)                     | Phases 1-3       |
| `DEVIN_ORG_ID`         | Devin organization ID (`org-*`)                | Phases 1-3       |
| `NOTION_API_KEY`       | Notion integration API token                   | Phase 4 (report) |
| `NOTION_PARENT_PAGE_ID`| Notion page ID for creating databases          | Phase 4 (report) |
| `SLACK_WEBHOOK_URL`    | Slack incoming webhook URL for notifications   | Phase 4 (report) |

For scheduled runs, also set a **repository variable** (Settings > Secrets and
variables > Actions > Variables):

| Variable             | Description                                   |
|----------------------|-----------------------------------------------|
| `DEFAULT_SCAN_REPO`  | Default target repo for weekly scans (e.g. `myorg/myrepo`) |

### Triggering a manual scan

1. Go to **Actions > DevinDemo Pipeline Scan > Run workflow**
2. Fill in:
   - **repo**: target repository (e.g. `owner/repo`)
   - **phases**: comma-separated list (default `scan`), e.g. `scan,validate`
   - **scan_input** (optional): path to existing scan results for
     validate/cleanup/report phases
3. Click **Run workflow**

### Weekly schedule

The workflow runs automatically every **Monday at 9:00 AM UTC**. It uses the
`DEFAULT_SCAN_REPO` variable and runs the `scan` phase only.

### Downloading artifacts

After each run, the `results/` directory is uploaded as a workflow artifact.
Go to the workflow run page and download the **pipeline-results-\*** artifact
to get the JSON output files.

### Pipeline helper script

You can also run the pipeline locally using the helper script:

```bash
# Run scan only
./scripts/run_pipeline.sh --repo owner/repo --phases scan

# Run full pipeline
./scripts/run_pipeline.sh --repo owner/repo --phases scan,validate,cleanup,report

# Run validate + report using existing scan results
./scripts/run_pipeline.sh --repo owner/repo --phases validate,report --scan-input results/scan_xxx.json
```

Required environment variables: `DEVIN_API_KEY_V3`, `DEVIN_API_KEY_V1`,
`DEVIN_ORG_ID`. For the report phase, also set `NOTION_API_KEY`,
`NOTION_PARENT_PAGE_ID`, and `SLACK_WEBHOOK_URL`.

## Project structure

```
DevinDemo/
├── main.py                     # CLI entrypoint
├── requirements.txt
├── list_sessions.py            # Standalone Devin API session lister (utility)
├── send_message.py             # v1 API message sender (Teams workaround)
├── scripts/
│   └── run_pipeline.sh         # Pipeline runner (chains phases sequentially)
├── .github/
│   └── workflows/
│       └── scan.yml            # GitHub Actions workflow (scheduled + manual)
├── src/
│   ├── api/
│   │   └── client.py           # Devin v3 API client wrapper
│   ├── scanner/
│   │   └── identifier.py       # Part 1: Identification (implemented)
│   ├── validator/
│   │   └── validator.py        # Part 2: Validation (implemented)
│   ├── cleanup/
│   │   └── cleanup.py          # Part 3: Cleanup PR generation (stub)
│   └── reporter/
│       ├── reporter.py         # Part 4: Reporting orchestrator
│       ├── notion_reporter.py  # Notion database integration
│       └── slack_notifier.py   # Slack webhook notifications
├── web/
│   ├── app.py                  # Flask + SocketIO web server
│   └── templates/
│       └── index.html          # Single-page GUI
└── results/                    # Output files from pipeline runs
```

## Assumptions & limitations

These are documented so that contributors and users know the current scope.

### Target languages
- **Python only** — the scanner currently analyses `.py` files.  Support for
  other languages (JS/TS, Java, Go, etc.) is planned for future iterations.

### Feature flag detection
- **General patterns only** — we do NOT integrate with specific flag management
  systems (LaunchDarkly, Unleash, Split, etc.).  Instead we look for:
  - Environment variable checks that gate behaviour (`os.environ.get("FEATURE_…")`)
  - Boolean configuration variables used in `if`/`else` branches
  - Functions whose purpose is to check whether a feature is enabled
  - Constants or settings that act as on/off switches
- **Staleness signals** are prioritized in Phase 1: hardcoded constants with no
  dynamic override, always-true/false branches, and flag names referencing old
  versions or dates (e.g. `ENABLE_V2_MIGRATION`, `USE_NEW_AUTH_2023`).
- This means some flags may be missed if they use unconventional patterns, and
  some false positives are expected.  Part 2 (validation) will reduce noise.

### Dead code detection
- Unreachable branches (`if False:`, `if 0:`, always-true guards)
- Functions/classes with **strong unused signals**: private (prefixed with `_`)
  and never referenced in the same file, or in modules never imported. Framework
  handlers, CLI commands, and test fixtures are intentionally skipped — Phase 2
  performs full reachability analysis.
- Unused imports are **deprioritized** — only flagged when the import is from a
  non-existent or deprecated module. Routine unused imports are better handled
  by linters.
- Large blocks of commented-out code (≥3 lines of former source code, not docs)

### Tech debt detection
- `TODO` / `FIXME` / `HACK` / `XXX` comments — **prioritises actionable items**
  with staleness signals: ticket references, past version numbers, dates, or
  phrases like "remove after" / "temporary workaround". Generic TODOs with no
  actionable context are skipped.
- Deprecated stdlib or third-party API usage (e.g. `optparse`, `imp`)
- Python version compatibility shims (`sys.version` checks, `six` usage,
  `try/except` import patterns for merged stdlib modules)

### Devin API
- All four stages use the **Devin v3 API** with service user credentials.
- **Part 1** (identify): smart Devin session scan — uses staleness signals and
  strong unused indicators to flag candidates quickly. May include false
  positives, which Phase 2 filters out.
- **Part 2** (validate): deep-dive Devin sessions with **category-aware** Layer 1
  validation. Commented-out code is verified as former source (not docs),
  tech debt markers are checked for actionability, and feature flags are
  confirmed as actual flag patterns. 8 layers total, slower but thorough.
- **Part 3** (cleanup): Devin sessions that generate cleanup PRs.
- **Part 4** (report): Devin sessions that publish summaries.
- The `org_id` **must be provided explicitly** in the URL path — despite the
  docs stating it can be omitted for org-scoped service users, omitting it
  returns 404.
- Structured output (`structured_output_schema`) is used to get parseable
  JSON results from Devin sessions.

## Utilities

### `list_sessions.py`

A standalone script to list all Devin sessions in your organization.
See `python list_sessions.py --help` for usage.
