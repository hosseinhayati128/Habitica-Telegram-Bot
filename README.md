# HHabitica – Habitica Telegram Bot

Unofficial Telegram bot for [Habitica](https://habitica.com) that lets you manage your **Habits, Dailys, Todos and Rewards directly from Telegram** — with inline menus, a handy reply keyboard and a pinned status HUD.

You can try the running bot here: **[@HHabitica_bot](https://t.me/HHabitica_bot)**

> This is a fan project, not affiliated with or endorsed by Habitica.

---

## Features

* ✅ **Link your Habitica account once**

  * `/start` guides you through entering your `USER_ID` and `API_KEY`
  * `/relink` lets you switch to a different Habitica account
  * Credentials are stored per‑Telegram‑user using `PicklePersistence` (local file)

* 📊 **Pinned status HUD in your DM**

  * Shows HP, MP, Gold, XP, level, etc.
  * Automatically updated whenever you:

    * score a task (Habit / Daily / Todo)
    * buy a reward / health potion
    * run **Refresh Day / cron**

* 🌀 **Interactive panels for tasks**

  * `/habits` – Habits with ➕ / ➖ buttons and counters
  * `/dailys` – Dailys with completed/active markers and layout toggle
  * `/todos` – Todos as a big button panel with layout toggle
  * `/completedtodos` – Completed todos you can un‑complete with one tap
  * `/rewards` – Custom rewards, with price in gold and one‑tap “buy”

* 🧪 **Potions & rewards**

  * `/buy_potion` or the **🧪 Buy Potion** button to quickly heal
  * Rewards panel (`rMenu`) lets you buy any Habitica custom reward

* 📅 **Refresh Day (cron) helper**

  * `/refresh_day` / **🔄 Refresh Day** button:

    * shows yesterday’s unfinished Dailies
    * lets you mark which ones you actually did
    * then runs Habitica cron for you
  * Inline version from the **Inline Menu** too

* 📝 **Add Todos from Telegram**

  * `/add_todo` or **➕ New Todo** button
  * Conversation flow:

    1. Ask for title
    2. Ask for difficulty (Trivial / Easy / Medium / Hard)
    3. Creates the Todo in Habitica and confirms

* 🔎 **Inline task picker (works in any chat)**

  * Type `@HHabitica_bot` in any chat and choose:

    * “All Habits / All Dailys / All Todos / Rewards”
  * Opens interactive panels **inline**, so you can:

    * see lists
    * score tasks
    * see updated stats — without leaving the chat

* 🧱 **Reply keyboard menu**

  * Persistent RK with buttons:

    * 🔎 Inline Menu
    * 🌀 Habits / 📅 Dailys / 📝 Todos / 💰 Rewards
    * ➕ New Todo
    * 📊 Status / 🧪 Buy Potion
    * 🔄 Refresh Day
    * ✅ Completed Todos / 🔁 Menu
  * `/menu` and `/menu_rk` control how/when it’s shown

* 🧍 **Avatar image export**

  * `/avatar` sends a PNG image of your current Habitica avatar
  * Uses a small Node.js helper with Puppeteer + the `habitica-avatar` library (optional; see “Installation & setup”)


* ⚙️ **Configurable panel layout via `PANEL_BEHAVIOUR`**

  * One central dict defines how each panel looks:

    * whether to show status block
    * whether to show the plain text task list
    * whether the list comes before or after the status block
  * Used consistently for:

    * `/habits`, `/dailys`, `/todos`, `/completedtodos`, `/rewards`
    * Their inline “All …” versions
    * The `hMenu`, `dMenu`, `tMenu`, `rMenu`, `cMenu` callbacks and refreshes

---

## Panel behaviour configuration

In `habitica_bot.py` you’ll find something like:

```python
PANEL_BEHAVIOUR: dict[str, dict[str, bool]] = {
    "habits": {
        "show_status": True,
        "show_list": False,
        "list_first": True,
    },
    "dailys": {
        "show_status": True,
        "show_list": True,
        "list_first": True,
    },
    "todos": {
        "show_status": True,
        "show_list": True,
        "list_first": False,
    },
    "completedTodos": {
        "show_status": False,
        "show_list": True,
        "list_first": True,
    },
    "rewards": {
        "show_status": True,
        "show_list": False,
        "list_first": False,
    },
}
```

**What each flag means:**

* `show_status`: include the HP/MP/XP/Gold block at the top/bottom.
* `show_list`: include a text list of tasks (in addition to buttons).
* `list_first`:

  * `True` → list first, then status block
  * `False` → status block first, then list

All the panels (normal commands, inline “All …” queries, and the `hMenu/tMenu/rMenu/dMenu/cMenu` callbacks) rebuild their message text using this same configuration, so your layout stays consistent even after scoring tasks or buying rewards.

---

## Commands overview

Common commands you’ll see in the bot:

| Command           | Description                                                     |
| ----------------- | --------------------------------------------------------------- |
| `/start`          | Link your Habitica account (or change it if already linked)     |
| `/relink`         | Force re-entering USER_ID and API_KEY                           |
| `/status`         | Show current stats and refresh the pinned HUD                   |
| `/habits`         | Habits panel (buttons + optional task list)                     |
| `/dailys`         | Dailys panel (with layout toggle)                               |
| `/todos`          | Todos panel (with layout toggle)                                |
| `/completedtodos` | Completed todos that you can un‑complete                        |
| `/rewards`        | Custom rewards panel                                            |
| `/buy_potion`     | Quick “buy health potion” shortcut                              |
| `/refresh_day`    | Guided Habitica cron / “Refresh Day” flow                       |
| `/add_todo`       | Add a new Todo via conversation                                 |
| `/inline`         | Show inline launcher helper message                             |
| `/menu`           | Show / rebuild the reply keyboard menu                          |
| `/task_list`      | Text-only grouped task list (Habits / Dailys / Todos / Rewards) |
| `/avatar`         | Generate and send your Habitica avatar as a PNG image           |
| `/debug`          | Debug info (intended for dev/testing)                           |
| `/sync_commands`  | Manually re-sync Telegram commands menu                         |

---

## Installation & setup

### 1. Clone the repo

```bash
git clone https://github.com/<your-username>/<your-repo-name>.git
cd <your-repo-name>
```

### 2. Create a virtual environment (optional but recommended)

```bash
python -m venv venv
source venv/bin/activate       # on Windows: venv\Scripts\activate
```

### 3. Install dependencies

If you have a `requirements.txt`:

```bash
pip install -r requirements.txt
```

Otherwise, make sure you install at least:

* `python-telegram-bot` (v20+)
* `requests`
* `httpx`
* `flask` (only needed if you use the webhook/WSGI setup)

### 4. Create a Telegram bot

1. Talk to [@BotFather](https://t.me/BotFather) on Telegram.
2. Run `/newbot` and follow the prompts.
3. Copy the bot token you get at the end.

### 5. Get your Habitica API credentials

In Habitica:

1. Open **User → Settings → API**.
2. Copy your `User ID` and `API Token`.
3. You’ll enter these inside Telegram via `/start` (the bot does *not* hard-code them).

### 6. Set `TELEGRAM_BOT_TOKEN` environment variable

The main bot code reads your token from the environment:

```python
BOT_TOKEN: Final = os.environ.get("TELEGRAM_BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
```

So, before running the bot, set:

**Linux / macOS:**

```bash
export TELEGRAM_BOT_TOKEN="your-telegram-bot-token-here"
```

**Windows (PowerShell):**

```powershell
$env:TELEGRAM_BOT_TOKEN="your-telegram-bot-token-here"
```

*(On hosts like PythonAnywhere you can set this in your WSGI file or environment config instead of committing it to the repo.)*

### 7. (Optional) Enable `/avatar` (Node.js avatar renderer)

If you want the `/avatar` command to send a PNG of your Habitica avatar, you also need Node.js and the small helper project in this repo (`render_avatar_from_json.js`, `package.json`, etc.). 

1. Install **Node.js 18+** on the server (required by Puppeteer). 
2. From the project root, install the Node dependencies defined in `package.json`:

   ```bash
   npm install
   ```
3. Build (or rebuild) the browser bundle that the renderer uses:

   ```bash
   npx browserify node_modules/habitica-avatar/index.js -s habiticaAvatar -o habitica-avatar.bundle.js
   ```

   You do not need to recreate `habitica-avatar.bundle.js` on every deploy.
You can commit the generated file to the repo and deploy it like any other asset.
Only rebuild it if you update the `habitica-avatar` dependency or change the avatar renderer code.




---

## Running the bot

### Option A – Local development (polling)

The main file (e.g. `habitica_bot.py`) defines a `build_application()` helper and uses polling when run directly:

```bash
python habitica_bot.py
```

This:

* builds a `python-telegram-bot` `Application`
* syncs commands (if configured)
* starts `run_polling()`

Now just open your bot in Telegram and run `/start`.

### Option B – Webhook / WSGI hosting (e.g. PythonAnywhere, other hosts)

The repo also contains:

* `webhook_app.py` – a small Flask app that:

  * exposes `/telegram-webhook`
  * builds an `Application` via `build_application(register_commands=False)`
  * processes **one** Telegram update per HTTP request (no long-running tasks)
* `wsgi.py` (or similar) – example WSGI entry file that imports the Flask app and sets `TELEGRAM_BOT_TOKEN` in the environment.

Typical high‑level steps:

1. Configure your host (PythonAnywhere, VPS, etc.) to serve `webhook_app.flask_app`.
2. Make sure the environment variable `TELEGRAM_BOT_TOKEN` is set on the server.
3. Tell Telegram to use your webhook URL, e.g.:

   ```bash
   https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook?url=https://your-domain/telegram-webhook
   ```

This approach works well on platforms that don’t allow long‑running background processes.

---

## Data & privacy

* The bot uses `PicklePersistence` to store:

  * your Habitica `USER_ID` and `API_KEY`
  * some per-user/per-chat settings (layouts, pinned message IDs, etc.)
* All of this is stored **in a local file on the machine running the bot** (e.g. `botdata.pkl`).
* No external database is used; you control the data by controlling the server.

If you’re deploying a public instance, make sure you’re comfortable storing API keys on that machine and that it’s not shared with untrusted users.

---

## Trying the hosted bot

If you just want to see how it behaves before self‑hosting, you can connect your Habitica account to the public instance:

👉 **[@HHabitica_bot](https://t.me/HHabitica_bot)**

*(Please keep in mind it’s a personal hobby bot; uptime is not guaranteed.)*

---

## Contributing

Feel free to:

* Fork the repo and adapt it to your own workflow
* Open issues / PRs for bugs, improvements, or new features
* Tweak `PANEL_BEHAVIOUR`, reply keyboards, or inline flows to better match your Habitica setup
