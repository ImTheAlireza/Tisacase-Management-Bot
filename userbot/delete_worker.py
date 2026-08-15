#!/usr/bin/env python3
"""
Telethon user-bot worker: deletes old group messages on behalf of the owner.

The management bot cannot delete group messages older than 48 hours (hard
Telegram Bot API limit). This worker runs on the SAME server, connects to the
management bot's MySQL database (same MAIN_DB_* env vars), claims rows from
the `userbot_deletion_queue` table and deletes those messages through the
OWNER'S USER ACCOUNT — user accounts have no 48-hour limit, and in basic
groups any member may delete any message.

⚠️  Userbots are against Telegram's Terms of Service. Using this worker is at
    your own risk; it performs deletions ONLY (no reading/replies), which is
    about as low-risk as automation gets, but your account could still be
    restricted by Telegram.

Environment (same .env as the management bot, plus):
    USERBOT_API_ID        required — from https://my.telegram.org
    USERBOT_API_HASH      required — from https://my.telegram.org
    USERBOT_SESSION       Telethon StringSession (preferred)
                          or USERBOT_SESSION_FILE — path to a .session file
    USERBOT_POLL_INTERVAL seconds between empty-queue polls (default 5)
    USERBOT_BATCH_SIZE    rows claimed per cycle (default 25)
    USERBOT_DELETE_PACE   seconds between two deletions (default 2.0)
    USERBOT_MAX_ATTEMPTS  attempts before a row is marked failed (default 5)
    USERBOT_STALE_MINUTES reclaim processing rows stuck this long (default 15)

Usage:
    # first time: create a session interactively (phone + code)
    python userbot/delete_worker.py --login

    # run forever (systemd / nohup)
    python userbot/delete_worker.py

    # single pass (useful for cron / testing)
    python userbot/delete_worker.py --once
"""

import argparse
import asyncio
import logging
import os
import signal
import sys

# Make sure we read the management bot's .env BEFORE importing config.settings
# (it exits at import time when MAIN_* env vars are missing).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO_ROOT, '.env'))
except ImportError:
    pass

from config.settings import (  # noqa: E402
    USERBOT_API_ID,
    USERBOT_API_HASH,
    USERBOT_SESSION,
    USERBOT_SESSION_FILE,
    USERBOT_POLL_INTERVAL,
    USERBOT_BATCH_SIZE,
    USERBOT_DELETE_PACE,
    USERBOT_MAX_ATTEMPTS,
    USERBOT_STALE_MINUTES,
)
from models.userbot_deletion_queue import UserbotDeletionQueue  # noqa: E402

from telethon import TelegramClient  # noqa: E402
from telethon.errors import FloodWaitError  # noqa: E402
from telethon.sessions import StringSession  # noqa: E402

LOG_TAG = "[USERBOT]"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("userbot_worker")


def build_client() -> TelegramClient:
    """Build the Telethon client from env configuration."""
    if not USERBOT_API_ID or not USERBOT_API_HASH:
        logger.error(
            "USERBOT_API_ID / USERBOT_API_HASH are missing. "
            "Get them from https://my.telegram.org and add them to the .env file."
        )
        sys.exit(2)

    try:
        api_id = int(USERBOT_API_ID)
    except ValueError:
        logger.error("USERBOT_API_ID must be numeric")
        sys.exit(2)

    if USERBOT_SESSION:
        return TelegramClient(
            StringSession(USERBOT_SESSION), api_id, USERBOT_API_HASH
        )
    if USERBOT_SESSION_FILE:
        return TelegramClient(USERBOT_SESSION_FILE, api_id, USERBOT_API_HASH)

    logger.error(
        "No session configured: set USERBOT_SESSION (StringSession) or "
        "USERBOT_SESSION_FILE (path). Run with --login to create a file session."
    )
    sys.exit(2)


async def delete_one(client: TelegramClient, row: dict) -> str:
    """Delete a single queued message through the user account.

    Returns 'done', 'retry' or 'failed'.
    """
    queue_id = row['id']
    chat_id = row['chat_id']
    message_id = row['message_id']

    try:
        # revoke=True deletes for everyone (required to clear old files).
        await client.delete_messages(chat_id, message_ids=[message_id], revoke=True)
        UserbotDeletionQueue.mark_done(queue_id)
        logger.info(
            f"{LOG_TAG} deleted chat={chat_id} msg={message_id} code={row.get('code')}"
        )
        return 'done'
    except FloodWaitError as e:
        wait = max(int(e.seconds), 1)
        logger.warning(
            f"{LOG_TAG} flood control for msg={message_id}: "
            f"waiting {wait}s, will retry"
        )
        UserbotDeletionQueue.retry_later(queue_id, f"flood wait {wait}s")
        await asyncio.sleep(wait)
        return 'retry'
    except Exception as e:
        error = str(e)[:300]
        if row.get('attempts', 0) >= USERBOT_MAX_ATTEMPTS:
            UserbotDeletionQueue.mark_failed(queue_id, error)
            logger.error(
                f"{LOG_TAG} FAILED (final) chat={chat_id} msg={message_id}: {error}"
            )
            return 'failed'
        UserbotDeletionQueue.retry_later(queue_id, error)
        logger.warning(
            f"{LOG_TAG} error chat={chat_id} msg={message_id} "
            f"(attempt {row.get('attempts', 0)}/{USERBOT_MAX_ATTEMPTS}): {error}"
        )
        return 'retry'


async def run_once(client: TelegramClient) -> int:
    """Claim and process one batch. Returns the number of processed rows."""
    rows = UserbotDeletionQueue.claim_batch(
        limit=USERBOT_BATCH_SIZE,
        stale_minutes=USERBOT_STALE_MINUTES,
    )
    if not rows:
        return 0

    logger.info(f"{LOG_TAG} processing {len(rows)} queued message(s)")
    for row in rows:
        await delete_one(client, row)
        if USERBOT_DELETE_PACE > 0:
            await asyncio.sleep(USERBOT_DELETE_PACE)
    return len(rows)


async def run_loop(client: TelegramClient) -> None:
    """Poll the queue forever with graceful shutdown."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    me = await client.get_me()
    logger.info(
        f"{LOG_TAG} started as {me.first_name} "
        f"(@{me.username if me.username else me.id}) — waiting for queue items"
    )

    while not stop.is_set():
        try:
            processed = await run_once(client)
            if processed == 0:
                # Empty queue: wait, but stay responsive to shutdown.
                try:
                    await asyncio.wait_for(stop.wait(), timeout=USERBOT_POLL_INTERVAL)
                except asyncio.TimeoutError:
                    pass
        except Exception as e:
            logger.exception(f"{LOG_TAG} cycle error: {e}")
            try:
                await asyncio.wait_for(stop.wait(), timeout=USERBOT_POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass

    logger.info(f"{LOG_TAG} shutdown requested — disconnecting")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Userbot deletion worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="process one batch and exit (cron/testing)",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="interactively create the session file (phone + code)",
    )
    args = parser.parse_args()

    client = build_client()

    try:
        if args.login:
            await client.start()  # interactive phone/code prompt
            me = await client.get_me()
            logger.info(
                f"{LOG_TAG} logged in as {me.first_name} "
                f"(@{me.username if me.username else me.id}) — session saved"
            )
            return

        await client.start()  # with an existing session this is a no-op

        if args.once:
            processed = await run_once(client)
            logger.info(f"{LOG_TAG} single pass done — {processed} message(s)")
            return

        await run_loop(client)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(f"{LOG_TAG} interrupted")
