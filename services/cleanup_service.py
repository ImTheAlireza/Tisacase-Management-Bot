import logging
from models.design import Design
from utils.enums import DesignStatus

class CleanupService:
    """Service for cleanup and maintenance operations"""

    @staticmethod
    async def delete_design_completely(code: str, bot) -> dict:
        """
        Delete a design completely - wrapper for Design.delete_completely
        """
        return await Design.delete_completely(code, bot)

    @staticmethod
    async def cleanup_orphaned_pending_designs(bot, max_age_hours: int = 24) -> dict:
        """
        Find and delete pending designs older than X hours with no files.

        Args:
            bot: Telegram bot instance
            max_age_hours: Delete pending designs older than this (default 24h)

        Returns:
            {
                'scanned': int,
                'deleted': int,
                'codes': list,
                'errors': list
            }
        """
        from datetime import timedelta
        from utils.helpers import get_tehran_time, to_utc_naive
        import pytz

        now = get_tehran_time()
        cutoff_tehran = now - timedelta(hours=max_age_hours)

        # Convert cutoff to UTC naive for comparison with DB values
        cutoff_utc_naive = to_utc_naive(cutoff_tehran)

        all_pending = Design.get_all_pending()

        scanned = 0
        deleted = 0
        deleted_codes = []
        errors = []

        for design in all_pending:
            scanned += 1

            # created_at from DB is UTC naive — compare directly with utc naive cutoff
            if design.created_at is None:
                continue

            # Normalize created_at — strip timezone if present, treat as UTC naive
            created_at = design.created_at
            if hasattr(created_at, 'tzinfo') and created_at.tzinfo is not None:
                # Already aware — convert to UTC naive
                created_at = created_at.astimezone(pytz.UTC).replace(tzinfo=None)

            is_old = created_at < cutoff_utc_naive
            has_no_files = (
                len(design.mockup_file_ids) == 0 and
                len(design.print_file_ids) == 0
            )

            if is_old and has_no_files:
                try:
                    result = await Design.delete_completely(design.code, bot)
                    if result['database_deleted']:
                        deleted += 1
                        deleted_codes.append(design.code)
                    else:
                        errors.extend(result['errors'])
                except Exception as e:
                    errors.append(f"حذف طرح {design.code} ناموفق بود: {e}")
                    logging.error(f"Cleanup failed for {design.code}: {e}")

        return {
            'scanned': scanned,
            'deleted': deleted,
            'codes': deleted_codes,
            'errors': errors
        }