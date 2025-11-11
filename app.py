#!/usr/bin/env python3
"""
app.py - simple Telegram relay webhook for Render/ngrok/local.

Behavior:
 - Requires BOT_TOKEN, USER_A_ID, USER_B_ID in env (or .env locally).
 - Exposes webhook endpoint at /<BOT_TOKEN>.
 - For any message from USER_A -> copies the message to USER_B, and vice-versa.
 - Uses Telegram HTTP API (requests) to avoid async/polling complexities.
 - No external logging of message contents.
"""

import os
import logging
from typing import Set
from flask import Flask, request, Response
import requests

# load .env (for local testing)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass  # python-dotenv optional

LOG_LEVEL = logging.DEBUG if os.environ.get("DEBUG", "0") == "1" else logging.INFO
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=LOG_LEVEL)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
USER_A = os.environ.get("USER_A_ID")
USER_B = os.environ.get("USER_B_ID")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # optional, used to auto-set webhook if desired
USE_COPY = os.environ.get("USE_COPY", "1") != "0"  # default use copyMessage
REPLY_UNAUTHORIZED = os.environ.get("REPLY_UNAUTHORIZED", "1") != "0"

if not BOT_TOKEN or not USER_A or not USER_B:
    logger.error("BOT_TOKEN, USER_A_ID and USER_B_ID must be set in environment.")
    raise SystemExit("Missing required environment variables (BOT_TOKEN, USER_A_ID, USER_B_ID).")

try:
    allowed_ids: Set[int] = {int(USER_A), int(USER_B)}
except ValueError:
    logger.error("USER_A_ID and USER_B_ID must be integer Telegram user IDs.")
    raise SystemExit("USER IDs must be integers.")

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = Flask(__name__)


@app.route("/", methods=["GET"])
def index():
    return "Relay bot running.\n", 200


@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook_handler():
    """
    Receive Telegram update (webhook) and forward/copy message to the other user.
    """
    try:
        payload = request.get_json(force=True)
    except Exception as e:
        logger.debug("Invalid JSON: %s", e)
        return Response(status=400)

    # Only handle message updates (private chats)
    message = payload.get("message")
    if not message:
        # ignore other update types
        return Response(status=200)

    from_user = message.get("from", {})
    sender_id = from_user.get("id")
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    message_id = message.get("message_id")

    if sender_id is None or message_id is None or chat_id is None:
        logger.debug("Missing fields in update; ignoring.")
        return Response(status=200)

    # Only allow the two designated users
    if sender_id not in allowed_ids:
        logger.info("Blocked message from unauthorized user %s", sender_id)
        if REPLY_UNAUTHORIZED:
            # reply politely (don't leak details)
            try:
                requests.post(f"{API_BASE}/sendMessage", json={
                    "chat_id": sender_id,
                    "text": "You are not authorized to use this bot."
                }, timeout=5)
            except Exception:
                pass
        return Response(status=200)

    # pick the other user as recipient
    recipients = list(allowed_ids - {sender_id})
    if not recipients:
        logger.warning("No recipient configured other than sender %s", sender_id)
        return Response(status=200)

    recipient_id = recipients[0]
    if recipient_id == sender_id:
        logger.warning("Recipient equals sender; ignoring.")
        return Response(status=200)

    # choose API method: copyMessage (new message) or forwardMessage (shows forwarded header)
    try:
        if USE_COPY:
            method = "copyMessage"
            payload = {
                "chat_id": recipient_id,
                "from_chat_id": chat_id,
                "message_id": message_id
            }
        else:
            method = "forwardMessage"
            payload = {
                "chat_id": recipient_id,
                "from_chat_id": chat_id,
                "message_id": message_id
            }

        resp = requests.post(f"{API_BASE}/{method}", json=payload, timeout=8)
        if not resp.ok:
            logger.error("Telegram API error (%s): %s", resp.status_code, resp.text)
            # optionally inform sender
            try:
                requests.post(f"{API_BASE}/sendMessage", json={
                    "chat_id": sender_id,
                    "text": "Failed to deliver the message to the other user."
                }, timeout=5)
            except Exception:
                pass
        else:
            logger.info("Relayed message %s from %s -> %s", message_id, sender_id, recipient_id)
    except Exception as e:
        logger.exception("Exception while relaying message: %s", e)
        try:
            requests.post(f"{API_BASE}/sendMessage", json={
                "chat_id": sender_id,
                "text": "An internal error occurred while forwarding your message."
            }, timeout=5)
        except Exception:
            pass

    return Response(status=200)


def set_webhook():
    """
    Optionally set the webhook if WEBHOOK_URL is present in env.
    The WEBHOOK_URL should include the path /<BOT_TOKEN>, e.g.
    https://example.onrender.com/123456:ABC-...
    """
    if not WEBHOOK_URL:
        logger.info("No WEBHOOK_URL set; skipping automatic webhook configuration.")
        return

    try:
        resp = requests.post(f"{API_BASE}/setWebhook", json={"url": WEBHOOK_URL}, timeout=8)
        if resp.ok:
            logger.info("setWebhook OK: %s", WEBHOOK_URL)
        else:
            logger.error("setWebhook failed: %s %s", resp.status_code, resp.text)
    except Exception as e:
        logger.exception("Error setting webhook: %s", e)


if __name__ == "__main__":
    # If WEBHOOK_URL provided, try to set webhook on startup
    if WEBHOOK_URL:
        set_webhook()

    port = int(os.environ.get("PORT", "8000"))
    logger.info("Starting Flask app on 0.0.0.0:%s (allowed_ids=%s)", port, allowed_ids)
    # Use Flask built-in server (fine for Render). For production you can use gunicorn.
    app.run(host="0.0.0.0", port=port)
