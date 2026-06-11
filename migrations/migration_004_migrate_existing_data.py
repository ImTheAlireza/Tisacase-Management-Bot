import logging
import json

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
        
        # Get product line IDs
        cursor.execute("SELECT id, code_prefix FROM product_lines")
        product_line_map = {row[1]: row[0] for row in cursor.fetchall()}
        
        # ========== MIGRATE MOBILE DESIGNS ==========
        
        # Pending mobile designs
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
        except Exception as e:
            logging.warning(f"No pending_designs table or migration failed: {e}")
        
        # Approved/Rejected mobile designs
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
        except Exception as e:
            logging.warning(f"No design_log table or migration failed: {e}")
        
        # Locked mobile codes
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
        except Exception as e:
            logging.warning(f"No locked_codes table or migration failed: {e}")
        
        # ========== MIGRATE STICKERS ==========
        
        # Pending stickers
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
        except Exception as e:
            logging.warning(f"No pending_stickers table or migration failed: {e}")
        
        # Approved/Rejected stickers
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
        except Exception as e:
            logging.warning(f"No sticker_log table or migration failed: {e}")
        
        # Locked sticker codes
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
        except Exception as e:
            logging.warning(f"No locked_sticker_codes table or migration failed: {e}")
        
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