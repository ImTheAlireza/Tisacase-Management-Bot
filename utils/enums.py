from enum import Enum


class DesignStatus(str, Enum):
    """
    Design workflow statuses.
    Inherits from str so values can be used directly
    in SQL queries and JSON serialization.
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


class EditorStage(str, Enum):
    """
    Stages of the editor file submission flow.
    Linear: mockup → print → confirm
    With workspace as editing hub.
    """
    MOCKUP    = "mockup"
    PRINT     = "print"
    CONFIRM   = "confirm"
    WORKSPACE = "workspace"