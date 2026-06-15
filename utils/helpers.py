import pytz
import logging
from datetime import datetime
from typing import Optional
from config.settings import TIMEZONE

TEHRAN_TZ = pytz.timezone(TIMEZONE)


def get_tehran_time(dt: Optional[datetime] = None) -> datetime:
    """
    Get current time in Tehran timezone or convert given datetime to Tehran.

    Args:
        dt: datetime object (can be None, naive UTC, or aware)

    Returns:
        datetime object with Tehran timezone
    """
    if dt is None:
        return datetime.now(TEHRAN_TZ)
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


async def delete_messages(bot, chat_id: int, message_ids: list[int]) -> None:
    """Safely delete multiple messages."""
    if not message_ids:
        return
    for msg_id in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            logging.warning(f"Failed to delete message {msg_id}: {e}")


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