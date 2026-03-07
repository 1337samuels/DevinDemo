# DevinDemo — Feature Flag & Tech Debt Scanner

A Devin-powered automation that scans Python codebases for stale feature flags,
dead code, and tech debt — then validates findings, generates cleanup PRs, and
reports results to Notion / Slack.

## How it works

The tool uses the [Devin v3 API](https://docs.devin.ai/api-reference/v3/usage-examples)
to create AI-powered sessions that analyse a target repository.  The pipeline
has four stages:

| Stage | Description |
|-------|-------------|
| **1. Scan** | Fast identification — flag potential feature flags (with staleness signals), dead code, actionable tech debt |
| **2. Validate** | Deep 8-layer verification of each finding (slow, thorough) |
| **3. Cleanup PR's** | Generate PRs that remove dead code and simplify branches |
| **4. Report** | Publish summaries to Notion, Slack, or GitHub Issues |

## Prerequisites

- **Python 3.10+**
- **Runtime dependencies:** `pip install -r requirements.txt`
- **Test dependencies:** `pip install -r requirements-dev.txt`
- A Devin **service user** API key (starts with `cog_`) — create one in **Settings > Service users**
- A Devin **legacy API key** (starts with `apk_`) — create one in **Settings > API keys**
- Your Devin **organization ID** (starts with `org-`) — find it in **Settings > General**

### Authentication

| Key type | Prefix | Where to create | Used for |
|----------|--------|-----------------|----------|
| Service user key | `cog_*` | **Settings > Service users** | v3 API (session creation, polling) |
| Legacy API key | `apk_*` | **Settings > API keys** | v1 API (`send_message`, session details) |

> **Note:** `cog_*` keys do **not** work with the v1 API, and `apk_*` keys do **not** work with the v3 API.
> See the [authentication docs](https://docs.devin.ai/api-reference/authentication) for details.

### `secrets.txt` (recommended)

Instead of passing API keys on every command, create a `secrets.txt` file in the
project root.  The CLI will automatically load it and fill in any missing flags.

```text
API_V3_KEY = "cog_..."
API_V1_KEY = "apk_..."
ORG_ID = "org-..."

NOTION_SECRET = "ntn_..."
NOTION_MASTER_PAGE_ID = "31b37812..."
SLACK_WEBHOOK_URL = "https://hooks.slack.com/..."
```

| Key                    | Maps to CLI flag           | Phases |
|------------------------|----------------------------|--------|
| `API_V3_KEY`           | `--api-key`                | 1-3    |
| `API_V1_KEY`           | `--v1-api-key`             | 1-3    |
| `ORG_ID`               | `--org-id`                 | 1-3    |
| `NOTION_SECRET`        | `--notion-api-key`         | 4      |
| `NOTION_MASTER_PAGE_ID`| `--notion-parent-page-id`  | 4      |
| `SLACK_WEBHOOK_URL`    | `--slack-webhook-url`      | 4      |

> **Important:** `secrets.txt` is listed in `.gitignore` and must **never** be
> committed.  CLI flags always take precedence over `secrets.txt` values.

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Run a scan (with secrets.txt configured)
python main.py scan owner/repo

# Or pass credentials explicitly
python main.py --api-key <COG_KEY> --v1-api-key <APK_KEY> --org-id <ORG_ID> scan owner/repo
```

## Usage

```bash
python main.py <command> [options]
```

### Global arguments (phases 1-3)

| Argument       | Description |
|----------------|-------------|
| `--api-key`    | Devin service user API key (`cog_*`) |
| `--v1-api-key` | Devin legacy API key (`apk_*`) |
| `--org-id`     | Devin organization ID (`org-*`) |

### Commands

#### `scan` — Identify feature flags & tech debt (Phase 1)

```bash
python main.py scan owner/repo [options]
```

| Option            | Description                                  | Default |
|-------------------|----------------------------------------------|---------|
| `repo`            | GitHub repository in `owner/repo` format     | —       |
| `-o`, `--output`  | Write full JSON results to a file            | auto    |
| `--batch-size`    | Max files per batch scan session             | 10      |
| `--poll-interval` | Seconds between status polls                 | 15      |
| `--poll-timeout`  | Max seconds to wait for session completion   | 900     |
| `--max-acu`       | Optional ACU cap for the Devin session       | —       |

#### `validate` — Deep verification of findings (Phase 2)

```bash
python main.py validate <scan_results.json> [options]
```

| Option              | Description                                    | Default |
|---------------------|------------------------------------------------|---------|
| `input`             | Path to the Phase 1 scan results JSON          | —       |
| `-o`, `--output`    | Write validated results to a file              | auto    |
| `--layers`          | Comma-separated validation layer numbers       | all     |
| `--max-batch-size`  | Max candidates per Devin session               | 5       |
| `--staleness-days`  | Staleness threshold in days                    | 365     |
| `--pr-lookback-days`| PR lookback period in days                     | 90      |
| `--issue-lookback-days` | Issue lookback period in days              | 180     |
| `--poll-interval`   | Seconds between status polls                   | 15      |
| `--poll-timeout`    | Max seconds to wait per session                | 900     |
| `--max-acu`         | Optional ACU cap per Devin session             | —       |

#### `cleanup` — Generate cleanup PRs (Phase 3)

```bash
python main.py cleanup <validated_results.json> [options]
```

| Option            | Description                                  | Default |
|-------------------|----------------------------------------------|---------|
| `input`           | Path to the Phase 2 validated findings JSON  | —       |
| `-o`, `--output`  | Write cleanup results to a file              | auto    |
| `--auto-merge`    | Auto-merge PRs that passed all layers        | off     |
| `--poll-interval` | Seconds between status polls                 | 15      |
| `--poll-timeout`  | Max seconds to wait per finding              | 900     |
| `--max-acu`       | Optional ACU cap for the Devin session       | —       |

#### `report` — Publish findings (Phase 4)

```bash
python main.py report --input <validated_results.json> [options]
```

| Option                   | Description                              | Default |
|--------------------------|------------------------------------------|---------|
| `-i`, `--input`          | Path to the Phase 2 validated findings   | —       |
| `-o`, `--output`         | Write report metadata to a file          | —       |
| `--cleanup-results`      | Path to Phase 3 cleanup results (optional) | —     |
| `--notion-api-key`       | Notion integration API token             | —       |
| `--notion-database-id`   | Existing Notion database ID              | —       |
| `--notion-parent-page-id`| Notion page ID for new databases         | —       |
| `--slack-webhook-url`    | Slack incoming webhook URL               | —       |

### Examples

```bash
# Full pipeline: scan -> validate -> cleanup -> report
python main.py scan myorg/myrepo
python main.py validate results/scan_myorg_myrepo_20260307_120000.json
python main.py cleanup results/validate_myorg_myrepo_20260307_121500.json
python main.py report --input results/validate_myorg_myrepo_20260307_121500.json \
    --cleanup-results results/cleanup_myorg_myrepo_20260307_123000.json

# Or use the pipeline helper script
./scripts/run_pipeline.sh --repo myorg/myrepo --phases scan,validate,cleanup,report
```

Output files are written to `results/` with timestamped filenames by default
(e.g. `results/scan_owner_repo_20260307_120000.json`).

### Output JSON structure

Each finding includes fields for Phase 2 hand-off:

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

Phase 2 iterates through each finding by `id`, performs deep verification,
and updates `verification_status` to `"verified"`, `"false_positive"`, or `"needs_review"`.

## Why use the v1 API for sending messages?

The v3 API's `send_message` endpoint requires the `ManageOrgSessions` permission, which is only available to **Enterprise** accounts with `cog_*` service user keys.

If you have a **Teams** account, you can use the [v1 `send_message` endpoint](https://docs.devin.ai/api-reference/v1/sessions/send-a-message-to-an-existing-devin-session) instead. It accepts Personal API Keys (`apk_user_*`) and Service API Keys (`apk_*`), which are available on all account tiers.

The pipeline handles this automatically — it uses the v3 API for session creation and polling, and the v1 API (via the `--v1-api-key` flag) for `send_message()` calls.

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

- **Tab navigation** across all four phases (Scan, Validate, Cleanup PR's, Report)
- **Dashboard** with 2x2 console grid for monitoring all phases simultaneously
- **"Run All"** button to execute the full pipeline sequentially from the Dashboard
- **Form inputs** for all CLI arguments, with password-masked fields for API keys
- **Live console output** streamed in real time via WebSocket
- **Stop button** to terminate a running process
- **File discovery dropdowns** — phases that require input files automatically
  discover matching results from the `results/` directory

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
├── main.py                        # CLI entrypoint — all 4 phases
├── requirements.txt               # Runtime dependencies
├── requirements-dev.txt           # Test dependencies
├── scripts/
│   └── run_pipeline.sh            # Pipeline runner (chains phases sequentially)
├── .github/
│   └── workflows/
│       ├── scan.yml               # GitHub Actions workflow (scheduled + manual)
│       └── scan-hourly.yml        # Hourly scan workflow
├── src/
│   ├── api/
│   │   └── client.py              # Devin API client (v3 + v1)
│   ├── scanner/
│   │   └── identifier.py          # Phase 1: Identification
│   ├── validator/
│   │   └── validator.py           # Phase 2: 8-layer validation
│   ├── cleanup/
│   │   └── cleanup.py             # Phase 3: Cleanup PR generation
│   ├── reporter/
│   │   ├── reporter.py            # Phase 4: Reporting orchestrator
│   │   ├── notion_reporter.py     # Notion database integration
│   │   └── slack_notifier.py      # Slack webhook notifications
│   └── tracking/
│       └── acu_tracker.py         # ACU usage tracking
├── web/
│   ├── app.py                     # Flask + SocketIO web server
│   └── templates/
│       ├── landing.html           # Landing page with pipeline overview
│       └── index.html             # Dashboard GUI
├── tests/                         # Unit tests (pytest)
│   ├── conftest.py
│   ├── test_api_client.py
│   ├── test_scanner.py
│   ├── test_validator.py
│   ├── test_cleanup.py
│   ├── test_reporter.py
│   └── test_web.py
└── results/                       # Output files (git-ignored)
```

## Testing

```bash
pip install -r requirements-dev.txt
pytest
pytest --cov=src --cov-report=term-missing
```

## Assumptions & limitations

### Target languages
- **Python only** — the scanner analyses `.py` files.  Support for
  other languages is planned for future iterations.

### Feature flag detection
- **General patterns only** — no integration with specific flag management
  systems (LaunchDarkly, Unleash, etc.).  Detection targets:
  - Environment variable checks gating behaviour (`os.environ.get("FEATURE_...")`)
  - Boolean config variables in `if`/`else` branches
  - Functions that check whether a feature is enabled
  - Constants/settings acting as on/off switches
- **Staleness signals** are prioritised: hardcoded constants with no dynamic
  override, always-true/false branches, and flag names referencing old versions
  or dates (e.g. `ENABLE_V2_MIGRATION`, `USE_NEW_AUTH_2023`).
- Phase 2 (validation) reduces false positives from Phase 1.

### Dead code detection
- Unreachable branches (`if False:`, `if 0:`, always-true guards)
- Functions/classes with **strong unused signals**: private (prefixed `_`)
  and never referenced, or in modules never imported
- Framework handlers, CLI commands, and test fixtures are intentionally
  skipped — Phase 2 performs full reachability analysis
- Unused imports are **deprioritised** — only flagged when from non-existent
  or deprecated modules
- Large blocks of commented-out code (3+ lines of former source code)

### Tech debt detection
- `TODO` / `FIXME` / `HACK` / `XXX` comments — prioritises **actionable items**
  with staleness signals (ticket references, past version numbers, dates)
- Deprecated stdlib or third-party API usage (e.g. `optparse`, `imp`)
- Python version compatibility shims (`sys.version` checks, `six` usage)

### Devin API
- All four stages use the **Devin v3 API** for session creation and polling,
  and the **v1 API** for `send_message()` and session details.
- The `org_id` **must be provided explicitly** — omitting it returns 404.
- Structured output (`structured_output_schema`) is used to get parseable
  JSON results from Devin sessions.
