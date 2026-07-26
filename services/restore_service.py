import os
import json
import shutil
import zipfile
import tempfile
import logging
import subprocess
import asyncio
from config.database import get_db_connection
from config.settings import DB_CONFIG, SUPERVISORD_CONF, SUPERVISOR_PROCESS


PUBLIC_DIR = '/home/selfnit4/self/public'


class RestoreService:

    @staticmethod
    def find_sql_in_zip(zip_path: str) -> str | None:
        """Find the .sql file inside the backup ZIP"""
        with zipfile.ZipFile(zip_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.sql') and not name.startswith('__'):
                    return name
        return None

    @staticmethod
    def restore_database(sql_path: str) -> dict:
        """
        Restore database from a .sql file.
        Drops all existing tables, then runs the SQL dump.
        """
        temp_dir = None
        config_file = None
        try:
            # Create temporary MySQL config file
            temp_dir = tempfile.mkdtemp()
            config_file = os.path.join(temp_dir, 'my.cnf')

            with open(config_file, 'w') as f:
                f.write('[client]\n')
                host = DB_CONFIG['host']
                user = DB_CONFIG['user']
                password = DB_CONFIG['password']
                f.write(f"host={host}\n")
                f.write(f"user={user}\n")
                f.write(f"password={password}\n")

            os.chmod(config_file, 0o600)

            # Step 1: Drop all tables
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
                cursor.execute("SHOW TABLES")
                tables = [row[0] for row in cursor.fetchall()]
                for table in tables:
                    cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
                cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
                conn.commit()
                logging.info(f"✅ Dropped {len(tables)} tables")
            finally:
                cursor.close()
                conn.close()

            # Step 2: Import SQL dump
            cmd = [
                'mysql',
                f"--defaults-file={config_file}",
                DB_CONFIG['database']
            ]

            with open(sql_path, 'r', encoding='utf-8') as f:
                result = subprocess.run(
                    cmd, stdin=f, capture_output=True, text=True,
                    timeout=300
                )

            if result.returncode != 0:
                return {'success': False, 'error': result.stderr[:500]}

            return {'success': True, 'tables_restored': len(tables)}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            try:
                os.remove(config_file)
                os.rmdir(temp_dir)
            except Exception:
                pass

    @staticmethod
    def restore_public_files(zip_path: str) -> dict:
        """
        Extract the public/ directory from the backup ZIP
        and replace the current public directory contents.
        """
        try:
            with zipfile.ZipFile(zip_path, 'r') as z:
                public_entries = [
                    name for name in z.namelist()
                    if name.startswith('public/') and not name.endswith('__')
                ]

                if not public_entries:
                    return {'success': True, 'files_restored': 0, 'note': 'No public/ files in backup'}

                # Clear existing public directory (except hidden files and .mimocode)
                cleared = 0
                if os.path.exists(PUBLIC_DIR):
                    for item in os.listdir(PUBLIC_DIR):
                        if item.startswith('.'):
                            continue
                        item_path = os.path.join(PUBLIC_DIR, item)
                        if os.path.isdir(item_path):
                            shutil.rmtree(item_path)
                        else:
                            os.remove(item_path)
                        cleared += 1

                # Extract public/ files
                restored = 0
                for entry in public_entries:
                    if entry == 'public/':
                        continue
                    # Strip 'public/' prefix
                    relative = entry[len('public/'):]
                    if not relative:
                        continue
                    target = os.path.join(PUBLIC_DIR, relative)
                    os.makedirs(os.path.dirname(target), exist_ok=True)

                    if not entry.endswith('/'):
                        with z.open(entry) as src, open(target, 'wb') as dst:
                            dst.write(src.read())
                        restored += 1

                return {
                    'success': True,
                    'files_cleared': cleared,
                    'files_restored': restored
                }

        except Exception as e:
            return {'success': False, 'error': str(e)}

    @staticmethod
    def restart_bot():
        """Restart the bot via supervisorctl"""
        try:
            subprocess.run(
                ['supervisorctl', '-c', SUPERVISORD_CONF, 'restart', SUPERVISOR_PROCESS],
                capture_output=True, timeout=10
            )
            return True
        except Exception as e:
            logging.error(f"Failed to restart bot: {e}")
            return False
