import subprocess
import zipfile
import tempfile
import os
import json
import shutil
import pymysql
import logging
from datetime import datetime
from config.database import get_db_connection, DB_CONFIG
from utils.helpers import get_tehran_time


class BackupService:

    @staticmethod
    def create_database_backup():
        try:
            dump_cmd = [
                'mysqldump',
                '-h', DB_CONFIG['host'],
                '-u', DB_CONFIG['user'],
                f"-p{DB_CONFIG['password']}",
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

            return json.dumps(codes_data, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logging.error(f"Codes export failed: {e}")
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
                    'pending': row['pending'] or 0,
                    'approved': row['approved'] or 0,
                    'rejected': row['rejected'] or 0
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
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                logging.info("Creating database dump...")
                sql_dump = BackupService.create_database_backup()
                if sql_dump:
                    zipf.writestr(f"database_{timestamp}.sql", sql_dump)
                    logging.info(f"✅ Database dump added ({len(sql_dump)} bytes)")
                else:
                    logging.warning("⚠️ Database dump failed, skipping")

                codes_json = BackupService.create_codes_export()
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

                stats = BackupService.get_stats_summary()
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

        except Exception as e:
            logging.error(f"❌ Backup ZIP creation failed: {e}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None


async def send_daily_backup(context):
    from config.settings import SUDO_USER_ID
    from models.user import User
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton

    zip_path = await BackupService.create_daily_backup_zip()
    stats = BackupService.get_stats_summary()

    lines = ["📋 لاگ روزانه\n━━━━━━━━━━━━━━━━━━"]
    for prefix, data in stats.get('product_lines', {}).items():
        lines.append(
            f"\n{data['name']} ({prefix})\n"
            f"  ⏳ در انتظار: {data['pending']}\n"
            f"  ✅ تایید: {data['approved']}\n"
            f"  ❌ رد: {data['rejected']}"
        )
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