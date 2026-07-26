import pymysql
import logging
from typing import Optional, Tuple, List
from datetime import datetime, timedelta
from config.database import get_db_connection
from utils.helpers import get_tehran_time, to_utc_naive
from utils.enums import DesignStatus


class SearchService:
    """Service for searching designs with various filters"""

    @staticmethod
    def search_designs(
        code_pattern: Optional[str] = None,
        status: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        product_line_id: Optional[int] = None,
        offset: int = 0,
        limit: int = 10
    ) -> Tuple[List[dict], int]:
        """
        Search designs with filters.

        Args:
            code_pattern: Exact or partial code match (e.g., "TS001" or "TS")
            status: Filter by status (pending, approved, rejected, deleted) or None for all
            date_from: Start date (inclusive)
            date_to: End date (inclusive)
            product_line_id: Filter by product line
            offset: Pagination offset
            limit: Results per page

        Returns:
            (results: List[dict], total_count: int)
        """
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        try:
            # Build WHERE clauses dynamically
            where_clauses = []
            params = []

            if code_pattern:
                # Support both exact and partial match
                if '%' in code_pattern or '_' in code_pattern:
                    where_clauses.append("d.code LIKE %s")
                    params.append(code_pattern)
                else:
                    # Partial match - search for codes starting with pattern
                    where_clauses.append("d.code LIKE %s")
                    params.append(f"{code_pattern}%")

            if status:
                where_clauses.append("d.status = %s")
                params.append(status)

            if date_from:
                date_from_utc = to_utc_naive(date_from)
                where_clauses.append("d.created_at >= %s")
                params.append(date_from_utc)

            if date_to:
                # Include the entire day
                date_to_end = date_to.replace(hour=23, minute=59, second=59)
                date_to_utc = to_utc_naive(date_to_end)
                where_clauses.append("d.created_at <= %s")
                params.append(date_to_utc)

            if product_line_id:
                where_clauses.append("d.product_line_id = %s")
                params.append(product_line_id)

            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

            # Count total results
            count_query = f"""
                SELECT COUNT(*) as total
                FROM designs d
                WHERE {where_sql}
            """
            cursor.execute(count_query, params)
            total_count = cursor.fetchone()['total']

            # Get paginated results with product line info
            results_query = f"""
                SELECT
                    d.id, d.code, d.status, d.product_line_id,
                    d.editor_user_id, d.editor_name,
                    d.reviewer_user_id, d.reviewer_name,
                    d.created_at, d.reviewed_at,
                    pl.name_fa as product_name,
                    pl.icon as product_icon,
                    pl.code_prefix
                FROM designs d
                JOIN product_lines pl ON d.product_line_id = pl.id
                WHERE {where_sql}
                ORDER BY d.created_at DESC
                LIMIT %s OFFSET %s
            """
            cursor.execute(results_query, params + [limit, offset])
            results = cursor.fetchall()

            return results, total_count

        except Exception as e:
            logging.error(f"Search failed: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_quick_date_range(period: str) -> Tuple[datetime, datetime]:
        """
        Get date range for quick filters.

        Args:
            period: 'today', 'week', 'month', 'all'

        Returns:
            (date_from, date_to) in Tehran timezone
        """
        now = get_tehran_time()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        if period == 'today':
            return today_start, now

        elif period == 'week':
            # Start of current week (Saturday in Iranian calendar)
            days_since_saturday = (today_start.weekday() + 2) % 7
            week_start = today_start - timedelta(days=days_since_saturday)
            return week_start, now

        elif period == 'month':
            # Start of current month
            month_start = today_start.replace(day=1)
            return month_start, now

        elif period == 'all':
            # No date filter
            return None, None

        else:
            raise ValueError(f"Invalid period: {period}")
