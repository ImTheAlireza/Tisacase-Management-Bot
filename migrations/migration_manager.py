import pymysql
import logging
from datetime import datetime
from config.database import get_db_connection
from utils.enums import DesignStatus

class MigrationManager:
    """Manages database schema migrations"""
    
    def __init__(self):
        self.conn = None
        
    def connect(self):
        """Establish database connection"""
        self.conn = get_db_connection()
        
    def create_migrations_table(self):
        """Create table to track applied migrations"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    migration_name VARCHAR(255) UNIQUE NOT NULL,
                    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    success BOOLEAN DEFAULT TRUE,
                    error_message TEXT,
                    execution_time_ms INT,
                    INDEX idx_name (migration_name),
                    INDEX idx_success (success)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            self.conn.commit()
            logging.info("✅ Migration tracking table ready")
        except Exception as e:
            logging.error(f"❌ Failed to create migrations table: {e}")
            raise
        finally:
            cursor.close()
            
    def get_applied_migrations(self):
        """Get list of successfully applied migrations"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                SELECT migration_name 
                FROM schema_migrations 
                WHERE success = TRUE
            """)
            applied = {row[0] for row in cursor.fetchall()}
            return applied
        finally:
            cursor.close()
            
    def apply_migration(self, migration):
        """Apply a single migration with error handling"""
        cursor = self.conn.cursor()
        start_time = datetime.now()
        
        try:
            logging.info(f"🔄 Applying migration: {migration.name}")
            
            # Execute the migration
            migration.up(cursor)
            
            # Calculate execution time
            execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
            
            # Record success
            cursor.execute("""
                INSERT INTO schema_migrations 
                (migration_name, success, execution_time_ms)
                VALUES (%s, TRUE, %s)
                ON DUPLICATE KEY UPDATE 
                success = TRUE, 
                applied_at = CURRENT_TIMESTAMP,
                execution_time_ms = %s
            """, (migration.name, execution_time, execution_time))
            
            self.conn.commit()
            logging.info(f"✅ Migration {migration.name} completed ({execution_time}ms)")
            
        except Exception as e:
            self.conn.rollback()
            logging.error(f"❌ Migration {migration.name} failed: {e}")
            
            # Record failure
            cursor.execute("""
                INSERT INTO schema_migrations 
                (migration_name, success, error_message)
                VALUES (%s, FALSE, %s)
                ON DUPLICATE KEY UPDATE 
                success = FALSE,
                error_message = %s,
                applied_at = CURRENT_TIMESTAMP
            """, (migration.name, str(e)[:1000], str(e)[:1000]))
            
            self.conn.commit()
            raise
        finally:
            cursor.close()
            
    def run_migrations(self, migrations):
        """Run all pending migrations in order"""
        try:
            self.connect()
            self.create_migrations_table()
            
            applied = self.get_applied_migrations()
            pending = [m for m in migrations if m.name not in applied]
            
            if not pending:
                logging.info("✅ No pending migrations - database is up to date")
                return True
            
            logging.info(f"📊 Found {len(pending)} pending migration(s)")
            
            for migration in pending:
                self.apply_migration(migration)
                
            logging.info(f"✅ All {len(pending)} migration(s) completed successfully")
            return True
            
        except Exception as e:
            logging.error(f"❌ Migration process failed: {e}")
            return False
        finally:
            if self.conn:
                self.conn.close()
                
    def rollback_migration(self, migration):
        """Rollback a specific migration (if down() method exists)"""
        cursor = self.conn.cursor()
        
        try:
            if not hasattr(migration, 'down'):
                raise Exception(f"Migration {migration.name} does not support rollback")
            
            logging.info(f"🔄 Rolling back migration: {migration.name}")
            
            migration.down(cursor)
            
            cursor.execute("""
                DELETE FROM schema_migrations 
                WHERE migration_name = %s
            """, (migration.name,))
            
            self.conn.commit()
            logging.info(f"✅ Migration {migration.name} rolled back")
            
        except Exception as e:
            self.conn.rollback()
            logging.error(f"❌ Rollback failed for {migration.name}: {e}")
            raise
        finally:
            cursor.close()