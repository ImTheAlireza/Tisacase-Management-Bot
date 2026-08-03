import pymysql
import logging
from datetime import timedelta
from config.database import get_db_connection
from utils.helpers import get_tehran_time, to_utc_naive
from utils.enums import DesignStatus

PERSIAN_WEEKDAYS = ['شنبه', 'یکشنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه']


def _persian_weekday(dt) -> str:
    """Convert Python weekday (Mon=0) to Iranian weekday (Sat=0)"""
    return PERSIAN_WEEKDAYS[(dt.weekday() + 2) % 7]


def _month_start(dt):
    """Return the first moment of dt's Gregorian month in the same timezone."""
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _previous_month_start(month_start):
    """Return the first moment of the previous Gregorian month."""
    if month_start.month == 1:
        return month_start.replace(year=month_start.year - 1, month=12)
    return month_start.replace(month=month_start.month - 1)


class StatsService:

    @staticmethod
    def get_product_line_stats() -> list[dict]:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            now = get_tehran_time()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            days_since_saturday = (today_start.weekday() + 2) % 7
            week_start = today_start - timedelta(days=days_since_saturday)
            prev_week_start = week_start - timedelta(days=7)
            yesterday_start = today_start - timedelta(days=1)
            month_start = _month_start(today_start)
            prev_month_start = _previous_month_start(month_start)

            today_utc = to_utc_naive(today_start)
            yesterday_utc = to_utc_naive(yesterday_start)
            week_utc = to_utc_naive(week_start)
            prev_week_utc = to_utc_naive(prev_week_start)
            month_utc = to_utc_naive(month_start)
            prev_month_utc = to_utc_naive(prev_month_start)

            # Main aggregation query
            # "all-time" counts respect stats_reset_at; "today"/"week" always show current period
            # Excludes deleted designs from all counts
            cursor.execute("""
                SELECT
                    pl.id,
                    pl.code_prefix,
                    pl.name_fa,
                    pl.icon,
                    pl.is_active,
                    pl.stats_reset_at,

                    SUM(CASE WHEN d.status != 'deleted'
                             AND (pl.stats_reset_at IS NULL OR d.created_at >= pl.stats_reset_at)
                             THEN 1 ELSE 0 END)                                    AS total_all,
                    SUM(CASE WHEN d.status = 'pending'
                             AND (pl.stats_reset_at IS NULL OR d.created_at >= pl.stats_reset_at)
                             THEN 1 ELSE 0 END)                                    AS pending_all,
                    SUM(CASE WHEN d.status = 'approved'
                             AND (pl.stats_reset_at IS NULL OR d.reviewed_at >= pl.stats_reset_at)
                             THEN 1 ELSE 0 END)                                    AS approved_all,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND (pl.stats_reset_at IS NULL OR d.reviewed_at >= pl.stats_reset_at)
                             THEN 1 ELSE 0 END)                                    AS rejected_all,

                    SUM(CASE WHEN d.status != 'deleted'
                             AND d.created_at  >= %s THEN 1 ELSE 0 END)            AS submitted_week,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s THEN 1 ELSE 0 END)            AS approved_week,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s THEN 1 ELSE 0 END)            AS rejected_week,

                    SUM(CASE WHEN d.status != 'deleted'
                             AND d.created_at >= %s AND d.created_at < %s
                             THEN 1 ELSE 0 END)                                    AS submitted_prev_week,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                    AS approved_prev_week,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                    AS rejected_prev_week,

                    SUM(CASE WHEN d.status != 'deleted'
                             AND d.created_at >= %s THEN 1 ELSE 0 END)             AS submitted_month,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s THEN 1 ELSE 0 END)            AS approved_month,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s THEN 1 ELSE 0 END)            AS rejected_month,

                    SUM(CASE WHEN d.status != 'deleted'
                             AND d.created_at >= %s AND d.created_at < %s
                             THEN 1 ELSE 0 END)                                    AS submitted_prev_month,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                    AS approved_prev_month,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                    AS rejected_prev_month,

                    SUM(CASE WHEN d.status != 'deleted'
                             AND d.created_at  >= %s THEN 1 ELSE 0 END)            AS submitted_today,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s THEN 1 ELSE 0 END)            AS approved_today,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s THEN 1 ELSE 0 END)            AS rejected_today,

                    SUM(CASE WHEN d.status != 'deleted'
                             AND d.created_at >= %s AND d.created_at < %s
                             THEN 1 ELSE 0 END)                                    AS submitted_yesterday,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                    AS approved_yesterday,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                    AS rejected_yesterday

                FROM product_lines pl
                LEFT JOIN designs d ON pl.id = d.product_line_id
                GROUP BY pl.id, pl.code_prefix, pl.name_fa, pl.icon, pl.is_active, pl.stats_reset_at
                ORDER BY pl.display_order
            """, (
                week_utc, week_utc, week_utc,
                prev_week_utc, week_utc,
                prev_week_utc, week_utc,
                prev_week_utc, week_utc,
                month_utc, month_utc, month_utc,
                prev_month_utc, month_utc,
                prev_month_utc, month_utc,
                prev_month_utc, month_utc,
                today_utc, today_utc, today_utc,
                yesterday_utc, today_utc,
                yesterday_utc, today_utc,
                yesterday_utc, today_utc
            ))

            rows = cursor.fetchall()

            if not rows:
                return []

            # FIX: Fetch all code details in ONE query per status instead of 3 per row
            pl_ids = [r['id'] for r in rows]
            pl_id_index = {r['id']: r for r in rows}

            # Initialize lists
            for row in rows:
                row['pending_codes']    = []
                row['recent_approved']  = []
                row['recent_rejected']  = []

            # Single query for all pending codes
            format_strings = ','.join(['%s'] * len(pl_ids))

            cursor.execute(f"""
                SELECT product_line_id, code
                FROM designs
                WHERE product_line_id IN ({format_strings})
                  AND status = 'pending'
                ORDER BY product_line_id, created_at DESC
            """, pl_ids)

            for r in cursor.fetchall():
                pl_id_index[r['product_line_id']]['pending_codes'].append(r['code'])

            # Single query for recent approved (top 10 per product line)
            cursor.execute(f"""
                SELECT product_line_id, code, reviewed_at
                FROM (
                    SELECT product_line_id, code, reviewed_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY product_line_id
                               ORDER BY reviewed_at DESC
                           ) AS rn
                    FROM designs
                    WHERE product_line_id IN ({format_strings})
                      AND status = 'approved'
                ) ranked
                WHERE rn <= 10
                ORDER BY product_line_id, reviewed_at DESC
            """, pl_ids)

            for r in cursor.fetchall():
                pl_id_index[r['product_line_id']]['recent_approved'].append(r['code'])

            # Single query for recent rejected (top 10 per product line)
            cursor.execute(f"""
                SELECT product_line_id, code, reviewed_at
                FROM (
                    SELECT product_line_id, code, reviewed_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY product_line_id
                               ORDER BY reviewed_at DESC
                           ) AS rn
                    FROM designs
                    WHERE product_line_id IN ({format_strings})
                      AND status = 'rejected'
                ) ranked
                WHERE rn <= 10
                ORDER BY product_line_id, reviewed_at DESC
            """, pl_ids)

            for r in cursor.fetchall():
                clean_code = r['code'].split('_REJ_')[0]
                pl_id_index[r['product_line_id']]['recent_rejected'].append(clean_code)

            return rows

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_editor_stats() -> list[dict]:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            now = get_tehran_time()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            days_since_saturday = (today_start.weekday() + 2) % 7
            week_start = today_start - timedelta(days=days_since_saturday)
            prev_week_start = week_start - timedelta(days=7)
            yesterday_start = today_start - timedelta(days=1)
            month_start = _month_start(today_start)
            prev_month_start = _previous_month_start(month_start)

            today_utc = to_utc_naive(today_start)
            yesterday_utc = to_utc_naive(yesterday_start)
            week_utc  = to_utc_naive(week_start)
            prev_week_utc = to_utc_naive(prev_week_start)
            month_utc = to_utc_naive(month_start)
            prev_month_utc = to_utc_naive(prev_month_start)

            cursor.execute("""
                SELECT
                    d.editor_user_id,
                    d.editor_name,
                    MAX(u.stats_reset_at) AS stats_reset_at,
                    COUNT(*)                                                     AS submitted_all,
                    SUM(CASE WHEN d.status = 'approved' THEN 1 ELSE 0 END)      AS approved_all,
                    SUM(CASE WHEN d.status = 'rejected' THEN 1 ELSE 0 END)      AS rejected_all,
                    SUM(CASE WHEN d.status = 'pending'  THEN 1 ELSE 0 END)      AS pending_all,
                    SUM(CASE WHEN d.created_at  >= %s   THEN 1 ELSE 0 END)      AS submitted_week,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s    THEN 1 ELSE 0 END)      AS approved_week,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s    THEN 1 ELSE 0 END)      AS rejected_week,
                    SUM(CASE WHEN d.created_at >= %s AND d.created_at < %s
                             THEN 1 ELSE 0 END)                                 AS submitted_prev_week,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS approved_prev_week,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS rejected_prev_week,
                    SUM(CASE WHEN d.created_at  >= %s   THEN 1 ELSE 0 END)      AS submitted_month,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s    THEN 1 ELSE 0 END)      AS approved_month,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s    THEN 1 ELSE 0 END)      AS rejected_month,
                    SUM(CASE WHEN d.created_at >= %s AND d.created_at < %s
                             THEN 1 ELSE 0 END)                                 AS submitted_prev_month,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS approved_prev_month,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS rejected_prev_month,
                    SUM(CASE WHEN d.created_at  >= %s   THEN 1 ELSE 0 END)      AS submitted_today,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s    THEN 1 ELSE 0 END)      AS approved_today,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s    THEN 1 ELSE 0 END)      AS rejected_today,
                    SUM(CASE WHEN d.created_at >= %s AND d.created_at < %s
                             THEN 1 ELSE 0 END)                                 AS submitted_yesterday,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS approved_yesterday,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS rejected_yesterday
                FROM designs d
                LEFT JOIN users u ON d.editor_user_id = u.user_id
                WHERE (u.stats_reset_at IS NULL OR d.created_at >= u.stats_reset_at)
                GROUP BY d.editor_user_id, d.editor_name
                ORDER BY submitted_all DESC
            """, (
                week_utc, week_utc, week_utc,
                prev_week_utc, week_utc,
                prev_week_utc, week_utc,
                prev_week_utc, week_utc,
                month_utc, month_utc, month_utc,
                prev_month_utc, month_utc,
                prev_month_utc, month_utc,
                prev_month_utc, month_utc,
                today_utc, today_utc, today_utc,
                yesterday_utc, today_utc,
                yesterday_utc, today_utc,
                yesterday_utc, today_utc
            ))

            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_reviewer_stats() -> list[dict]:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            now = get_tehran_time()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            days_since_saturday = (today_start.weekday() + 2) % 7
            week_start = today_start - timedelta(days=days_since_saturday)
            prev_week_start = week_start - timedelta(days=7)
            yesterday_start = today_start - timedelta(days=1)
            month_start = _month_start(today_start)
            prev_month_start = _previous_month_start(month_start)

            today_utc = to_utc_naive(today_start)
            yesterday_utc = to_utc_naive(yesterday_start)
            week_utc  = to_utc_naive(week_start)
            prev_week_utc = to_utc_naive(prev_week_start)
            month_utc = to_utc_naive(month_start)
            prev_month_utc = to_utc_naive(prev_month_start)

            cursor.execute("""
                SELECT
                    d.reviewer_user_id,
                    d.reviewer_name,
                    MAX(u.stats_reset_at) AS stats_reset_at,
                    COUNT(*)                                                     AS reviewed_all,
                    SUM(CASE WHEN d.status = 'approved' THEN 1 ELSE 0 END)      AS approved_all,
                    SUM(CASE WHEN d.status = 'rejected' THEN 1 ELSE 0 END)      AS rejected_all,
                    SUM(CASE WHEN d.reviewed_at >= %s   THEN 1 ELSE 0 END)      AS reviewed_week,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s    THEN 1 ELSE 0 END)      AS approved_week,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s    THEN 1 ELSE 0 END)      AS rejected_week,
                    SUM(CASE WHEN d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS reviewed_prev_week,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS approved_prev_week,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS rejected_prev_week,
                    SUM(CASE WHEN d.reviewed_at >= %s   THEN 1 ELSE 0 END)      AS reviewed_month,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s    THEN 1 ELSE 0 END)      AS approved_month,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s    THEN 1 ELSE 0 END)      AS rejected_month,
                    SUM(CASE WHEN d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS reviewed_prev_month,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS approved_prev_month,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS rejected_prev_month,
                    SUM(CASE WHEN d.reviewed_at >= %s   THEN 1 ELSE 0 END)      AS reviewed_today,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s    THEN 1 ELSE 0 END)      AS approved_today,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s    THEN 1 ELSE 0 END)      AS rejected_today,
                    SUM(CASE WHEN d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS reviewed_yesterday,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS approved_yesterday,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                                 AS rejected_yesterday
                FROM designs d
                LEFT JOIN users u ON d.reviewer_user_id = u.user_id
                WHERE d.status IN ('approved', 'rejected')
                  AND d.reviewer_user_id IS NOT NULL
                  AND (u.stats_reset_at IS NULL OR d.reviewed_at >= u.stats_reset_at)
                GROUP BY d.reviewer_user_id, d.reviewer_name
                ORDER BY reviewed_all DESC
            """, (
                week_utc, week_utc, week_utc,
                prev_week_utc, week_utc,
                prev_week_utc, week_utc,
                prev_week_utc, week_utc,
                month_utc, month_utc, month_utc,
                prev_month_utc, month_utc,
                prev_month_utc, month_utc,
                prev_month_utc, month_utc,
                today_utc, today_utc, today_utc,
                yesterday_utc, today_utc,
                yesterday_utc, today_utc,
                yesterday_utc, today_utc
            ))

            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_top_performers() -> dict:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            now = get_tehran_time()
            today_utc = to_utc_naive(
                now.replace(hour=0, minute=0, second=0, microsecond=0)
            )

            cursor.execute("""
                SELECT d.editor_name, COUNT(*) as count
                FROM designs d
                LEFT JOIN users u ON d.editor_user_id = u.user_id
                WHERE (u.stats_reset_at IS NULL OR d.created_at >= u.stats_reset_at)
                GROUP BY d.editor_user_id, d.editor_name
                ORDER BY count DESC LIMIT 1
            """)
            top_editor_all = cursor.fetchone()

            cursor.execute("""
                SELECT editor_name, COUNT(*) as count
                FROM designs
                WHERE created_at >= %s
                GROUP BY editor_user_id, editor_name
                ORDER BY count DESC LIMIT 1
            """, (today_utc,))
            top_editor_today = cursor.fetchone()

            cursor.execute("""
                SELECT d.reviewer_name, COUNT(*) as count
                FROM designs d
                LEFT JOIN users u ON d.reviewer_user_id = u.user_id
                WHERE d.status IN ('approved', 'rejected')
                  AND d.reviewer_user_id IS NOT NULL
                  AND (u.stats_reset_at IS NULL OR d.reviewed_at >= u.stats_reset_at)
                GROUP BY d.reviewer_user_id, d.reviewer_name
                ORDER BY count DESC LIMIT 1
            """)
            top_reviewer_all = cursor.fetchone()

            cursor.execute("""
                SELECT reviewer_name, COUNT(*) as count
                FROM designs
                WHERE status IN ('approved', 'rejected')
                  AND reviewed_at >= %s
                  AND reviewer_user_id IS NOT NULL
                GROUP BY reviewer_user_id, reviewer_name
                ORDER BY count DESC LIMIT 1
            """, (today_utc,))
            top_reviewer_today = cursor.fetchone()

            return {
                'top_editor_all':     top_editor_all,
                'top_editor_today':   top_editor_today,
                'top_reviewer_all':   top_reviewer_all,
                'top_reviewer_today': top_reviewer_today,
            }
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_system_stats() -> dict:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            now = get_tehran_time()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            yesterday_start = today_start - timedelta(days=1)
            month_start = _month_start(today_start)
            prev_month_start = _previous_month_start(month_start)

            today_utc = to_utc_naive(today_start)
            yesterday_utc = to_utc_naive(yesterday_start)
            month_utc = to_utc_naive(month_start)
            prev_month_utc = to_utc_naive(prev_month_start)

            cursor.execute("""
                SELECT
                    COUNT(*)                                             AS total,
                    SUM(CASE WHEN status = 'pending'  THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
                    SUM(CASE WHEN created_at >= %s    THEN 1 ELSE 0 END) AS submitted_today,
                    SUM(CASE WHEN created_at >= %s AND created_at < %s
                             THEN 1 ELSE 0 END)                         AS submitted_yesterday,
                    SUM(CASE WHEN created_at >= %s    THEN 1 ELSE 0 END) AS submitted_month,
                    SUM(CASE WHEN status = 'approved'
                             AND reviewed_at >= %s THEN 1 ELSE 0 END)   AS approved_month,
                    SUM(CASE WHEN status = 'rejected'
                             AND reviewed_at >= %s THEN 1 ELSE 0 END)   AS rejected_month,
                    SUM(CASE WHEN created_at >= %s AND created_at < %s
                             THEN 1 ELSE 0 END)                         AS submitted_prev_month,
                    SUM(CASE WHEN status = 'approved'
                             AND reviewed_at >= %s AND reviewed_at < %s
                             THEN 1 ELSE 0 END)                         AS approved_prev_month,
                    SUM(CASE WHEN status = 'rejected'
                             AND reviewed_at >= %s AND reviewed_at < %s
                             THEN 1 ELSE 0 END)                         AS rejected_prev_month
                FROM designs
                WHERE status != 'deleted'
            """, (
                today_utc,
                yesterday_utc, today_utc,
                month_utc, month_utc, month_utc,
                prev_month_utc, month_utc,
                prev_month_utc, month_utc,
                prev_month_utc, month_utc
            ))
            totals = cursor.fetchone()

            cursor.execute("""
                SELECT role, COUNT(*) as count
                FROM users
                WHERE is_active = TRUE
                GROUP BY role
            """)
            users = {row['role']: row['count'] for row in cursor.fetchall()}

            return {
                'totals': totals,
                'users':  users,
                'uptime': StatsService._get_uptime(),
            }
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def _get_uptime() -> str:
        import subprocess
        from config.settings import SUPERVISORD_CONF, SUPERVISOR_PROCESS
        try:
            result = subprocess.run(
                ['supervisorctl', '-c', SUPERVISORD_CONF, 'status', SUPERVISOR_PROCESS],
                capture_output=True, text=True, timeout=5
            )
            output = result.stdout.strip()
            if 'uptime' in output:
                return output.split('uptime')[-1].strip()
            elif 'RUNNING' in output:
                return "در حال اجرا"
            return output or "نامشخص"
        except Exception as e:
            logging.warning(f"Could not read uptime: {e}")
            return "نامشخص"

    @staticmethod
    def get_daily_summary() -> dict:
        """Gather all data needed for the rich daily log"""
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            now = get_tehran_time()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            days_since_saturday = (today_start.weekday() + 2) % 7
            week_start = today_start - timedelta(days=days_since_saturday)
            yesterday_start = today_start - timedelta(days=1)
            today_utc = to_utc_naive(today_start)
            yesterday_utc = to_utc_naive(yesterday_start)
            week_utc = to_utc_naive(week_start)

            # 1. Today's activity per product line
            cursor.execute("""
                SELECT
                    pl.code_prefix, pl.name_fa, pl.icon,
                    SUM(CASE WHEN d.created_at >= %s THEN 1 ELSE 0 END)    AS submitted_today,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s THEN 1 ELSE 0 END)   AS approved_today,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s THEN 1 ELSE 0 END)   AS rejected_today,
                    SUM(CASE WHEN d.created_at >= %s AND d.created_at < %s
                             THEN 1 ELSE 0 END)                           AS submitted_yesterday,
                    SUM(CASE WHEN d.status = 'approved'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                           AS approved_yesterday,
                    SUM(CASE WHEN d.status = 'rejected'
                             AND d.reviewed_at >= %s AND d.reviewed_at < %s
                             THEN 1 ELSE 0 END)                           AS rejected_yesterday
                FROM product_lines pl
                LEFT JOIN designs d ON pl.id = d.product_line_id
                WHERE pl.is_active = TRUE
                GROUP BY pl.id, pl.code_prefix, pl.name_fa, pl.icon
                ORDER BY pl.display_order
            """, (
                today_utc, today_utc, today_utc,
                yesterday_utc, today_utc,
                yesterday_utc, today_utc,
                yesterday_utc, today_utc
            ))
            today_lines = cursor.fetchall()

            # 2. Pending codes (still waiting for review)
            cursor.execute("""
                SELECT d.code, pl.name_fa, pl.icon, d.created_at
                FROM designs d
                JOIN product_lines pl ON d.product_line_id = pl.id
                WHERE d.status = 'pending'
                ORDER BY d.created_at ASC
            """)
            pending_codes = cursor.fetchall()

            # 3. Today's top editor
            cursor.execute("""
                SELECT editor_name, COUNT(*) as count
                FROM designs
                WHERE created_at >= %s
                GROUP BY editor_user_id, editor_name
                ORDER BY count DESC LIMIT 1
            """, (today_utc,))
            top_editor_today = cursor.fetchone()

            # 4. Today's top reviewer
            cursor.execute("""
                SELECT reviewer_name, COUNT(*) as count
                FROM designs
                WHERE status IN ('approved', 'rejected')
                  AND reviewed_at >= %s
                  AND reviewer_user_id IS NOT NULL
                GROUP BY reviewer_user_id, reviewer_name
                ORDER BY count DESC LIMIT 1
            """, (today_utc,))
            top_reviewer_today = cursor.fetchone()

            # 5. Weekly totals
            cursor.execute("""
                SELECT
                    COUNT(*)                                                     AS submitted_week,
                    SUM(CASE WHEN status = 'approved'
                             AND reviewed_at >= %s THEN 1 ELSE 0 END)           AS approved_week,
                    SUM(CASE WHEN status = 'rejected'
                             AND reviewed_at >= %s THEN 1 ELSE 0 END)           AS rejected_week
                FROM designs
                WHERE created_at >= %s
            """, (week_utc, week_utc, week_utc))
            weekly = cursor.fetchone()

            # 6. System totals (excludes deleted)
            cursor.execute("""
                SELECT
                    COUNT(*)                                                     AS total,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END)         AS pending,
                    SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END)        AS approved
                FROM designs
                WHERE status != 'deleted'
            """)
            system = cursor.fetchone()

            return {
                'date': now.strftime('%Y/%m/%d'),
                'weekday': _persian_weekday(now),
                'today_lines': today_lines,
                'pending_codes': pending_codes,
                'top_editor_today': top_editor_today,
                'top_reviewer_today': top_reviewer_today,
                'weekly': weekly,
                'system': system,
            }
        finally:
            cursor.close()
            conn.close()
