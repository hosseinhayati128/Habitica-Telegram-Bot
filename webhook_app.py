import asyncio
from http import HTTPStatus

from flask import Flask, request
from telegram import Update

# ⬅️ replace `your_bot_module` with the *actual* filename of the big bot file,
# WITHOUT the `.py` extension.
# For example, if your file is `habitica_bot.py`, use:
#   from habitica_bot import build_application
from habitica_bot import build_application

flask_app = Flask(__name__)


async def _handle_update(update_json: dict) -> None:
    """
    Build an Application, process ONE update, and shut it down.
    This follows the 'no long running tasks' pattern from python-telegram-bot docs.
    """
    application = build_application(register_commands=False)

    async with application:
        update = Update.de_json(update_json, application.bot)
        await application.process_update(update)


@flask_app.post("/telegram-webhook")  # you can change this path to something secret-ish
def telegram_webhook():
    if request.headers.get("content-type") != "application/json":
        return "Unsupported Media Type", HTTPStatus.UNSUPPORTED_MEDIA_TYPE

    update_json = request.get_json(silent=True, force=True)
    if not update_json:
        return "Bad Request", HTTPStatus.BAD_REQUEST

    # Run the async handler in a fresh event loop for this request
    asyncio.run(_handle_update(update_json))

    return "OK", HTTPStatus.OK
