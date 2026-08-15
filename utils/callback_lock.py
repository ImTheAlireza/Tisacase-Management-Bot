import asyncio
import logging
from typing import Callable
from functools import wraps
from utils.helpers import safe_answer_callback


class CallbackLock:
    """
    In-memory lock to prevent duplicate callback processing.
    Prevents double-tap race conditions on approve/reject/delete buttons.
    
    Uses a set of active keys — if a key is already being processed,
    subsequent calls are rejected immediately with a user-friendly message.
    """

    def __init__(self) -> None:
        self._processing: set[str] = set()
        self._lock: asyncio.Lock = asyncio.Lock()

    async def acquire(self, key: str) -> bool:
        """
        Try to acquire lock for a key.
        Returns True if acquired, False if already processing.
        """
        async with self._lock:
            if key in self._processing:
                return False
            self._processing.add(key)
            return True

    async def release(self, key: str) -> None:
        """Release lock for a key."""
        async with self._lock:
            self._processing.discard(key)

    def is_processing(self, key: str) -> bool:
        """Check if a key is currently being processed."""
        return key in self._processing


# Global instance — shared across all handlers
callback_lock = CallbackLock()


def deduplicate_callback(key_func: Callable) -> Callable:
    """
    Decorator for callback handlers that should not run concurrently
    for the same key (e.g. same design code).

    Args:
        key_func: Function that takes (update, context) and returns
                  a string key to lock on.

    Usage:
        @deduplicate_callback(lambda u, c: f"review_{u.callback_query.data.split('_',1)[1]}")
        async def review_callback(update, context):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(update, context, *args, **kwargs):
            query = update.callback_query

            try:
                key = key_func(update, context)
            except Exception as e:
                logging.warning(f"deduplicate_callback key_func failed: {e}")
                return await func(update, context, *args, **kwargs)

            acquired = await callback_lock.acquire(key)
            if not acquired:
                logging.warning(f"Duplicate callback blocked for key: {key}")
                try:
                    await safe_answer_callback(query, "⏳ این درخواست در حال پردازش است. لطفاً صبر کنید...",
                        show_alert=False)
                except Exception:
                    pass
                return

            try:
                return await func(update, context, *args, **kwargs)
            finally:
                await callback_lock.release(key)

        return wrapper
    return decorator