import time
import logging
from typing import Dict, Tuple
from collections import defaultdict


class RateLimiter:
    """
    Thread-safe rate limiter for bot operations.
    Tracks per-user cooldowns for different actions.
    """

    def __init__(self):
        # {user_id: {action: last_timestamp}}
        self._timestamps: Dict[int, Dict[str, float]] = defaultdict(dict)
        # {action: cooldown_seconds}
        self._cooldowns: Dict[str, float] = {
            'file_upload': 1.0,          # 1 second between file uploads
            'code_generation': 3.0,      # 3 seconds between code generations
            'command': 0.5,              # 0.5 seconds between commands
            'submit_design': 5.0,        # 5 seconds between design submissions
            'review_action': 2.0,        # 2 seconds between approve/reject actions
        }

    def check_rate_limit(self, user_id: int, action: str) -> Tuple[bool, float]:
        """
        Check if user can perform action.

        Args:
            user_id: Telegram user ID
            action: Action key (file_upload, code_generation, etc.)

        Returns:
            (can_proceed: bool, wait_seconds: float)
            If can_proceed is False, wait_seconds indicates how long to wait.
        """
        if action not in self._cooldowns:
            logging.warning(f"Unknown rate limit action: {action}")
            return True, 0.0

        cooldown = self._cooldowns[action]
        now = time.time()
        last_action = self._timestamps[user_id].get(action, 0)

        elapsed = now - last_action

        if elapsed >= cooldown:
            self._timestamps[user_id][action] = now
            return True, 0.0
        else:
            wait_time = cooldown - elapsed
            return False, wait_time

    def reset_user(self, user_id: int, action: str = None) -> None:
        """Reset rate limit for a user (all actions or specific action)."""
        if action:
            self._timestamps[user_id].pop(action, None)
        else:
            self._timestamps.pop(user_id, None)

    def set_cooldown(self, action: str, seconds: float) -> None:
        """Update cooldown for an action."""
        self._cooldowns[action] = seconds
        logging.info(f"Rate limit updated: {action} = {seconds}s")


# Global rate limiter instance
rate_limiter = RateLimiter()
