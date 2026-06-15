import logging
import json
from utils.enums import DesignStatus

class Migration004:
    """Migrate existing data from old tables to new unified structure"""
    
    name = "004_migrate_existing_data"
    
    @staticmethod
    def up(cursor):
        """Migrate data from legacy tables"""
        logging.info("Starting data migration from legacy tables...")
        
        migrated_count = {
            'mobile': 0,
            'sticker': 0,
            'locked_mobile': 0,
            'locked_sticker': 0
        }
        
        # FIX: Helper to check if table exists
        def table_exists(table_name):
            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND table_name = %s
            """, (table_name,))
            return cursor.fetchone()[0] > 0
        
        # Get product line IDs
        cursor.execute("SELECT id, code_prefix FROM product_lines")
        product_line_map = {row[1]: row[0] for row in cursor.fetchall()}
        
        # ========== MIGRATE MOBILE DESIGNS ==========
        
        # Pending mobile designs
        if table_exists('pending_designs'):
            try:
                cursor.execute("SELECT COUNT(*) FROM pending_designs")
                if cursor.fetchone()[0] > 0:
                    cursor.execute("""
                        INSERT INTO designs 
                        (code, product_line_id, status, editor_user_id, editor_name,
                         mockup_file_ids, print_file_ids, mockup_message_ids_reviewer, created_at)
                        SELECT 
                            pd.code,
                            %s,
                            'pending',
                            pd.designer_chat_id,
                            (SELECT first_name FROM users WHERE user_id = pd.designer_chat_id LIMIT 1),
                            pd.mockup_file_ids,
                            pd.print_file_ids,
                            pd.mockup_message_ids_nazi,
                            pd.created_at
                        FROM pending_designs pd
                        ON DUPLICATE KEY UPDATE code = pd.code
                    """, (product_line_map.get('TS', 1),))
                    migrated_count['mobile'] += cursor.rowcount
                    logging.info(f"Migrated {cursor.rowcount} pending mobile designs")
            except Exception as e:
                # FIX: More specific error logging
                logging.error(f"Failed to migrate pending_designs: {e}")
                raise  # Re-raise to fail the migration properly
        else:
            logging.info("Table 'pending_designs' does not exist, skipping")
        
        # Approved/Rejected mobile designs
        if table_exists('design_log'):
            try:
                cursor.execute("SELECT COUNT(*) FROM design_log")
                if cursor.fetchone()[0] > 0:
                    cursor.execute("""
                        INSERT INTO designs 
                        (code, product_line_id, status, editor_user_id, editor_name,
                         mockup_file_ids, print_file_ids, created_at, final_name)
                        SELECT 
                            dl.code,
                            %s,
                            dl.status,
                            dl.designer_chat_id,
                            (SELECT first_name FROM users WHERE user_id = dl.designer_chat_id LIMIT 1),
                            '[]',
                            '[]',
                            dl.created_at,
                            dl.final_name
                        FROM design_log dl
                        ON DUPLICATE KEY UPDATE status = dl.status
                    """, (product_line_map.get('TS', 1),))
                    migrated_count['mobile'] += cursor.rowcount
                    logging.info(f"Migrated {cursor.rowcount} mobile design logs")
            except Exception as e:
                logging.error(f"Failed to migrate design_log: {e}")
                raise
        else:
            logging.info("Table 'design_log' does not exist, skipping")
        
        # Locked mobile codes
        if table_exists('locked_codes'):
            try:
                cursor.execute("SELECT COUNT(*) FROM locked_codes")
                if cursor.fetchone()[0] > 0:
                    cursor.execute("""
                        INSERT INTO designs_locked_codes 
                        (code, product_line_id, locked_at, is_manual)
                        SELECT 
                            lc.code,
                            %s,
                            lc.locked_at,
                            FALSE
                        FROM locked_codes lc
                        ON DUPLICATE KEY UPDATE code = lc.code
                    """, (product_line_map.get('TS', 1),))
                    migrated_count['locked_mobile'] = cursor.rowcount
                    logging.info(f"Migrated {cursor.rowcount} locked mobile codes")
            except Exception as e:
                logging.error(f"Failed to migrate locked_codes: {e}")
                raise
        else:
            logging.info("Table 'locked_codes' does not exist, skipping")
        
        # ========== MIGRATE STICKERS ==========
        
        # Pending stickers
        if table_exists('pending_stickers'):
            try:
                cursor.execute("SELECT COUNT(*) FROM pending_stickers")
                if cursor.fetchone()[0] > 0:
                    cursor.execute("""
                        INSERT INTO designs 
                        (code, product_line_id, status, editor_user_id, editor_name,
                         mockup_file_ids, print_file_ids, mockup_message_ids_reviewer, created_at)
                        SELECT 
                            ps.code,
                            %s,
                            'pending',
                            ps.designer_chat_id,
                            (SELECT first_name FROM users WHERE user_id = ps.designer_chat_id LIMIT 1),
                            ps.mockup_file_ids,
                            ps.print_file_ids,
                            ps.mockup_message_ids_nazi,
                            ps.created_at
                        FROM pending_stickers ps
                        ON DUPLICATE KEY UPDATE code = ps.code
                    """, (product_line_map.get('STI', 2),))
                    migrated_count['sticker'] += cursor.rowcount
                    logging.info(f"Migrated {cursor.rowcount} pending stickers")
            except Exception as e:
                logging.error(f"Failed to migrate pending_stickers: {e}")
                raise
        else:
            logging.info("Table 'pending_stickers' does not exist, skipping")
        
        # Approved/Rejected stickers
        if table_exists('sticker_log'):
            try:
                cursor.execute("SELECT COUNT(*) FROM sticker_log")
                if cursor.fetchone()[0] > 0:
                    cursor.execute("""
                        INSERT INTO designs 
                        (code, product_line_id, status, editor_user_id, editor_name,
                         mockup_file_ids, print_file_ids, created_at, final_name)
                        SELECT 
                            sl.code,
                            %s,
                            sl.status,
                            sl.designer_chat_id,
                            (SELECT first_name FROM users WHERE user_id = sl.designer_chat_id LIMIT 1),
                            '[]',
                            '[]',
                            sl.created_at,
                            sl.final_name
                        FROM sticker_log sl
                        ON DUPLICATE KEY UPDATE status = sl.status
                    """, (product_line_map.get('STI', 2),))
                    migrated_count['sticker'] += cursor.rowcount
                    logging.info(f"Migrated {cursor.rowcount} sticker logs")
            except Exception as e:
                logging.error(f"Failed to migrate sticker_log: {e}")
                raise
        else:
            logging.info("Table 'sticker_log' does not exist, skipping")
        
        # Locked sticker codes
        if table_exists('locked_sticker_codes'):
            try:
                cursor.execute("SELECT COUNT(*) FROM locked_sticker_codes")
                if cursor.fetchone()[0] > 0:
                    cursor.execute("""
                        INSERT INTO designs_locked_codes 
                        (code, product_line_id, locked_at, is_manual)
                        SELECT 
                            lsc.code,
                            %s,
                            lsc.locked_at,
                            FALSE
                        FROM locked_sticker_codes lsc
                        ON DUPLICATE KEY UPDATE code = lsc.code
                    """, (product_line_map.get('STI', 2),))
                    migrated_count['locked_sticker'] = cursor.rowcount
                    logging.info(f"Migrated {cursor.rowcount} locked sticker codes")
            except Exception as e:
                logging.error(f"Failed to migrate locked_sticker_codes: {e}")
                raise
        else:
            logging.info("Table 'locked_sticker_codes' does not exist, skipping")
        
        logging.info(f"✅ Data migration completed:")
        logging.info(f"   - Mobile designs: {migrated_count['mobile']}")
        logging.info(f"   - Sticker designs: {migrated_count['sticker']}")
        logging.info(f"   - Locked mobile codes: {migrated_count['locked_mobile']}")
        logging.info(f"   - Locked sticker codes: {migrated_count['locked_sticker']}")
    
    @staticmethod
    def down(cursor):
        """Rollback: clear migrated data"""
        cursor.execute("DELETE FROM designs")
        cursor.execute("DELETE FROM designs_locked_codes")
        logging.info("Migrated data cleared")