import pymysql
import logging
from config.database import get_db_connection
from models.product_line import ProductLine
from models.design import Design
from utils.helpers import get_tehran_time, to_utc_naive
from utils.enums import DesignStatus

class CodeService:
    """Service for generating and managing design codes"""
    
    @staticmethod
    def generate_code(product_line_prefix, editor_user_id, editor_name):
        """
        Generate next available code for a product line with proper locking
        
        Args:
            product_line_prefix: Code prefix (TS, STI, TB, TT)
            editor_user_id: ID of the editor creating the design
            editor_name: Name of the editor
            
        Returns:
            tuple: (code, design_object)
        """
        # Get product line
        product_line = ProductLine.get_by_prefix(product_line_prefix)
        if not product_line:
            raise ValueError(f"Invalid product line: {product_line_prefix}")
        
        if not product_line.is_active:
            raise ValueError(f"Product line {product_line_prefix} is not active")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            # FIX: Start transaction with proper isolation
            conn.begin()
            
            # FIX: Lock the relevant rows to prevent concurrent code generation
            # Get all used codes for this product line with FOR UPDATE lock
            cursor.execute("""
                SELECT code FROM designs 
                WHERE product_line_id = %s
                FOR UPDATE
            """, (product_line.id,))
            
            used_codes_from_designs = {row[0] for row in cursor.fetchall()}
            
            cursor.execute("""
                SELECT code FROM designs_locked_codes 
                WHERE product_line_id = %s
                FOR UPDATE
            """, (product_line.id,))
            
            used_codes_from_locked = {row[0] for row in cursor.fetchall()}
            
            used_codes = used_codes_from_designs | used_codes_from_locked
            
            # Find next available code
            prefix = product_line.code_prefix
            start = product_line.counter_start
            end = product_line.counter_end
            
            for counter in range(start, end + 1):
                code = f"{prefix}{counter:03d}"
                
                if code not in used_codes:
                    # Create design with this code
                    now_utc = to_utc_naive(get_tehran_time())
                    
                    # FIX: Insert directly in the same transaction
                    cursor.execute("""
                        INSERT INTO designs
                        (code, product_line_id, status, editor_user_id, editor_name,
                         mockup_file_ids, print_file_ids, created_at)
                        VALUES (%s, %s, 'pending', %s, %s, '[]', '[]', %s)
                    """, (code, product_line.id, editor_user_id, editor_name, now_utc))
                    
                    design_id = cursor.lastrowid
                    
                    # Commit the transaction
                    conn.commit()
                    
                    logging.info(f"✅ Generated code {code} for {editor_name}")
                    
                    # Create design object to return
                    design = Design(
                        id=design_id,
                        code=code,
                        product_line_id=product_line.id,
                        status=DesignStatus.PENDING,
                        editor_user_id=editor_user_id,
                        editor_name=editor_name,
                        mockup_file_ids=[],
                        print_file_ids=[],
                        created_at=get_tehran_time()
                    )
                    
                    return code, design
            
            # If we get here, no codes available
            conn.rollback()
            raise Exception(f"No available codes for {product_line_prefix} ({start}-{end})")
            
        except Exception as e:
            conn.rollback()
            logging.error(f"Code generation failed for {product_line_prefix}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()
    
    @staticmethod
    def is_code_available(code):
        """Check if a code is available"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT COUNT(*) FROM designs WHERE code = %s
                UNION ALL
                SELECT COUNT(*) FROM designs_locked_codes WHERE code = %s
            """, (code, code))
            
            total = sum(row[0] for row in cursor.fetchall())
            return total == 0
        finally:
            cursor.close()
            conn.close()
    
    @staticmethod
    def lock_code_manual(code, product_line_prefix, locked_by, notes=None):
        """
        Manually lock/reserve a code (sudo only)
        
        Args:
            code: The code to lock
            product_line_prefix: Product line prefix
            locked_by: User ID who is locking
            notes: Optional notes
        """
        # FIX: Allow locking codes even for inactive product lines
        product_line = ProductLine.get_by_prefix(product_line_prefix, active_only=False)
        if not product_line:
            raise ValueError(f"Invalid product line: {product_line_prefix}")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            # FIX: Use transaction for atomicity
            conn.begin()
            
            # Check if code is already used
            cursor.execute("""
                SELECT COUNT(*) FROM designs WHERE code = %s
            """, (code,))
            designs_count = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT COUNT(*) FROM designs_locked_codes WHERE code = %s
            """, (code,))
            locked_count = cursor.fetchone()[0]
            
            if designs_count > 0 or locked_count > 0:
                conn.rollback()
                raise ValueError(f"Code {code} is already in use")
            
            now_utc = to_utc_naive(get_tehran_time())
            
            cursor.execute("""
                INSERT INTO designs_locked_codes 
                (code, product_line_id, locked_by, locked_at, is_manual, notes)
                VALUES (%s, %s, %s, %s, TRUE, %s)
            """, (code, product_line.id, locked_by, now_utc, notes))
            
            conn.commit()
            logging.info(f"🔒 Code {code} manually locked by user {locked_by}")

        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to lock code {code}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()
    
    @staticmethod
    def unlock_code(code):
        """
        Unlock/release a code (sudo only)
        
        Args:
            code: The code to unlock
        """
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Check if code exists in locked_codes
            cursor.execute("""
                SELECT is_manual FROM designs_locked_codes WHERE code = %s
            """, (code,))
            
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Code {code} is not locked")
            
            is_manual = row[0]
            
            # Delete from locked_codes
            cursor.execute("""
                DELETE FROM designs_locked_codes WHERE code = %s
            """, (code,))
            
            conn.commit()
            
            lock_type = "manual" if is_manual else "auto"
            logging.info(f"🔓 Code {code} unlocked ({lock_type} lock)")
            
        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to unlock code {code}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()
    
    @staticmethod
    def get_locked_codes(product_line_prefix=None):
        """
        Get all locked codes, optionally filtered by product line
        
        Returns:
            list of dicts with code info
        """
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        
        try:
            if product_line_prefix:
                product_line = ProductLine.get_by_prefix(product_line_prefix)
                if not product_line:
                    return []
                
                cursor.execute("""
                    SELECT lc.*, pl.name_fa as product_name, pl.icon as product_icon,
                           u.first_name as locked_by_name
                    FROM designs_locked_codes lc
                    JOIN product_lines pl ON lc.product_line_id = pl.id
                    LEFT JOIN users u ON lc.locked_by = u.user_id
                    WHERE lc.product_line_id = %s
                    ORDER BY lc.is_manual DESC, lc.locked_at DESC
                """, (product_line.id,))
            else:
                cursor.execute("""
                    SELECT lc.*, pl.name_fa as product_name, pl.icon as product_icon,
                           u.first_name as locked_by_name
                    FROM designs_locked_codes lc
                    JOIN product_lines pl ON lc.product_line_id = pl.id
                    LEFT JOIN users u ON lc.locked_by = u.user_id
                    ORDER BY pl.display_order, lc.is_manual DESC, lc.locked_at DESC
                """)
            
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()
            
            
    @staticmethod
    def cleanup_orphaned_designs():
        """Remove pending designs with no files (created but never submitted)"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # FIX: Use JSON functions for proper comparison
            cursor.execute("""
                DELETE FROM designs
                WHERE status = 'pending'
                  AND JSON_LENGTH(mockup_file_ids) = 0
                  AND JSON_LENGTH(print_file_ids) = 0
                  AND created_at < NOW() - INTERVAL 24 HOUR
            """)
            deleted = cursor.rowcount
            conn.commit()
            if deleted:
                logging.info(f"🧹 Cleaned up {deleted} orphaned pending designs")
        except Exception as e:
            conn.rollback()
            logging.error(f"Cleanup orphaned designs failed: {e}")
        finally:
            cursor.close()
            conn.close()