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
| **1. Identify** | Implemented | Scan a repo for feature flags, dead code, and tech debt |
| **2. Validate** | Stub | Confirm findings are truly legacy (git blame, flag status) |
| **3. Cleanup**  | Stub | Generate PRs that remove dead code and simplify branches |
| **4. Report**   | Stub | Publish summaries to Notion, Slack, or GitHub Issues |

## Prerequisites

- **Python 3.10+**
- **pip** dependencies: `pip install -r requirements.txt`
- A Devin **service user** API key (starts with `cog_`) — create one in **Settings > Service users**
- Your Devin **organization ID** (starts with `org-`) — find it in **Settings > General**

> **Note:** Legacy keys (`apk_user_*`, `apk_*`) do **not** work with the v3 API.
> See the [authentication docs](https://docs.devin.ai/api-reference/authentication) for details.

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Run a scan
python main.py --api-key <YOUR_COG_KEY> --org-id <YOUR_ORG_ID> scan owner/repo
```

## Usage

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

### Examples

Scan a repository and print results to stdout:

```bash
python main.py --api-key cog_xxx --org-id org-xxx scan myorg/myrepo
```

Scan and save full JSON results to a file:

```bash
python main.py --api-key cog_xxx --org-id org-xxx scan myorg/myrepo -o results.json
```

## Sample output

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

## Project structure

```
DevinDemo/
├── main.py                     # CLI entrypoint
├── requirements.txt
├── list_sessions.py            # Standalone Devin API session lister (utility)
├── src/
│   ├── api/
│   │   └── client.py           # Devin v3 API client wrapper
│   ├── scanner/
│   │   └── identifier.py       # Part 1: Identification (implemented)
│   ├── validator/
│   │   └── validator.py        # Part 2: Validation (stub)
│   ├── cleanup/
│   │   └── cleanup.py          # Part 3: Cleanup PR generation (stub)
│   └── reporter/
│       └── reporter.py         # Part 4: Reporting (stub)
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
- This means some flags may be missed if they use unconventional patterns, and
  some false positives are expected.  Part 2 (validation) will reduce noise.

### Dead code detection
- Unreachable branches (`if False:`, `if 0:`, always-true guards)
- Unused functions/classes (defined but never called)
- Unused imports
- Large blocks of commented-out code (≥3 lines)

### Tech debt detection
- `TODO` / `FIXME` / `HACK` / `XXX` comments
- Deprecated stdlib or third-party API usage
- Python version compatibility shims (`sys.version` checks, `six` usage)

### Devin API
- All four stages use the **Devin v3 API** with service user credentials.
- The `org_id` **must be provided explicitly** in the URL path — despite the
  docs stating it can be omitted for org-scoped service users, omitting it
  returns 404.
- Structured output (`structured_output_schema`) is used to get parseable
  JSON results from Devin sessions.

## Utilities

### `list_sessions.py`

A standalone script to list all Devin sessions in your organization.
See `python list_sessions.py --help` for usage.
