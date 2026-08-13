# HHabitica — Habitica Telegram Bot

HHabitica is an unofficial Telegram bot for managing Habitica Habits, Dailies,
Todos, rewards, status, reminders, and avatars. It supports local polling for
development and a synchronous Flask/WSGI deployment designed for PythonAnywhere.

Try the public instance at [@HHabitica_bot](https://t.me/HHabitica_bot). This is a
fan project and is not affiliated with or endorsed by Habitica.

This repository currently contains the existing Telegram bot only. A Telegram Mini
App is intentionally outside this stabilization milestone.

## Features and commands

- Private account linking with persistent Habitica credentials.
- Habit, Daily, and Todo scoring; completed Todo restoration.
- Custom reward and health-potion purchases.
- Inline panels, reply keyboards, and a pinned private status message.
- Guided Habitica “Refresh Day”/cron flow.
- Habitica task reminders delivered to a DM, group, or forum topic.
- Local avatar rendering through Node.js, Puppeteer, and a tracked browser bundle.
- Flask webhook and authenticated reminder-tick endpoints for PythonAnywhere.

| Command | Purpose |
| --- | --- |
| `/start`, `/relink`, `/cancel` | Link, replace, or cancel linking a Habitica account |
| `/status` | Refresh stats and the pinned status message |
| `/habits`, `/dailys`, `/todos` | Open interactive task panels |
| `/completedtodos`, `/rewards` | Restore completed Todos or purchase custom rewards |
| `/buy_potion`, `/refresh_day` | Buy a potion or run the guided day refresh |
| `/add_todo`, `/task_list` | Create a Todo or show a text task list |
| `/inline`, `/menu`, `/menu_rk` | Open inline and reply-keyboard launchers |
| `/notify_here`, `/reminder_status` | Configure reminder destination and status display |
| `/avatar` | Render and send the current Habitica avatar |
| `/sync_commands`, `/debug` | Refresh Telegram commands or show sanitized diagnostics |

Account linking is accepted only in a private chat. Credential messages are deleted
on a best-effort basis after capture, but the final credentials remain encrypted in
transit by Telegram and stored locally in the bot's pickle persistence file.

## Requirements

- Linux or macOS. Production locking uses `fcntl`, as available on PythonAnywhere.
  On Windows, use WSL for the current codebase.
- Python 3.10 or newer.
- Node.js 18 or newer for avatar rendering; Node.js 20+ is recommended. The rest of
  the bot works without Node and falls back when no rendered avatar is available.
- A Telegram bot token from [@BotFather](https://t.me/BotFather).
- A Habitica User ID and API Token, entered later through `/start` in a private chat.

Runtime Python dependencies are bounded in `requirements.txt`. Development tools are
in `requirements-dev.txt`. Node dependencies and exact transitive versions are locked
in `package-lock.json`.

## Installation

```bash
git clone https://github.com/hosseinhayati128/Habitica-Telegram-Bot.git
cd Habitica-Telegram-Bot

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For tests and linting:

```bash
python -m pip install -r requirements-dev.txt
```

For optional avatar rendering, install the lockfile exactly:

```bash
npm ci
npm test
```

Puppeteer's pinned install script downloads its compatible Chrome headless-shell
build during `npm ci`; make sure the host has enough disk space and allows the
download. The package script is explicitly allowlisted for current npm releases.

## Configuration

The application reads the following environment variables. It does not automatically
load a `.env` file.

| Variable | Required | Default and behavior |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes | BotFather token used by polling and WSGI. |
| `BOT_DATA_PATH` | Recommended in production | `botdata.pkl` in the process working directory. Use an absolute path on WSGI hosts. |
| `TELEGRAM_WEBHOOK_SECRET` | Optional for compatibility; strongly recommended | When set, `/telegram-webhook` requires Telegram's matching `X-Telegram-Bot-Api-Secret-Token` header. If unset, legacy webhook requests remain accepted. |
| `TICK_TOKEN` | Required to use `/tick` | Shared secret for the reminder endpoint. An unset value denies every tick request. |
| `ALLOW_LEGACY_TICK_QUERY_TOKEN` | Migration only | Defaults to `true`, allowing legacy `/tick?token=...`. Set to `false` after moving the scheduler to a header. |
| `RUNTIME_LOCK_TIMEOUT_SECONDS` | No | `2.0`. Nonnegative seconds to wait for the cross-process persistence lock, capped at 30. Invalid values use the default. |
| `REMINDER_WINDOW_SECONDS` | No | `60`, constrained to 1–3600 seconds. Controls the reminder matching window. |
| `AVATAR_RENDER_TIMEOUT_SECONDS` | No | `45`; positive values are capped at 120 seconds. |
| `NODE_BIN` | No | Explicit Node executable name/path. Otherwise `node`, `nodejs`, and common NVM paths are searched. |

Never commit real values. `.gitignore` excludes common secret files, pickle data,
runtime locks, caches, and avatar output.

## Local development with polling

Set at least the Telegram token and preferably an absolute development data path:

```bash
export TELEGRAM_BOT_TOKEN='replace-with-your-token'
export BOT_DATA_PATH="$PWD/.runtime/dev-botdata.pkl"
mkdir -p .runtime
chmod 700 .runtime
python habitica_bot.py
```

If this bot was previously configured for webhook delivery, delete the webhook before
starting polling; Telegram does not permit webhook delivery and `getUpdates` polling
for the same bot at the same time:

```bash
curl --fail --silent --show-error \
  --request POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook" \
  --data-urlencode 'drop_pending_updates=false'
```

Running `habitica_bot.py` builds the application, synchronizes commands, and calls
`run_polling()`. The external `/tick` endpoint is not started by polling, so scheduled
reminders require the WSGI deployment or another deliberate tick integration.

Do not run polling and WSGI simultaneously against the same `BOT_DATA_PATH`.

## PythonAnywhere Flask/WSGI deployment

The WSGI entry point is `webhook_app.flask_app`. It exposes:

- `POST /telegram-webhook` for Telegram JSON updates (maximum body: 1 MiB).
- `GET /tick` for authenticated reminder checks.

Both routes serialize their complete persistence transaction with a Linux advisory
lock next to the pickle file. Lock contention returns HTTP 503 rather than allowing
two workers to overwrite each other's state.

### 1. Install and prepare private state

In a PythonAnywhere Bash console, create a virtual environment supported by the web
app and install the runtime dependencies:

```bash
cd /home/<username>/Habitica-Telegram-Bot
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p /home/<username>/.local/share/habitica-telegram-bot
chmod 700 /home/<username>/.local/share/habitica-telegram-bot
```

Use the Python version actually enabled for the PythonAnywhere web app if it is newer
than 3.10. Configure the web app to use this virtual environment.

### 2. Configure the private WSGI file

Set secrets in PythonAnywhere's private WSGI configuration or another host-provided
environment mechanism, never in this repository. A minimal WSGI file is:

```python
import os
import sys

PROJECT_ROOT = "/home/<username>/Habitica-Telegram-Bot"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ["TELEGRAM_BOT_TOKEN"] = "replace-on-host"
os.environ["BOT_DATA_PATH"] = (
    "/home/<username>/.local/share/habitica-telegram-bot/botdata.pkl"
)
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "replace-on-host"
os.environ["TICK_TOKEN"] = "replace-on-host"
os.environ["ALLOW_LEGACY_TICK_QUERY_TOKEN"] = "false"

from webhook_app import flask_app as application
```

Generate independent high-entropy webhook and tick values. Do not reuse the bot token
or a Habitica key. Reload the web app after changing its environment.

### 3. Register the Telegram webhook manually

Setting `TELEGRAM_WEBHOOK_SECRET` in the application is only half of the setup.
Telegram must also be told to send the same secret. With the values exported in a
private shell session:

```bash
export WEBHOOK_URL='https://<username>.pythonanywhere.com/telegram-webhook'

curl --fail --silent --show-error \
  --request POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=${WEBHOOK_URL}" \
  --data-urlencode "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
```

For an existing deployment, a low-interruption migration is:

1. Generate a webhook secret.
2. Call `setWebhook` with `secret_token` while compatibility mode is still active.
3. Set the identical `TELEGRAM_WEBHOOK_SECRET` on PythonAnywhere and reload.
4. Confirm webhook delivery, then keep the secret in both configurations.

If the application variable is configured but Telegram's webhook is not updated with
the same value, every webhook request receives HTTP 403.

## Reminder tick authentication and migration

New scheduler jobs should send the token in a header, preferably:

```bash
curl --fail --silent --show-error \
  --header "Authorization: Bearer ${TICK_TOKEN}" \
  'https://<username>.pythonanywhere.com/tick'
```

`X-Tick-Token: <token>` is also accepted. Schedule the endpoint approximately once per
minute. Avoid overlapping runs; a busy runtime lock returns HTTP 503 with a short
`Retry-After` hint.

Older jobs using `/tick?token=...` continue to work while
`ALLOW_LEGACY_TICK_QUERY_TOKEN` is true. Query strings commonly enter access logs, so
migrate deliberately:

1. Change the scheduler to `Authorization: Bearer` or `X-Tick-Token` and verify HTTP
   200 using the existing token.
2. Set `ALLOW_LEGACY_TICK_QUERY_TOKEN=false` and reload the web app.
3. Rotate `TICK_TOKEN` because the previous value may exist in URL logs, then update
   the scheduler's secret header.

If `TICK_TOKEN` is missing or mismatched, `/tick` always returns HTTP 403. Tick responses
contain only aggregate counts, not user IDs, task content, or credentials.

## Persistence, backup, and concurrency

`PicklePersistence` stores account credentials, conversation state, layouts, reminder
deduplication state, and pinned-message IDs. Treat the file as a secret:

- Set `BOT_DATA_PATH` to an absolute path outside the repository and web-served paths.
- Make its directory mode `0700` and the pickle/backups mode `0600`.
- Never open a pickle from an untrusted source; pickle loading can execute code.
- Never commit the pickle, lock file, backups, or copied production state.

The WSGI routes use `<BOT_DATA_PATH>.lock` to serialize webhook and tick transactions
across worker processes on the same Linux host. This does not make pickle a distributed
database and does not coordinate another machine or an independently started polling
process.

For a consistent backup, stop or quiesce webhook/tick processing first, copy the file
to a private non-web directory, restrict it to `0600`, then resume processing. Do not
copy while a worker may be writing. Test restoration only with a trusted copy and a
separate `BOT_DATA_PATH`.

## Avatar renderer and tracked bundle

Production avatar rendering works as follows:

1. Python fetches the authenticated Habitica profile.
2. Only the avatar fields needed by the renderer are written to a private temporary
   JSON file; Habitica credentials are not passed to Node.
3. `render_avatar_from_json.js` uses Puppeteer and
   `habitica-avatar.bundle.js` to produce a PNG.
4. Python validates and atomically caches the PNG under the ignored `Avatar/` folder.

`habitica-avatar.bundle.js` is generated but intentionally tracked because production
needs it at runtime and should not have to rebuild assets during deployment. Do not
edit the bundle manually. Rebuild it only after changing the `habitica-avatar`
dependency or the bundling source:

```bash
npm ci
npm run build:avatar
npm test
git diff -- habitica-avatar.bundle.js package.json package-lock.json
```

Review and commit the generated diff together with its source/dependency change.
`render_avatar.js` is a legacy developer utility; normal bot execution uses
`render_avatar_from_json.js` and does not put Habitica credentials on a Node command
line.

## Tests and lint

The Python tests mock Telegram, Habitica, subprocess, and WSGI boundaries; they must
not contact real services.

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest
python -m ruff check .

npm ci
npm test
```

Use `npm run build:avatar` only when intentionally regenerating the tracked bundle.
Before committing, review `git status` and ensure no pickle, token, `.env`, avatar
payload, or runtime output is staged.

## Troubleshooting

- **Polling reports a Telegram conflict:** remove the registered webhook and ensure no
  second polling process is running for the same bot token.
- **Webhook returns 403:** compare `TELEGRAM_WEBHOOK_SECRET` with the `secret_token`
  passed to `setWebhook`. Do not print either value in logs.
- **Webhook returns 415 or 400:** Telegram must POST a JSON object containing an integer
  `update_id`. Proxies must preserve the JSON content type and body.
- **Webhook returns 413:** a proxy or caller sent more than the 1 MiB request limit.
- **Webhook or tick returns 503:** another worker owns the persistence lock or the
  transaction failed. Prevent overlapping jobs; increase `RUNTIME_LOCK_TIMEOUT_SECONDS`
  only after checking for stuck/duplicate workers.
- **Tick returns 403:** set `TICK_TOKEN` and send it in an accepted header. If legacy
  query authentication was disabled, `?token=` is intentionally ignored.
- **State resets after reload:** set an absolute `BOT_DATA_PATH`, ensure its parent is
  writable by the web app, and confirm polling/WSGI are not using different files.
- **Avatar says Node is unavailable:** run `npm ci`, set `NODE_BIN` to an executable
  Node path if auto-detection fails, and verify Puppeteer's Chromium can start.
- **Avatar times out or returns no PNG:** check host resource limits and optionally
  adjust `AVATAR_RENDER_TIMEOUT_SECONDS` up to 120 seconds. Renderer logs are sanitized;
  reproduce locally for detailed diagnosis rather than logging profile JSON.
- **PythonAnywhere import fails:** verify the source directory, virtualenv, selected
  Python version, and WSGI `sys.path`, then reload the web app.
- **Habitica actions fail:** verify credentials through `/relink` in a private chat.
  Never paste credentials into an issue, log, screenshot, or group chat.

## Security and privacy

- Telegram and Habitica secrets come from environment variables or private persisted
  user data; no secrets belong in source control.
- Habitica API calls use bounded timeouts, validated response envelopes, and no blind
  retry of non-idempotent mutations.
- Webhook and tick secrets use timing-safe comparisons.
- Raw Telegram updates and raw Habitica response bodies are not normal log content.
- The bot intentionally remains a local-pickle deployment for this milestone. Protect
  the host account, backups, WSGI file, and state directory accordingly.

## Known limitations

- `habitica_bot.py` remains a large, tightly coupled handler module. The avatar and
  webhook/runtime-lock boundaries were extracted because they are cohesive; a broad
  handler rewrite was intentionally deferred.
- The Habitica client is synchronous. Requests now have strict connect/read timeouts,
  and reminder/avatar work is offloaded from the event loop, but some legacy polling
  handlers can still pause other updates while waiting for Habitica. An async adapter
  should be a separately tested follow-up rather than a mechanical rewrite.
- Webhook update IDs are persisted to suppress normal Telegram redelivery. A hard
  process crash after an external Habitica mutation but before the journal is flushed
  can still leave an ambiguous replay; solving that fully needs a durable transactional
  design beyond a local pickle.
- The runtime lock coordinates only processes on one Linux host that use these WSGI
  routes and the same `BOT_DATA_PATH`; it does not coordinate polling or another host.
- `PicklePersistence` remains a non-database file format; the lock prevents concurrent
  writers but cannot make a write crash-proof. Keep private, quiesced backups as
  described above.

## License

MIT. See `LICENSE`.
