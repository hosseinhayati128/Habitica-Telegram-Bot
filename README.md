# HHabitica — Habitica Telegram Bot

HHabitica is an unofficial Telegram bot for managing Habitica Habits, Dailies,
Todos, rewards, status, reminders, and avatars. It supports local polling for
development and a synchronous Flask/WSGI deployment designed for PythonAnywhere.

Try the public instance at [@HHabitica_bot](https://t.me/HHabitica_bot). This is a
fan project and is not affiliated with or endorsed by Habitica.

The same Flask application serves a Telegram Mini App with a live character overview,
authenticated avatar, three appearance modes, and functional Habit, Daily, and Todo
pages. Tasks can be created, edited, scored, completed, restored, and deleted without
exposing Habitica credentials to the browser.

## Features and commands

- Private account linking with persistent Habitica credentials.
- Habit, Daily, and Todo scoring; completed Todo restoration.
- Custom reward and health-potion purchases.
- Inline panels, reply keyboards, and a pinned private status message.
- Guided Habitica “Refresh Day”/cron flow.
- Habitica task reminders delivered to a DM, group, or forum topic.
- Local avatar rendering through Node.js, Puppeteer, and a tracked browser bundle.
- Flask webhook and authenticated reminder-tick endpoints for PythonAnywhere.
- Telegram Mini App Home view with signed Telegram authentication and read-only access
  to the existing linked Habitica account.
- Authenticated Mini App task views with filters, simple task editing, checklists,
  completion, and Habit scoring.
- A startup Record Yesterday gate that follows Habitica's own `needsCron`, timezone,
  custom-day, and Daily-schedule rules before current-day scoring is enabled.
- Compact authoritative Habit counters and a deliberate Health Potion action that
  updates the shared Home/task profile without regenerating the avatar.

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
| `TELEGRAM_WEBHOOK_SECRET` | Required for webhook delivery | `/telegram-webhook` requires Telegram's matching `X-Telegram-Bot-Api-Secret-Token` header and fails closed when this value is absent. |
| `ALLOW_INSECURE_WEBHOOK_WITHOUT_SECRET` | Migration only | Defaults to `false`. A temporary `true` preserves legacy secretless delivery while `setWebhook(secret_token=...)` is configured. Never leave it enabled on an internet-facing deployment. |
| `TICK_TOKEN` | Required to use `/tick` | Shared secret for the reminder endpoint. An unset value denies every tick request. |
| `ALLOW_LEGACY_TICK_QUERY_TOKEN` | Migration only | Defaults to `true`, allowing legacy `/tick?token=...`. Set to `false` after moving the scheduler to a header. |
| `RUNTIME_LOCK_TIMEOUT_SECONDS` | No | `2.0`. Nonnegative seconds to wait for the cross-process persistence lock, capped at 30. Invalid values use the default. |
| `REMINDER_WINDOW_SECONDS` | No | `60`, constrained to 1–3600 seconds. Controls the reminder matching window. |
| `AVATAR_RENDER_TIMEOUT_SECONDS` | No | `45`; positive values are capped at 120 seconds. |
| `NODE_BIN` | No | Explicit Node executable name/path. Otherwise `node`, `nodejs`, and common NVM paths are searched. |
| `MINIAPP_AUTH_MAX_AGE_SECONDS` | No | `3600`, capped at 86400. Maximum age of signed Telegram Mini App init data. |
| `MINIAPP_DEV_MODE` | Local development only | Disabled. Set exactly `1` together with `MINIAPP_DEV_TELEGRAM_USER_ID` to open the Mini App outside Telegram. Never enable this in production. |
| `MINIAPP_DEV_TELEGRAM_USER_ID` | Local development only | Existing linked Telegram user ID used only when development mode is explicitly enabled and the auth header is absent. |
| `MINIAPP_AVATAR_REFRESH_COOLDOWN_SECONDS` | No | `30`, capped at 300. Reuses a recently rendered avatar instead of repeatedly launching Puppeteer for authenticated refresh replays. |
| `HABITICA_CLIENT_ID` | Recommended | Habitica's public `x-client` identifier in `<author Habitica UUID>-<app name>` form. It is not a secret. The historical `habitica-telegram-bot` value remains the compatibility fallback. |

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

## Mini App local development

The Mini App is served by the existing Flask application; it does not need a frontend
build step or a second web server. To preview it in an ordinary browser, use a copy of
development persistence that already contains a linked Telegram account:

```bash
export TELEGRAM_BOT_TOKEN='replace-with-your-development-token'
export BOT_DATA_PATH="$PWD/.runtime/dev-botdata.pkl"
export MINIAPP_DEV_MODE=1
export MINIAPP_DEV_TELEGRAM_USER_ID='your-linked-telegram-numeric-id'

flask --app webhook_app:flask_app run --debug
```

Open `http://127.0.0.1:5000/miniapp/`. Local dev mode is considered only when the
`Authorization` header is absent; an invalid signed header never falls back to the
development identity. Keep the mode off when testing production authentication.

The browser uses separate authenticated requests for profile JSON and the PNG, showing
the status before it begins potentially expensive avatar work. A first avatar request
can take up to the configured render timeout while Puppeteer builds the cached image;
subsequent requests reuse the existing opaque avatar cache. The refresh button requests
`/miniapp/api/avatar?refresh=1` and requests regeneration, subject to the short
server-side refresh cooldown.

Task development uses the same signed-auth boundary. In explicit local development
mode, the configured Telegram identity selects its already linked account; the browser
still never receives either Habitica credential. Lists are fetched only when their tab
is opened, and no task content is saved to LocalStorage, CloudStorage, or the pickle.
All create/edit/score/delete requests therefore operate on the development account
named by `MINIAPP_DEV_TELEGRAM_USER_ID`; use a non-production Habitica account when
testing mutations.

Every Mini App launch checks the linked account's authoritative Habitica day before it
loads profile, avatar, or interactive tasks. If Record Yesterday is required, the
blocking review submits selected Dailies and cron as real mutations. Use a disposable
Habitica account when testing this flow: local development mode relaxes only Telegram
launch authentication and does not simulate or sandbox Habitica actions.

## PythonAnywhere Flask/WSGI deployment

The WSGI entry point is `webhook_app.flask_app`. It exposes:

- `POST /telegram-webhook` for Telegram JSON updates (maximum body: 1 MiB).
- `GET /tick` for authenticated reminder checks.
- `GET /miniapp/` for the Mini App frontend.
- `GET /miniapp/api/me` for a signed, normalized Habitica character summary.
- `GET /miniapp/api/avatar` for the signed user's locally rendered PNG.
- `GET /miniapp/api/day-status` for the authoritative startup day gate.
- `POST /miniapp/api/day-refresh` to record an allowlisted set of yesterday's Dailies
  and then run Habitica cron once. Any number may be selected; the server records at
  most eight per request and returns a manual continuation before cron when more remain.
- `GET /miniapp/api/health-potion` for the normalized confirmation model and
  a short-lived account-bound `purchaseIntent`; `POST /miniapp/api/health-potion`
  consumes that intent for one deliberate purchase.
- `GET /miniapp/api/tasks?type=habit|daily|todo` for normalized active task lists;
  Todos also accept `completed=true|false`.
- `POST /miniapp/api/tasks` to create a Habit, simple weekly Daily, or Todo.
- `PATCH /miniapp/api/tasks/<uuid>` and `DELETE /miniapp/api/tasks/<uuid>` to edit or
  delete an editable personal task.
- `POST /miniapp/api/tasks/<uuid>/score` to score a Habit or change Daily/Todo
  completion.
- `POST /miniapp/api/tasks/<uuid>/checklist/<item_uuid>/score` to set the requested
  checklist completion state safely.

The webhook and tick routes serialize their complete persistence transaction with a Linux advisory
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

For an existing checkout, do not clone again. Update or upload the reviewed source
files in place, activate the virtual environment already attached to the web app, and
refresh the bounded dependencies:

```bash
cd /home/<username>/Habitica-Telegram-Bot
source .venv/bin/activate
python -m pip install -r requirements.txt
npm ci
```

Then open PythonAnywhere's **Web** tab and press **Reload** for the existing web app.
Do not start polling against the same `BOT_DATA_PATH` while WSGI is active.

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
os.environ["MINIAPP_AUTH_MAX_AGE_SECONDS"] = "3600"
os.environ["HABITICA_CLIENT_ID"] = "<public-author-habitica-uuid>-hhabitica"

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

### 4. Configure the Telegram Mini App

After the HTTPS web app is reloaded and `https://<username>.pythonanywhere.com/miniapp/`
opens successfully, configure the launch point in [@BotFather](https://t.me/BotFather):

Telegram requires the production Mini App URL to use HTTPS; local HTTP is only for
ordinary-browser development.

1. Run `/setmenubutton`, select the bot, and use a label such as `Open App`.
2. Enter `https://<username>.pythonanywhere.com/miniapp/` as the Web App URL.
3. Optionally enable the same URL as the Main Mini App under `/mybots` → the bot →
   **Bot Settings** → **Configure Mini App**.

Telegram supplies signed `initData` when the app is launched through its Menu Button
or Main Mini App. The client sends the untouched value as
`Authorization: tma <initData>`; the server verifies Telegram's bot-token HMAC and
freshness before reading persistence. No user ID from a URL, query parameter,
`initDataUnsafe`, or browser storage is accepted for authorization.

The same authentication runs before every task read and mutation. Credentials are
resolved only from private server persistence and sent to Habitica in server-side
headers; Mini App JavaScript receives only explicitly normalized task fields.

Day refresh, task scoring, checklist scoring, and Potion purchase are serialized by a
bounded set of private per-account gameplay lock shards. The service rechecks
Habitica's `needsCron` at mutation boundaries because the official Habitica clients
and another host cannot participate in a local file lock. Selected review IDs are
validated against a freshly fetched eligible Daily set, and score/cron/purchase POSTs
are never retried automatically when their outcome may be unknown.

The Telegram refresh-day panel now keeps its checkboxes local until confirmation and
submits the selected IDs through that same guarded service and gameplay lock. Ordinary
Telegram task scoring, reward purchases, and Potion purchases use that account lock too.
Potion confirmation intents are random, account-bound, valid for five minutes, and retained
only in a bounded in-memory registry. Replaying the same terminal intent returns the
same sanitized result without making a second Habitica purchase. Restarting the web
worker safely invalidates outstanding intents; reopen the confirmation to get a new
one.

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

Mini App credential lookup creates a fresh `PicklePersistence` adapter and uses the
same lock only long enough to read and copy one user's linked credentials. It never
flushes or rewrites the pickle. The lock is released before the bounded Habitica HTTP
request or avatar render begins, so a slow renderer cannot block webhook persistence.
This is a deliberately small read-only bridge for the current trusted server-side
pickle; it should be replaced behind the adapter if persistence is migrated later.

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

Focused task checks can be run while iterating:

```bash
python -m pytest tests/test_habitica_api.py tests/test_habitica_gameplay.py \
  tests/test_miniapp_tasks.py tests/test_miniapp_task_routes.py \
  tests/test_miniapp_frontend.py tests/test_bot_behavior.py
node --test tests/miniapp_tasks.test.js tests/miniapp_gameplay.test.js
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
- **Mini App says “Open from Telegram”:** launch it through the bot's configured Menu
  Button/Main Mini App. Ordinary browsers have no signed `initData`; use the explicit
  two-variable development mode only on a local machine.
- **Mini App says “Connect Habitica first”:** send `/start` to the bot in a private chat
  and link the same Telegram account that opened the Mini App.
- **Avatar says Node is unavailable:** run `npm ci`, set `NODE_BIN` to an executable
  Node path if auto-detection fails, and verify Puppeteer's Chromium can start.
- **Avatar times out or returns no PNG:** check host resource limits and optionally
  adjust `AVATAR_RENDER_TIMEOUT_SECONDS` up to 120 seconds. Renderer logs are sanitized;
  reproduce locally for detailed diagnosis rather than logging profile JSON.
- **PythonAnywhere import fails:** verify the source directory, virtualenv, selected
  Python version, and WSGI `sys.path`, then reload the web app.
- **Habitica actions fail:** verify credentials through `/relink` in a private chat.
  Never paste credentials into an issue, log, screenshot, or group chat.
- **Task change says its outcome is unknown:** do not repeat the action immediately.
  Let the page reconcile from Habitica or use its refresh control first; score and
  checklist operations are intentionally never retried blindly.
- **Mini App stays on Record Yesterday:** complete or explicitly submit the blocking
  review. Browser midnight is intentionally ignored; if Habitica is unavailable, the
  app cannot safely enable current-day scoring.
- **Record Yesterday asks to continue:** all choices remain allowed, but a free-host
  request records at most eight selected Dailies. Wait about one minute, then explicitly
  submit the checked remainder. Cron does not run until every selected Daily is resolved.
- **Potion action is unavailable:** resolve Record Yesterday first. Full health and
  insufficient gold are normal gameplay results; authentication errors require
  `/relink`, while an unknown purchase outcome should be reconciled before retrying.
- **Potion confirmation expired:** close and reopen the Potion sheet to obtain a new
  purchase intent. Do not resubmit an old or copied confirmation token.

## Security and privacy

- Telegram and Habitica secrets come from environment variables or private persisted
  user data; no secrets belong in source control.
- Habitica API calls use bounded timeouts, validated response envelopes, and no blind
  retry of non-idempotent mutations.
- Webhook and tick secrets use timing-safe comparisons.
- Raw Telegram updates and raw Habitica response bodies are not normal log content.
- The bot and Mini App intentionally remain a local-pickle deployment for this milestone. Protect
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
- The Mini App supports personal Habits, Todos, and simple weekly Dailies. It displays
  challenge/group or advanced-schedule tasks but keeps unsupported edits read-only;
  tags, rewards, reminders, advanced recurrence, and drag ordering remain out of scope.
- Editing an existing checklist is capped at one item operation per save to keep a
  one-worker deployment responsive and reduce partial updates. Larger checklist edits
  should be split across saves.
- Habitica task mutations are not transactional. If a mutation response is lost, the
  UI refreshes display state but blocks another conflicting mutation for the remainder
  of that Mini App session; reopening the app is the explicit new intent. A partly
  applied edit likewise requires authoritative refresh before further work.
- Completed Todos are limited to Habitica's retained completed-Todo feed rather than a
  permanent local archive; this project intentionally adds no task database or cache.
- The runtime lock coordinates only processes on one Linux host that use these WSGI
  routes and the same `BOT_DATA_PATH`; it does not coordinate polling or another host.
- Record Yesterday reproduces only the review state Habitica still exposes for the
  immediately preceding user day. For multiple missed days it reports the count but
  does not invent unavailable historical task states; Habitica cron remains
  authoritative for missed-time effects.
- Record Yesterday accepts all selected Dailies, but records at most eight per request
  to reserve Habitica's shared per-user request budget for startup and post-cron sync.
  A larger selection requires an explicit continuation after roughly one minute; no
  mutating request is retried automatically, and cron waits for the final batch.
- Habitica exposes current and last-cron numeric offsets but no trusted timezone ID;
  the review uses the current offset for the visible yesterday date and the validated
  last-cron offset for schedule conversion. Exact historical DST reconstruction is
  therefore not possible after unusual timezone changes.
- Local gameplay locks prevent duplicate Mini App requests on this host, but cannot
  make a sequence of Habitica HTTP mutations atomic with the official app or another
  client. A fresh `needsCron` read immediately precedes each selected Daily score and
  cron, reducing the unavoidable final GET-to-POST race; an ambiguous response is
  surfaced instead of blindly repeated.
- `PicklePersistence` remains a non-database file format; the lock prevents concurrent
  writers but cannot make a write crash-proof. Keep private, quiesced backups as
  described above.

## License

MIT. See `LICENSE`.
