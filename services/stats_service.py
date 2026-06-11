import pymysql
import logging
from config.database import get_db_connection
from utils.helpers import get_tehran_time


class StatsService:

    @staticmethod
    def get_product_line_stats():
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            now = get_tehran_time()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            week_start = today_start.replace(
                day=today_start.day - today_start.weekday()
            )

            from utils.helpers import to_utc_naive
            today_utc = to_utc_naive(today_start)
            week_utc = to_utc_naive(week_start)

            cursor.execute("""
                SELECT
                    pl.id,
                    pl.code_prefix,
                    pl.name_fa,
                    pl.icon,
                    pl.is_active,

                    COUNT(d.id) as total_all,
                    SUM(CASE WHEN d.status = 'pending'  THEN 1 ELSE 0 END) as pending_all,
                    SUM(CASE WHEN d.status = 'approved' THEN 1 ELSE 0 END) as approved_all,
                    SUM(CASE WHEN d.status = 'rejected' THEN 1 ELSE 0 END) as rejected_all,

                    SUM(CASE WHEN d.created_at >= %s THEN 1 ELSE 0 END) as submitted_week,
                    SUM(CASE WHEN d.status = 'approved' AND d.reviewed_at >= %s THEN 1 ELSE 0 END) as approved_week,
                    SUM(CASE WHEN d.status = 'rejected' AND d.reviewed_at >= %s THEN 1 ELSE 0 END) as rejected_week,

                    SUM(CASE WHEN d.created_at >= %s THEN 1 ELSE 0 END) as submitted_today,
                    SUM(CASE WHEN d.status = 'approved' AND d.reviewed_at >= %s THEN 1 ELSE 0 END) as approved_today,
                    SUM(CASE WHEN d.status = 'rejected' AND d.reviewed_at >= %s THEN 1 ELSE 0 END) as rejected_today

                FROM product_lines pl
                LEFT JOIN designs d ON pl.id = d.product_line_id
                GROUP BY pl.id, pl.code_prefix, pl.name_fa, pl.icon, pl.is_active
                ORDER BY pl.display_order
            """, (week_utc, week_utc, week_utc, today_utc, today_utc, today_utc))

            rows = cursor.fetchall()
            
            # Fetch specifically requested codes
            for row in rows:
                pl_id = row['id']
                
                cursor.execute("SELECT code FROM designs WHERE product_line_id = %s AND status = 'pending'", (pl_id,))
                row['pending_codes'] = [r['code'] for r in cursor.fetchall()]
                
                cursor.execute("SELECT code FROM designs WHERE product_line_id = %s AND status = 'approved' ORDER BY reviewed_at DESC LIMIT 10", (pl_id,))
                row['recent_approved'] = [r['code'] for r in cursor.fetchall()]
                
                cursor.execute("SELECT code FROM designs WHERE product_line_id = %s AND status = 'rejected' ORDER BY reviewed_at DESC LIMIT 10", (pl_id,))
                # Split out the _REJ_ timestamp cleanly for viewer
                row['recent_rejected'] = [r['code'].split('_REJ_')[0] for r in cursor.fetchall()]

            return rows
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_editor_stats():
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            from utils.helpers import to_utc_naive
            now = get_tehran_time()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            week_start = today_start.replace(day=today_start.day - today_start.weekday())
            today_utc = to_utc_naive(today_start)
            week_utc = to_utc_naive(week_start)

            cursor.execute("""
                SELECT
                    d.editor_user_id,
                    d.editor_name,
                    COUNT(*) as submitted_all,
                    SUM(CASE WHEN d.status = 'approved' THEN 1 ELSE 0 END) as approved_all,
                    SUM(CASE WHEN d.status = 'rejected' THEN 1 ELSE 0 END) as rejected_all,
                    SUM(CASE WHEN d.status = 'pending'  THEN 1 ELSE 0 END) as pending_all,
                    SUM(CASE WHEN d.created_at >= %s THEN 1 ELSE 0 END) as submitted_week,
                    SUM(CASE WHEN d.status = 'approved' AND d.reviewed_at >= %s THEN 1 ELSE 0 END) as approved_week,
                    SUM(CASE WHEN d.status = 'rejected' AND d.reviewed_at >= %s THEN 1 ELSE 0 END) as rejected_week,
                    SUM(CASE WHEN d.created_at >= %s THEN 1 ELSE 0 END) as submitted_today,
                    SUM(CASE WHEN d.status = 'approved' AND d.reviewed_at >= %s THEN 1 ELSE 0 END) as approved_today,
                    SUM(CASE WHEN d.status = 'rejected' AND d.reviewed_at >= %s THEN 1 ELSE 0 END) as rejected_today
                FROM designs d
                GROUP BY d.editor_user_id, d.editor_name
                ORDER BY submitted_all DESC
            """, (week_utc, week_utc, week_utc, today_utc, today_utc, today_utc))

            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_reviewer_stats():
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            from utils.helpers import to_utc_naive
            now = get_tehran_time()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            week_start = today_start.replace(day=today_start.day - today_start.weekday())
            today_utc = to_utc_naive(today_start)
            week_utc = to_utc_naive(week_start)

            cursor.execute("""
                SELECT
                    d.reviewer_user_id,
                    d.reviewer_name,
                    COUNT(*) as reviewed_all,
                    SUM(CASE WHEN d.status = 'approved' THEN 1 ELSE 0 END) as approved_all,
                    SUM(CASE WHEN d.status = 'rejected' THEN 1 ELSE 0 END) as rejected_all,
                    SUM(CASE WHEN d.reviewed_at >= %s THEN 1 ELSE 0 END) as reviewed_week,
                    SUM(CASE WHEN d.status = 'approved' AND d.reviewed_at >= %s THEN 1 ELSE 0 END) as approved_week,
                    SUM(CASE WHEN d.status = 'rejected' AND d.reviewed_at >= %s THEN 1 ELSE 0 END) as rejected_week,
                    SUM(CASE WHEN d.reviewed_at >= %s THEN 1 ELSE 0 END) as reviewed_today,
                    SUM(CASE WHEN d.status = 'approved' AND d.reviewed_at >= %s THEN 1 ELSE 0 END) as approved_today,
                    SUM(CASE WHEN d.status = 'rejected' AND d.reviewed_at >= %s THEN 1 ELSE 0 END) as rejected_today
                FROM designs d
                WHERE d.status IN ('approved', 'rejected')
                  AND d.reviewer_user_id IS NOT NULL
                GROUP BY d.reviewer_user_id, d.reviewer_name
                ORDER BY reviewed_all DESC
            """, (week_utc, week_utc, week_utc, today_utc, today_utc, today_utc))

            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_top_performers():
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            from utils.helpers import to_utc_naive
            now = get_tehran_time()
            today_utc = to_utc_naive(
                now.replace(hour=0, minute=0, second=0, microsecond=0)
            )

            cursor.execute("""
                SELECT editor_name, COUNT(*) as count
                FROM designs
                GROUP BY editor_user_id, editor_name
                ORDER BY count DESC
                LIMIT 1
            """)
            top_editor_all = cursor.fetchone()

            cursor.execute("""
                SELECT editor_name, COUNT(*) as count
                FROM designs
                WHERE created_at >= %s
                GROUP BY editor_user_id, editor_name
                ORDER BY count DESC
                LIMIT 1
            """, (today_utc,))
            top_editor_today = cursor.fetchone()

            cursor.execute("""
                SELECT reviewer_name, COUNT(*) as count
                FROM designs
                WHERE status IN ('approved', 'rejected')
                  AND reviewer_user_id IS NOT NULL
                GROUP BY reviewer_user_id, reviewer_name
                ORDER BY count DESC
                LIMIT 1
            """)
            top_reviewer_all = cursor.fetchone()

            cursor.execute("""
                SELECT reviewer_name, COUNT(*) as count
                FROM designs
                WHERE status IN ('approved', 'rejected')
                  AND reviewed_at >= %s
                  AND reviewer_user_id IS NOT NULL
                GROUP BY reviewer_user_id, reviewer_name
                ORDER BY count DESC
                LIMIT 1
            """, (today_utc,))
            top_reviewer_today = cursor.fetchone()

            return {
                'top_editor_all': top_editor_all,
                'top_editor_today': top_editor_today,
                'top_reviewer_all': top_reviewer_all,
                'top_reviewer_today': top_reviewer_today,
            }
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_system_stats():
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            from utils.helpers import to_utc_naive
            now = get_tehran_time()
            today_utc = to_utc_naive(
                now.replace(hour=0, minute=0, second=0, microsecond=0)
            )

            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'pending'  THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected,
                    SUM(CASE WHEN created_at >= %s    THEN 1 ELSE 0 END) as submitted_today
                FROM designs
            """, (today_utc,))
            totals = cursor.fetchone()

            cursor.execute("""
                SELECT role, COUNT(*) as count
                FROM users
                WHERE is_active = TRUE
                GROUP BY role
            """)
            users = {row['role']: row['count'] for row in cursor.fetchall()}
            uptime_str = StatsService._get_uptime()

            return {
                'totals': totals,
                'users': users,
                'uptime': uptime_str,
            }
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def _get_uptime():
        import subprocess
        try:
            result = subprocess.run(
                ['supervisorctl', '-c', '/home/selfnit4/supervisord.conf',
                 'status', 'tisa_bot'],
                capture_output=True, text=True, timeout=5
            )
            output = result.stdout.strip()
            if 'uptime' in output:
                uptime_part = output.split('uptime')[-1].strip()
                return uptime_part
            elif 'RUNNING' in output:
                return "در حال اجرا"
            else:
                return output or "نامشخص"
        except Exception as e:
            logging.warning(f"Could not read uptime: {e}")
            return "نامشخص"