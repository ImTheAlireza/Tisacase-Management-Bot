import subprocess
import zipfile
import tempfile
import os
import json
import csv
import shutil
import pymysql
import logging
import asyncio
from datetime import datetime
from config.database import get_db_connection
from config.settings import DB_CONFIG
from utils.helpers import get_tehran_time

class BackupService:

    @staticmethod
    def create_database_backup():
        """
        FIX: Use MySQL config file to avoid password in process list
        """
        try:
            # Create temporary MySQL config file
            temp_dir = tempfile.mkdtemp()
            config_file = os.path.join(temp_dir, 'my.cnf')
            
            try:
                # Write credentials to config file with secure permissions
                with open(config_file, 'w') as f:
                    f.write('[client]\n')
                    f.write(f"host={DB_CONFIG['host']}\n")
                    f.write(f"user={DB_CONFIG['user']}\n")
                    f.write(f"password={DB_CONFIG['password']}\n")
                
                # Set file permissions to 600 (owner read/write only)
                os.chmod(config_file, 0o600)
                
                # Run mysqldump using the config file
                dump_cmd = [
                    'mysqldump',
                    f"--defaults-file={config_file}",
                    DB_CONFIG['database'],
                    '--single-transaction',
                    '--routines',
                    '--triggers',
                    '--add-drop-table'
                ]
                
                result = subprocess.run(
                    dump_cmd, capture_output=True, text=True,
                    check=True, timeout=300
                )
                
                return result.stdout
                
            finally:
                # Clean up temp config file
                try:
                    os.remove(config_file)
                    os.rmdir(temp_dir)
                except Exception as e:
                    logging.warning(f"Failed to cleanup temp mysqldump config: {e}")
                    
        except subprocess.TimeoutExpired:
            logging.error("Database backup timed out (>5 minutes)")
            return None
        except Exception as e:
            logging.error(f"Database backup failed: {e}")
            return None

    @staticmethod
    def create_codes_export():
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            from datetime import datetime, date
            
            # FIX: Custom JSON encoder for dates
            def json_serial(obj):
                """JSON serializer for objects not serializable by default json code"""
                if isinstance(obj, (datetime, date)):
                    return obj.isoformat()
                return str(obj)
            
            codes_data = {}
            cursor.execute("SELECT * FROM product_lines ORDER BY display_order")
            product_lines = cursor.fetchall()

            for pl in product_lines:
                prefix = pl['code_prefix']
                codes_data[prefix] = {
                    'product_info': {
                        'name': pl['name_fa'],
                        'icon': pl['icon'],
                        'active': pl['is_active']
                    },
                    'designs': [],
                    'locked_codes': []
                }
                cursor.execute("""
                    SELECT code, status, editor_name, reviewer_name,
                           created_at, reviewed_at
                    FROM designs
                    WHERE product_line_id = %s
                    ORDER BY created_at DESC
                """, (pl['id'],))
                codes_data[prefix]['designs'] = cursor.fetchall()

                cursor.execute("""
                    SELECT code, is_manual, locked_at, notes
                    FROM designs_locked_codes
                    WHERE product_line_id = %s
                    ORDER BY locked_at DESC
                """, (pl['id'],))
                codes_data[prefix]['locked_codes'] = cursor.fetchall()

            # FIX: Use custom serializer
            return json.dumps(codes_data, ensure_ascii=False, indent=2, default=json_serial)
        except Exception as e:
            logging.error(f"Codes export failed: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def create_csv_export(time_range: str = 'all') -> str:
        """Export design data as CSV with Persian column headers.

        Args:
            time_range: 'week', 'month', or 'all'

        Returns:
            Path to generated CSV file, or None on failure
        """
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            where_clause = ""
            if time_range == 'week':
                where_clause = "WHERE d.created_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 7 DAY)"
            elif time_range == 'month':
                where_clause = "WHERE d.created_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 MONTH)"

            query = f"""
                SELECT
                    d.code,
                    pl.name_fa AS product_line,
                    pl.icon AS product_icon,
                    CASE d.status
                        WHEN 'pending' THEN 'در انتظار'
                        WHEN 'approved' THEN 'تایید شده'
                        WHEN 'rejected' THEN 'رد شده'
                        WHEN 'deleted' THEN 'حذف شده'
                        ELSE d.status
                    END AS status_fa,
                    d.editor_name,
                    d.reviewer_name,
                    d.created_at,
                    d.reviewed_at,
                    d.final_name
                FROM designs d
                JOIN product_lines pl ON d.product_line_id = pl.id
                {where_clause}
                ORDER BY d.created_at DESC
            """

            cursor.execute(query)
            rows = cursor.fetchall()

            if not rows:
                logging.warning("CSV export: no data found for time_range=%s", time_range)
                return None

            now_tehran = get_tehran_time()
            timestamp = now_tehran.strftime('%Y%m%d_%H%M')
            temp_dir = tempfile.mkdtemp()
            csv_filename = f"tisa_export_{time_range}_{timestamp}.csv"
            csv_path = os.path.join(temp_dir, csv_filename)

            headers = [
                'کد', 'خط تولید', 'آیکون', 'وضعیت',
                'طراح', 'ناظر', 'تاریخ ثبت', 'تاریخ بررسی', 'نام نهایی'
            ]

            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(headers)

                for row in rows:
                    created = row['created_at'].strftime('%Y-%m-%d %H:%M') if row['created_at'] else ''
                    reviewed = row['reviewed_at'].strftime('%Y-%m-%d %H:%M') if row['reviewed_at'] else ''

                    writer.writerow([
                        row['code'],
                        row['product_line'],
                        row['product_icon'],
                        row['status_fa'],
                        row['editor_name'] or '',
                        row['reviewer_name'] or '',
                        created,
                        reviewed,
                        row['final_name'] or ''
                    ])

            logging.info(f"CSV export created: {csv_filename} ({len(rows)} rows)")
            return csv_path

        except Exception as e:
            logging.error(f"CSV export failed: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_stats_summary():
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            stats = {
                'backup_timestamp': get_tehran_time().isoformat(),
                'product_lines': {},
                'users': {}
            }
            cursor.execute("""
                SELECT pl.code_prefix, pl.name_fa,
                       COUNT(CASE WHEN d.status = 'pending' THEN 1 END) as pending,
                       COUNT(CASE WHEN d.status = 'approved' THEN 1 END) as approved,
                       COUNT(CASE WHEN d.status = 'rejected' THEN 1 END) as rejected
                FROM product_lines pl
                LEFT JOIN designs d ON pl.id = d.product_line_id
                WHERE pl.is_active = TRUE
                GROUP BY pl.id, pl.code_prefix, pl.name_fa
            """)
            for row in cursor.fetchall():
                stats['product_lines'][row['code_prefix']] = {
                    'name': row['name_fa'],
                    DesignStatus.PENDING: row[DesignStatus.PENDING] or 0,
                    DesignStatus.APPROVED: row[DesignStatus.APPROVED] or 0,
                    DesignStatus.REJECTED: row[DesignStatus.REJECTED] or 0
                }

            cursor.execute("""
                SELECT role, COUNT(*) as count
                FROM users WHERE is_active = TRUE GROUP BY role
            """)
            for row in cursor.fetchall():
                stats['users'][row['role']] = row['count']

            return stats
        except Exception as e:
            logging.error(f"Stats summary failed: {e}")
            return {}
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    async def create_daily_backup_zip():
        now_tehran = get_tehran_time()
        timestamp = now_tehran.strftime('%Y%m%d_%H%M')
        zip_filename = f"tisa_backup_{timestamp}.zip"
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, zip_filename)

        try:
            # FIX: Run blocking operations in executor
            loop = asyncio.get_event_loop()
            
            logging.info("Creating database dump...")
            sql_dump = await loop.run_in_executor(
                None, BackupService.create_database_backup
            )
            
            codes_json = await loop.run_in_executor(
                None, BackupService.create_codes_export
            )
            
            stats = await loop.run_in_executor(
                None, BackupService.get_stats_summary
            )
            
            # FIX: Wrap the entire ZIP creation in executor since it's all blocking I/O
            def create_zip_file():
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    if sql_dump:
                        zipf.writestr(f"database_{timestamp}.sql", sql_dump)
                        logging.info(f"✅ Database dump added ({len(sql_dump)} bytes)")
                    else:
                        logging.warning("⚠️ Database dump failed, skipping")

                    if codes_json:
                        zipf.writestr(f"codes_{timestamp}.json", codes_json)

                    conn = get_db_connection()
                    cursor = conn.cursor(pymysql.cursors.DictCursor)
                    cursor.execute("SELECT * FROM schema_migrations ORDER BY applied_at")
                    migrations = cursor.fetchall()
                    cursor.close()
                    conn.close()
                    zipf.writestr(
                        f"migrations_{timestamp}.json",
                        json.dumps(migrations, default=str, indent=2, ensure_ascii=False)
                    )

                    zipf.writestr(
                        f"stats_{timestamp}.json",
                        json.dumps(stats, ensure_ascii=False, indent=2)
                    )

                    # Add Public Directory Recursively
                    public_dir = '/home/selfnit4/self/public'
                    if os.path.exists(public_dir):
                        logging.info("Adding public directory to backup...")
                        for foldername, subfolders, filenames in os.walk(public_dir):
                            for filename in filenames:
                                file_path = os.path.join(foldername, filename)
                                arcname = os.path.join('public', os.path.relpath(file_path, public_dir))
                                zipf.write(file_path, arcname)
                        logging.info("✅ Public directory appended natively to ZIP")
                    else:
                        logging.warning(f"⚠️ Public directory {public_dir} not found to Zip")

                    metadata = {
                        'created_at': now_tehran.isoformat(),
                        'version': '2.0',
                        'files_included': [
                            'database_dump.sql', 'codes_export.json',
                            'migrations.json', 'stats.json', 'public/'
                        ]
                    }
                    zipf.writestr('metadata.json', json.dumps(metadata, indent=2))

                file_size = os.path.getsize(zip_path)
                logging.info(f"✅ Backup ZIP created: {zip_filename} ({file_size} bytes)")
                return zip_path
            
            # Run ZIP creation in executor
            result = await loop.run_in_executor(None, create_zip_file)
            return result

        except Exception as e:
            logging.error(f"❌ Backup ZIP creation failed: {e}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None


async def send_daily_backup(context):
    from config.settings import SUDO_USER_ID
    from models.user import User
    from services.stats_service import StatsService
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton

    zip_path = await BackupService.create_daily_backup_zip()
    summary = StatsService.get_daily_summary()

    # ── Build the daily log ──────────────────────────────────────────
    lines = [
        f"📊 گزارش روزانه — {summary['date']} {summary['weekday']}",
        "━━━━━━━━━━━━━━━━━━",
    ]

    # Today's activity per product line
    has_today_activity = any(
        (r['submitted_today'] or 0) + (r['approved_today'] or 0) + (r['rejected_today'] or 0) > 0
        for r in summary['today_lines']
    )

    if has_today_activity:
        lines.append("\n📦 فعالیت امروز:")
        for r in summary['today_lines']:
            s = r['submitted_today'] or 0
            a = r['approved_today'] or 0
            rj = r['rejected_today'] or 0
            if s + a + rj == 0:
                continue
            parts = []
            if s: parts.append(f"ثبت {s}")
            if a: parts.append(f"تایید {a}")
            if rj: parts.append(f"رد {rj}")
            lines.append(f"  {r['icon']} {r['name_fa']}: {' | '.join(parts)}")
    else:
        lines.append("\n📦 فعالیت امروز: —")

    # Pending queue
    pending = summary['pending_codes']
    if pending:
        lines.append(f"\n⏳ در انتظار بررسی ({len(pending)}):")
        codes = [p['code'] for p in pending[:10]]
        lines.append(f"  {', '.join(codes)}")
        if len(pending) > 10:
            lines.append(f"  ... و {len(pending) - 10} مورد دیگر")
    else:
        lines.append("\n⏳ در انتظار بررسی: —")

    # Top performers today
    lines.append("\n👤 برترین‌ها امروز:")
    editor = summary['top_editor_today']
    reviewer = summary['top_reviewer_today']
    if editor:
        lines.append(f"  🎨 طراح: {editor['editor_name']} ({editor['count']} ثبت)")
    else:
        lines.append(f"  🎨 طراح: —")
    if reviewer:
        lines.append(f"  ✅ ناظر: {reviewer['reviewer_name']} ({reviewer['count']} بررسی)")
    else:
        lines.append(f"  ✅ ناظر: —")

    # Weekly trend
    w = summary['weekly']
    ws = w['submitted_week'] or 0
    wa = w['approved_week'] or 0
    wr = w['rejected_week'] or 0
    lines.append(f"\n📈 هفتگی: ثبت {ws} | تایید {wa} | رد {wr}")

    # System totals
    sys = summary['system']
    lines.append(f"🗄 کل: {sys['total'] or 0} طرح | {sys['pending'] or 0} در انتظار | {sys['approved'] or 0} تایید شده")

    log_text = '\n'.join(lines)

    reviewers = User.get_by_role('reviewer')
    reviewer_buttons = [
        InlineKeyboardButton(
            f"📤 ارسال به {r.first_name}",
            callback_data=f"sendlog_{r.user_id}"
        )
        for r in reviewers
    ]

    markup = InlineKeyboardMarkup([[btn] for btn in reviewer_buttons]) if reviewer_buttons else None

    try:
        await context.bot.send_message(
            chat_id=SUDO_USER_ID,
            text=log_text,
            reply_markup=markup
        )
    except Exception as e:
        logging.error(f"Failed to send daily log to sudo: {e}")

    if zip_path:
        try:
            file_size = os.path.getsize(zip_path) / 1024
            with open(zip_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=SUDO_USER_ID,
                    document=f,
                    filename=os.path.basename(zip_path),
                    caption=f"💾 بکاپ روزانه (شامل Public)\nحجم: {file_size:.1f} KB"
                )
        except Exception as e:
            logging.error(f"Failed to send backup ZIP to sudo: {e}")
        finally:
            shutil.rmtree(os.path.dirname(zip_path), ignore_errors=True)
    else:
        try:
            await context.bot.send_message(SUDO_USER_ID, "❌ بکاپ روزانه ناموفق بود.")
        except Exception as e:
            logging.error(f"Failed to send backup failure notice: {e}")