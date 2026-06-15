import re
from typing import Optional
from utils.enums import UserRole


class ValidationError(ValueError):
    """Raised when input validation fails."""
    pass


class Validators:

    # ------------------------------------------------------------------
    # User validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_user_id(value: str) -> int:
        """Validate and parse a Telegram user ID."""
        try:
            uid = int(value)
        except (ValueError, TypeError):
            raise ValidationError("❌ user_id باید عدد صحیح باشد.")
        if uid <= 0:
            raise ValidationError("❌ user_id باید عدد مثبت باشد.")
        return uid

    @staticmethod
    def validate_role(value: str) -> str:
        """Validate user role (editor or reviewer — not sudo)."""
        role = value.strip().lower()
        allowed = {UserRole.EDITOR, UserRole.REVIEWER}
        if role not in allowed:
            raise ValidationError(
                f"❌ نقش نامعتبر: '{role}'\n"
                f"نقش‌های مجاز: editor, reviewer"
            )
        return role

    @staticmethod
    def validate_name(value: str, field: str = "نام") -> str:
        """Validate a display name."""
        name = value.strip()
        if not name:
            raise ValidationError(f"❌ {field} نمی‌تواند خالی باشد.")
        if len(name) > 100:
            raise ValidationError(f"❌ {field} نباید بیشتر از ۱۰۰ کاراکتر باشد.")
        return name

    # ------------------------------------------------------------------
    # Code validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_design_code(value: str, valid_prefixes: list[str]) -> str:
        """
        Validate a design code like TS001, STI042.

        Args:
            value: Raw input string
            valid_prefixes: List of known prefixes e.g. ['TS', 'STI', 'TB', 'TT']

        Returns:
            Uppercased valid code
        """
        code = value.strip().upper()
        if not code:
            raise ValidationError("❌ کد طرح نمی‌تواند خالی باشد.")
        if len(code) > 20:
            raise ValidationError("❌ کد طرح بیش از حد طولانی است.")

        # Must start with a known prefix
        matched = any(code.startswith(p) for p in valid_prefixes)
        if not matched:
            raise ValidationError(
                f"❌ پیشوند کد نامعتبر است.\n"
                f"پیشوندهای مجاز: {', '.join(valid_prefixes)}"
            )

        # Remainder must be digits
        matched_prefix = next(p for p in valid_prefixes if code.startswith(p))
        suffix = code[len(matched_prefix):]
        if not suffix.isdigit():
            raise ValidationError(
                f"❌ بخش عددی کد باید فقط شامل اعداد باشد.\n"
                f"مثال: {matched_prefix}001"
            )
        return code

    @staticmethod
    def validate_product_line_prefix(value: str) -> str:
        """Validate a product line prefix — letters only, max 10 chars."""
        prefix = value.strip().upper()
        if not prefix:
            raise ValidationError("❌ پیشوند نمی‌تواند خالی باشد.")
        if len(prefix) > 10:
            raise ValidationError("❌ پیشوند نباید بیشتر از ۱۰ کاراکتر باشد.")
        if not prefix.isalpha():
            raise ValidationError("❌ پیشوند باید فقط شامل حروف لاتین باشد.")
        return prefix

    # ------------------------------------------------------------------
    # Group/Chat validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_chat_id(value: str) -> int:
        """Validate a Telegram chat ID (must be negative integer for groups)."""
        try:
            cid = int(value.strip())
        except (ValueError, TypeError):
            raise ValidationError(
                "❌ Chat ID باید عدد صحیح باشد.\n"
                "مثال: -1001234567890"
            )
        if cid >= 0:
            raise ValidationError(
                "❌ Chat ID گروه باید عدد منفی باشد.\n"
                "مثال: -1001234567890"
            )
        return cid

    # ------------------------------------------------------------------
    # Product line validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_product_name(value: str) -> str:
        """Validate a Persian product name."""
        name = value.strip()
        if not name:
            raise ValidationError("❌ نام محصول نمی‌تواند خالی باشد.")
        if len(name) > 50:
            raise ValidationError("❌ نام محصول نباید بیشتر از ۵۰ کاراکتر باشد.")
        return name

    @staticmethod
    def validate_icon(value: str) -> str:
        """Validate an emoji icon (basic length check)."""
        icon = value.strip()
        if not icon:
            raise ValidationError("❌ آیکون نمی‌تواند خالی باشد.")
        if len(icon) > 10:
            raise ValidationError("❌ آیکون نباید بیشتر از ۱۰ کاراکتر باشد.")
        return icon