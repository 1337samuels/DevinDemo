# DevinDemo

Python utilities for the [Devin API](https://docs.devin.ai/api-reference/overview):

- **`list_sessions.py`** -- List all sessions in your organization (v3 API, requires `cog_` service user key).
- **`send_message.py`** -- Send a message to an existing session (v1 API, works with Teams accounts using `apk_user_*` / `apk_*` keys).

## Prerequisites

- Python 3.10+
- No external dependencies — both scripts use only the Python standard library.

### Authentication

| Script              | Key type                                  | Where to create                           |
|---------------------|-------------------------------------------|-------------------------------------------|
| `list_sessions.py`  | Service user key (`cog_*`)                | **Settings > Service users**              |
| `send_message.py`   | Personal key (`apk_user_*`) or Service key (`apk_*`) | **Settings > API keys** |

> **Note:** `cog_*` keys do **not** work with the v1 API, and `apk_*` keys do **not** work with the v3 API.
> See the [authentication docs](https://docs.devin.ai/api-reference/authentication) for details.

## Usage

### list_sessions.py

```bash
python list_sessions.py <YOUR_DEVIN_API_KEY> --org-id <YOUR_ORG_ID>
```

#### Required arguments

| Argument       | Description                                                                          |
|----------------|--------------------------------------------------------------------------------------|
| `api_key`      | Your Devin service user API key (starts with `cog_`)                                 |
| `--org-id`     | Your Devin organization ID (starts with `org-`). Find it in **Settings > General**   |

#### Optional arguments

| Argument       | Description                                          | Default |
|----------------|------------------------------------------------------|---------|
| `--first`      | Max number of sessions to return per page (max 200)  | 100     |
| `--all-pages`  | Automatically fetch every page of results            | off     |

#### Examples

List the first 100 sessions:

```bash
python list_sessions.py cog_your_key_here --org-id org-your_org_id_here
```

List 10 sessions per page:

```bash
python list_sessions.py cog_your_key_here --org-id org-your_org_id_here --first 10
```

Fetch all sessions across every page:

```bash
python list_sessions.py cog_your_key_here --org-id org-your_org_id_here --all-pages
```

### send_message.py

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

### list_sessions.py

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

### send_message.py

```
Sending message to session abc-123-def-456 ...
Message sent successfully.
```

## Why use the v1 API for sending messages?

The v3 API's `send_message` endpoint requires the `ManageOrgSessions` permission, which is only available to **Enterprise** accounts with `cog_*` service user keys.

If you have a **Teams** account, you can use the [v1 `send_message` endpoint](https://docs.devin.ai/api-reference/v1/sessions/send-a-message-to-an-existing-devin-session) instead. It accepts Personal API Keys (`apk_user_*`) and Service API Keys (`apk_*`), which are available on all account tiers. The `send_message.py` script wraps this endpoint for convenience.
