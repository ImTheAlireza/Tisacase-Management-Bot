import asyncio
import pytz
import logging
from datetime import datetime
from typing import Awaitable, Callable, Optional, TypeVar

from telegram.error import RetryAfter

from config.settings import TELEGRAM_SEND_DELAY, TIMEZONE

TEHRAN_TZ = pytz.timezone(TIMEZONE)
T = TypeVar('T')
DEFAULT_MAX_SEND_RETRIES = 3


def get_tehran_time(dt: Optional[datetime] = None) -> datetime:
    """
    Get current time in Tehran timezone or convert given datetime to Tehran.

    Args:
        dt: datetime object (can be None, naive UTC, or aware)

    Returns:
        datetime object with Tehran timezone
    """
    if dt is None:
        return datetime.now(pytz.utc).astimezone(TEHRAN_TZ)
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    return dt.astimezone(TEHRAN_TZ)


def to_utc_naive(dt_tehran: datetime) -> datetime:
    """
    Convert Tehran time to UTC naive for MySQL storage.

    Args:
        dt_tehran: datetime object (can be naive Tehran or aware)

    Returns:
        naive datetime in UTC (without timezone info)
    """
    if dt_tehran.tzinfo is None:
        dt_tehran = TEHRAN_TZ.localize(dt_tehran)
    return dt_tehran.astimezone(pytz.UTC).replace(tzinfo=None)


def format_datetime_persian(dt: Optional[datetime]) -> str:
    """Format datetime in Persian-friendly format."""
    if dt is None:
        return "نامشخص"
    tehran_time = get_tehran_time(dt)
    return tehran_time.strftime('%Y/%m/%d %H:%M')


def retry_after_seconds(error: RetryAfter) -> int:
    """Extract Telegram RetryAfter delay as whole seconds.

    PTB v20 exposes ``retry_after`` as an int; PTB v22 may expose it as a
    ``datetime.timedelta``. Keep this helper centralized so all send/delete
    paths handle both versions consistently.
    """
    raw = getattr(error, 'retry_after', 1)
    if hasattr(raw, 'total_seconds'):
        try:
            return max(int(raw.total_seconds()), 1)
        except (TypeError, ValueError):
            return 1
    try:
        return max(int(raw or 1), 1)
    except (TypeError, ValueError):
        return 1


async def send_with_retry(
    send: Callable[[], Awaitable[T]],
    label: str = "Telegram request",
    *,
    max_retries: int = DEFAULT_MAX_SEND_RETRIES,
    pace_delay: Optional[float] = None,
) -> T:
    """Run one Telegram API call with flood-control retry and pacing.

    Args:
        send: zero-argument callable returning the awaitable Telegram request.
        label: human-readable operation label for logs.
        max_retries: total attempts for RetryAfter/429 responses.
        pace_delay: delay after the request finishes. Defaults to
            ``TELEGRAM_SEND_DELAY``; pass ``0`` to disable.

    Raises:
        The final Telegram exception after retries are exhausted. Non-429
        exceptions are not swallowed so callers can log per-user/per-file errors.
    """
    attempts = max(max_retries, 1)
    try:
        for attempt in range(1, attempts + 1):
            try:
                return await send()
            except RetryAfter as e:
                wait = retry_after_seconds(e)
                if attempt >= attempts:
                    logging.error(
                        f"{label} flood control (429), retries exhausted "
                        f"after {attempt}/{attempts} attempts; retry_after={wait}s"
                    )
                    raise
                logging.warning(
                    f"{label} flood control (429), retrying in {wait}s "
                    f"(attempt {attempt}/{attempts})"
                )
                await asyncio.sleep(wait)
    finally:
        delay = TELEGRAM_SEND_DELAY if pace_delay is None else pace_delay
        if delay and delay > 0:
            await asyncio.sleep(delay)

    # Unreachable, but keeps type checkers happy.
    raise RuntimeError(f"{label} failed unexpectedly")


async def safe_answer_callback(
    query,
    text: Optional[str] = None,
    show_alert: bool = False,
) -> bool:
    """Answer a callback query and never raise.

    Telegram invalidates a callback query after a short window and after it
    has been answered once; answering a stale/duplicate query raises
    ``BadRequest: Query is too old and response timeout expired or query id
    is invalid``. The answer is purely cosmetic (stops the client spinner),
    so a failed answer must never abort the handler's actual work.

    Returns True when the answer was delivered, False otherwise.
    """
    try:
        await query.answer(text=text, show_alert=show_alert)
        return True
    except Exception as e:
        logging.warning(f"Could not answer callback query: {e}")
        return False


DELETED_BY_BOT_CAPTION = (
    "🗑 این پیام توسط ربات حذف شد.\n"
    "⚠️ تلگرام اجازه حذف کامل پیام‌های قدیمی‌تر از ۴۸ ساعت را به ربات نمی‌دهد — "
    "برای پاک شدن کامل فایل، ادمین باید این پیام را دستی حذف کند."
)
DELETED_BY_BOT_TEXT = "🗑 حذف شده توسط ربات"


def group_message_link(chat_id: int, message_id: int) -> str | None:
    """Build a t.me link to a group message (None for private chats).

    Works for basic groups (id like -4608593336) and supergroups
    (id like -1001234567890): the link is t.me/c/<id>/<message_id>,
    with the supergroup '100' prefix stripped.
    """
    if chat_id >= 0:
        return None
    cid = abs(chat_id)
    cid_str = str(cid)
    if cid_str.startswith('100') and len(cid_str) >= 10:
        cid = int(cid_str[3:])
    return f"https://t.me/c/{cid}/{message_id}"


def deleted_marker_caption(chat_id: int, message_id: int) -> str:
    """Marker caption for a group message the bot could not delete.

    Includes a plain-text t.me link so anyone in the group (or the admin
    reading the report) can tap straight to the message and remove it.
    """
    link = group_message_link(chat_id, message_id)
    if link:
        return f"{DELETED_BY_BOT_CAPTION}\n\n🔗 {link}"
    return DELETED_BY_BOT_CAPTION


async def _mark_message_deleted(bot, chat_id: int, message_id: int) -> bool:
    """Edit a message to a deletion marker (best-effort).

    Bots can edit their own messages regardless of age, so when a full
    delete is impossible this at least hides the caption and signals the
    message is obsolete. Returns True when any edit succeeded.
    """
    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=deleted_marker_caption(chat_id, message_id),
            reply_markup=None
        )
        return True
    except Exception:
        pass

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=DELETED_BY_BOT_TEXT,
            reply_markup=None
        )
        return True
    except Exception as e:
        logging.warning(
            f"Could not edit message {message_id} in chat {chat_id}: {e}"
        )
        return False


async def delete_group_message(bot, chat_id: int, message_id: int) -> str:
    """Delete one group message for everyone, with a fallback for old messages.

    Telegram hard-limits bot deletions to messages sent less than 48 hours
    ago; older messages raise ``BadRequest: message can't be deleted for
    everyone`` and NO bot-side API call can fully remove them (this limit
    applies even in supergroups). Editing has no age limit, so as a fallback
    we edit the message to a deletion marker to hide its caption and flag it
    for manual removal by a human admin.

    Returns one of:
        'deleted' — message fully deleted for everyone.
        'hidden'  — too old to delete; caption/content replaced with a
                    deletion marker (human admin can still remove the file).
        'failed'  — deletion failed for another reason (no rights, 429
                    exhausted, ...) and the marker edit also failed.
    """
    try:
        await send_with_retry(
            lambda: bot.delete_message(chat_id=chat_id, message_id=message_id),
            f"Delete message {message_id} in chat {chat_id}"
        )
        return 'deleted'
    except Exception as e:
        error_text = str(e).lower()
        if "can't be deleted" not in error_text:
            logging.warning(
                f"Delete message {message_id} in chat {chat_id} failed: {e}"
            )
            return 'failed'

        logging.info(
            f"Message {message_id} in chat {chat_id} is older than 48h — "
            "Telegram refuses bot deletion; editing to a deletion marker"
        )
        if await _mark_message_deleted(bot, chat_id, message_id):
            return 'hidden'
        return 'failed'


async def delete_messages(bot, chat_id: int, message_ids: list[int]) -> int:
    """Safely delete multiple messages with pacing and 429 retry.

    Returns the number of messages successfully deleted. Individual failures are
    logged and do not abort the rest of the deletion batch.
    """
    if not message_ids:
        return 0

    deleted_count = 0
    for msg_id in message_ids:
        try:
            await send_with_retry(
                lambda msg_id=msg_id: bot.delete_message(chat_id=chat_id, message_id=msg_id),
                f"Delete message {msg_id} in chat {chat_id}"
            )
            deleted_count += 1
        except Exception as e:
            logging.warning(f"Failed to delete message {msg_id} in chat {chat_id}: {e}")
    return deleted_count


async def safe_edit_message(
    query,
    text: str,
    reply_markup=None,
    parse_mode: Optional[str] = None
) -> None:
    """Safely edit message — handles both text and caption messages."""
    try:
        if query.message.text:
            await query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        elif (query.message.caption is not None
              or query.message.photo
              or query.message.document):
            await query.edit_message_caption(
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        else:
            await query.message.delete()
            await query.message.chat.send_message(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
    except Exception as e:
        logging.error(f"Error in safe_edit_message: {e}")
        try:
            await query.message.delete()
            await query.message.chat.send_message(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        except Exception as e2:
            logging.error(f"safe_edit_message fallback also failed: {e2}")
