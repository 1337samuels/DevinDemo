# DevinDemo

A sample Python script that uses the [Devin API](https://docs.devin.ai/api-reference/v1/sessions/list-sessions) to list all sessions in your organization.

## Prerequisites

- Python 3.7+
- A Devin API key (personal `apk_user_*` or service `apk_*`)

No external dependencies are required — the script uses only the Python standard library.

## Usage

```bash
python list_sessions.py <YOUR_DEVIN_API_KEY>
```

### Optional arguments

| Argument     | Description                                | Default |
|--------------|--------------------------------------------|---------|
| `--limit`    | Maximum number of sessions to return       | 100     |
| `--offset`   | Number of sessions to skip (pagination)    | 0       |

### Examples

List the first 100 sessions:

```bash
python list_sessions.py apk_user_abc123
```

List 10 sessions starting from offset 20:

```bash
python list_sessions.py apk_user_abc123 --limit 10 --offset 20
```

## Sample output

```
Found 3 session(s):

  [finished] Fix login bug
    ID:      abc-123
    Created: 2026-03-01T12:00:00Z
    Updated: 2026-03-01T13:00:00Z
    PR: https://github.com/org/repo/pull/42

  [working] Add dark mode
    ID:      def-456
    Created: 2026-03-05T09:00:00Z
    Updated: 2026-03-05T09:30:00Z

  [blocked] Refactor auth module
    ID:      ghi-789
    Created: 2026-03-06T08:00:00Z
    Updated: 2026-03-06T08:15:00Z
```
