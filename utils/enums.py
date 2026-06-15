from enum import Enum


class DesignStatus(str, Enum):
    """
    Design workflow statuses.
    Inherits from str so values can be used directly in SQL queries
    and JSON serialization without extra conversion.
    """
    PENDING  = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    DELETED  = 'deleted'


class UserRole(str, Enum):
    """User roles in the system."""
    SUDO     = 'sudo'
    EDITOR   = 'editor'
    REVIEWER = 'reviewer'


class GroupType(str, Enum):
    """Types of Telegram groups per product line."""
    PRODUCTS = 'products'
    PRINT    = 'print'