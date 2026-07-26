import pymysql
import logging
from typing import Optional
from config.database import get_db_connection
from utils.helpers import get_tehran_time, to_utc_naive


class ProductLine:
    """Product Line model for managing product types"""

    def __init__(
        self,
        id: Optional[int],
        code_prefix: str,
        name_en: str,
        name_fa: str,
        icon: str = '📦',
        code_format: str = '{prefix}{counter:03d}',
        counter_start: int = 1,
        counter_end: int = 999,
        has_mockup: bool = True,
        has_print_file: bool = True,
        is_active: bool = True,
        display_order: int = 0,
        created_at=None,
        updated_at=None,
        metadata=None,
        group_products: Optional[int] = None,
        group_print: Optional[int] = None,
        stats_reset_at=None,
        **kwargs
    ):
        self.id = id
        self.code_prefix = code_prefix
        self.name_en = name_en
        self.name_fa = name_fa
        self.icon = icon
        self.code_format = code_format
        self.counter_start = counter_start
        self.counter_end = counter_end
        self.has_mockup = has_mockup
        self.has_print_file = has_print_file
        self.is_active = is_active
        self.display_order = display_order
        self.created_at = created_at
        self.updated_at = updated_at
        self.metadata = metadata
        self.group_products = group_products
        self.group_print = group_print
        self.stats_reset_at = stats_reset_at
        
        # This MUST be inside __init__, not at class level
        if kwargs:
            logging.warning(f"ProductLine.__init__ received unknown kwargs: {list(kwargs.keys())}")

    def is_fully_configured(self) -> bool:
        """Return True if both groups are set"""
        return self.group_products is not None and self.group_print is not None

    def missing_groups(self) -> list[str]:
        """Return list of missing group names"""
        missing = []
        if self.group_products is None:
            missing.append('group_products (گروه محصولات)')
        if self.group_print is None:
            missing.append('group_print (گروه چاپ)')
        return missing
        
        
    @staticmethod
    def get_all_active():
        """Get all active product lines"""
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("""
                SELECT * FROM product_lines
                WHERE is_active = TRUE
                ORDER BY display_order, name_fa
            """)
            return [ProductLine(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_all():
        """Get all product lines including inactive"""
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("""
                SELECT * FROM product_lines
                ORDER BY display_order, name_fa
            """)
            return [ProductLine(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_prefix(code_prefix, active_only=True):
        """
        Get product line by code prefix
        
        Args:
            code_prefix: The prefix code (e.g. 'TS', 'STI')
            active_only: If True, only return active lines (default: True)
        """
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            if active_only:
                cursor.execute("""
                    SELECT * FROM product_lines
                    WHERE code_prefix = %s AND is_active = TRUE
                """, (code_prefix,))
            else:
                cursor.execute("""
                    SELECT * FROM product_lines
                    WHERE code_prefix = %s
                """, (code_prefix,))
            row = cursor.fetchone()
            return ProductLine(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_id(product_line_id):
        """Get product line by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("""
                SELECT * FROM product_lines WHERE id = %s
            """, (product_line_id,))
            row = cursor.fetchone()
            return ProductLine(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def create(code_prefix, name_en, name_fa, icon='📦',
               code_format=None, counter_start=1, counter_end=999,
               display_order=999):
        """Create new product line"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if not code_format:
                code_format = f'{code_prefix}{{counter:03d}}'

            cursor.execute("""
                INSERT INTO product_lines
                (code_prefix, name_en, name_fa, icon, code_format,
                 counter_start, counter_end, display_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (code_prefix, name_en, name_fa, icon, code_format,
                  counter_start, counter_end, display_order))

            conn.commit()
            product_line_id = cursor.lastrowid
            logging.info(f"✅ Product line created: {code_prefix} - {name_fa}")
            return ProductLine.get_by_id(product_line_id)
        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to create product line: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def update(self, **kwargs):
        """Update product line fields"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            allowed_fields = ['name_fa', 'icon', 'code_format',
                              'counter_start', 'counter_end',
                              'is_active', 'display_order',
                              'group_products', 'group_print']

            updates = []
            values = []

            for field, value in kwargs.items():
                if field in allowed_fields:
                    updates.append(f"{field} = %s")
                    values.append(value)
                    setattr(self, field, value)

            if not updates:
                return

            values.append(self.id)
            cursor.execute(f"""
                UPDATE product_lines
                SET {', '.join(updates)}
                WHERE id = %s
            """, values)

            conn.commit()
            logging.info(f"✅ Product line {self.code_prefix} updated")
        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to update product line: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def set_group(self, group_type, chat_id):
        """
        Set group_products or group_print for this product line.
        group_type: 'products' or 'print'
        chat_id: integer chat ID (negative for groups)
        """
        if group_type not in ('products', 'print'):
            raise ValueError("group_type must be 'products' or 'print'")
        field = f'group_{group_type}'
        self.update(**{field: chat_id})
        logging.info(f"✅ {self.code_prefix} {field} set to {chat_id}")

    def deactivate(self):
        self.update(is_active=False)

    def activate(self):
        self.update(is_active=True)

    def has_approved_designs(self) -> bool:
        """Check if this product line has any approved designs"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT COUNT(*) FROM designs
                WHERE product_line_id = %s AND status = 'approved'
            """, (self.id,))
            count = cursor.fetchone()[0]
            return count > 0
        finally:
            cursor.close()
            conn.close()

    def has_any_designs(self) -> bool:
        """Check if this product line has any designs at all"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT COUNT(*) FROM designs
                WHERE product_line_id = %s
            """, (self.id,))
            count = cursor.fetchone()[0]
            return count > 0
        finally:
            cursor.close()
            conn.close()

    def delete(self):
        """
        Delete this product line permanently.
        Also cleans up locked codes and group message records.
        Does NOT delete designs — caller must ensure no designs exist.
        """
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM designs_locked_codes WHERE product_line_id = %s", (self.id,))
            cursor.execute("DELETE FROM design_group_messages WHERE design_id IN (SELECT id FROM designs WHERE product_line_id = %s)", (self.id,))
            cursor.execute("DELETE FROM product_lines WHERE id = %s", (self.id,))
            conn.commit()
            logging.info(f"🗑️ Product line {self.code_prefix} ({self.name_fa}) deleted")
        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to delete product line {self.code_prefix}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def get_stats(self, editor_user_id: int = None) -> dict:
        """Get statistics for this product line, optionally filtered by editor"""
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            if editor_user_id:
                cursor.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                        SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved,
                        SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
                    FROM designs
                    WHERE product_line_id = %s AND editor_user_id = %s AND status != 'deleted'
                """, (self.id, editor_user_id))
            else:
                cursor.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                        SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved,
                        SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
                    FROM designs
                    WHERE product_line_id = %s AND status != 'deleted'
                """, (self.id,))
            stats = cursor.fetchone()

            cursor.execute("""
                SELECT COUNT(*) as locked_count
                FROM designs_locked_codes
                WHERE product_line_id = %s AND is_manual = TRUE
            """, (self.id,))
            
            # FIX: Handle NULL values from SUM() when no rows exist
            stats['locked'] = cursor.fetchone()['locked_count']
            
            # Convert None to 0 for SUM results
            stats['pending'] = stats['pending'] or 0
            stats['approved'] = stats['approved'] or 0
            stats['rejected'] = stats['rejected'] or 0

            return stats
        finally:
            cursor.close()
            conn.close()