import pymysql
import logging
from typing import Optional
from config.database import get_db_connection
from utils.helpers import get_tehran_time, to_utc_naive


class User:
    """User model for managing users and roles"""

    def __init__(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        role: str = 'editor',
        is_sudo: bool = False,
        active_role: Optional[str] = None,
        is_active: bool = True,
        added_by: Optional[int] = None,
        added_at=None,
        last_active=None,
        metadata=None,
        stats_reset_at=None,
        **kwargs
    ):
        self.user_id = user_id
        self.username = username
        self.first_name = first_name
        self.last_name = last_name
        self.role = role
        self.is_sudo = is_sudo
        self.active_role = active_role or role
        self.is_active = is_active
        self.added_by = added_by
        self.added_at = added_at
        self.last_active = last_active
        self.metadata = metadata
        self.stats_reset_at = stats_reset_at

        if kwargs:
            logging.warning(f"User.__init__ received unknown kwargs: {list(kwargs.keys())}")

    @staticmethod
    def get_by_id(user_id: int) -> Optional['User']:
        """Get user by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            return User(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def create(
        user_id: int,
        first_name: str,
        role: str = 'editor',
        added_by: Optional[int] = None,
        username: Optional[str] = None
    ) -> 'User':
        """Create new user"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            now_utc = to_utc_naive(get_tehran_time())
            cursor.execute("""
                INSERT INTO users
                (user_id, username, first_name, role, is_active, added_by, added_at)
                VALUES (%s, %s, %s, %s, TRUE, %s, %s)
            """, (user_id, username, first_name, role, added_by, now_utc))
            conn.commit()
            logging.info(f"✅ User {user_id} ({first_name}) created as {role}")
            return User.get_by_id(user_id)
        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to create user {user_id}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_all_active() -> list['User']:
        """Get all active users"""
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("""
                SELECT * FROM users
                WHERE is_active = TRUE
                ORDER BY role, first_name
            """)
            return [User(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_role(role: str) -> list['User']:
        """Get all users with specific role"""
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("""
                SELECT * FROM users
                WHERE role = %s AND is_active = TRUE
                ORDER BY first_name
            """, (role,))
            return [User(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def is_privileged_user(user_id: int) -> bool:
        """Check if a user is privileged (Sudo or Nazi) — centralized check."""
        from config.settings import SUDO_USER_ID, NAZI_CHAT_ID
        if user_id in (SUDO_USER_ID, NAZI_CHAT_ID):
            return True
        user = User.get_by_id(user_id)
        return bool(user and user.is_sudo)

    def update_active_role(self, new_role: str) -> None:
        """Update sudo user's active role"""
        if not self.is_sudo:
            raise Exception("Only sudo users can switch roles")
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE users SET active_role = %s WHERE user_id = %s",
                (new_role, self.user_id)
            )
            conn.commit()
            self.active_role = new_role
            logging.info(f"User {self.user_id} switched to role: {new_role}")
        finally:
            cursor.close()
            conn.close()

    def update_last_active(self) -> None:
        """Update last active timestamp"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            now_utc = to_utc_naive(get_tehran_time())
            cursor.execute(
                "UPDATE users SET last_active = %s WHERE user_id = %s",
                (now_utc, self.user_id)
            )
            conn.commit()
        except Exception as e:
            logging.warning(f"Failed to update last_active for {self.user_id}: {e}")
        finally:
            cursor.close()
            conn.close()

    def deactivate(self) -> None:
        """Deactivate user"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE users SET is_active = FALSE WHERE user_id = %s",
                (self.user_id,)
            )
            conn.commit()
            self.is_active = False
            logging.info(f"User {self.user_id} deactivated")
        finally:
            cursor.close()
            conn.close()

    def get_effective_role(self) -> str:
        """Get the role user is currently acting as"""
        if self.is_sudo:
            return self.active_role or self.role
        return self.role

    def has_permission(self, permission: str) -> bool:
        """Check if user has specific permission"""
        effective_role = self.get_effective_role()
        if effective_role == 'sudo':
            return True
        permissions: dict[str, list[str]] = {
            'editor':   ['create_design', 'upload_files', 'view_own_designs'],
            'reviewer': ['approve_design', 'reject_design', 'view_pending']
        }
        return permission in permissions.get(effective_role, [])