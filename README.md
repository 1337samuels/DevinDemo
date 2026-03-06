# DevinDemo

A sample Python script that uses the [Devin v3 API](https://docs.devin.ai/api-reference/v3/usage-examples) to list all sessions in your organization.

## Prerequisites

- Python 3.10+
- A Devin **service user** API key (starts with `cog_`) — create one in **Settings > Service users**

No external dependencies are required — the script uses only the Python standard library.

> **Note:** Legacy keys (`apk_user_*`, `apk_*`) do **not** work with the v3 API.
> See the [authentication docs](https://docs.devin.ai/api-reference/authentication) for details.

## Usage

```bash
python list_sessions.py <YOUR_DEVIN_API_KEY>
```

### Optional arguments

| Argument       | Description                                          | Default |
|----------------|------------------------------------------------------|---------|
| `--first`      | Max number of sessions to return per page (max 200)  | 100     |
| `--all-pages`  | Automatically fetch every page of results            | off     |

### Examples

List the first 100 sessions:

```bash
python list_sessions.py cog_your_key_here
```

List 10 sessions per page:

```bash
python list_sessions.py cog_your_key_here --first 10
```

Fetch all sessions across every page:

```bash
python list_sessions.py cog_your_key_here --all-pages
```

## Sample output

```
Total sessions: 42
Fetched 3 session(s) (1 page(s)):

  [finished] Fix login bug
    ID:      abc-123
    Created: 2026-03-01 12:00:00 UTC
    Updated: 2026-03-01 13:00:00 UTC
    URL:     https://app.devin.ai/sessions/abc-123
    PR: https://github.com/org/repo/pull/42 (merged)

  [working] Add dark mode
    ID:      def-456
    Created: 2026-03-05 09:00:00 UTC
    Updated: 2026-03-05 09:30:00 UTC
    URL:     https://app.devin.ai/sessions/def-456

  [blocked] Refactor auth module
    ID:      ghi-789
    Created: 2026-03-06 08:00:00 UTC
    Updated: 2026-03-06 08:15:00 UTC
    URL:     https://app.devin.ai/sessions/ghi-789
```
