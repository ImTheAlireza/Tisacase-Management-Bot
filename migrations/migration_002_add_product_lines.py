from config.settings import DEFAULT_PRODUCT_LINES
import logging

class Migration002:
    """Create product_lines table and seed default products"""
    
    name = "002_add_product_lines"
    
    @staticmethod
    def up(cursor):
        """Create product_lines table"""
        logging.info("Creating product_lines table...")
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_lines (
                id INT AUTO_INCREMENT PRIMARY KEY,
                code_prefix VARCHAR(10) UNIQUE NOT NULL,
                name_en VARCHAR(50) NOT NULL,
                name_fa VARCHAR(50) NOT NULL,
                icon VARCHAR(10) DEFAULT '📦',
                code_format VARCHAR(50) DEFAULT '{prefix}{counter:03d}',
                counter_start INT DEFAULT 1,
                counter_end INT DEFAULT 999,
                has_mockup BOOLEAN DEFAULT TRUE,
                has_print_file BOOLEAN DEFAULT TRUE,
                is_active BOOLEAN DEFAULT TRUE,
                display_order INT DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                metadata JSON,
                INDEX idx_prefix (code_prefix),
                INDEX idx_active (is_active),
                INDEX idx_order (display_order)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        
        # Insert default product lines
        for pl in DEFAULT_PRODUCT_LINES:
            cursor.execute("""
                INSERT INTO product_lines 
                (code_prefix, name_en, name_fa, icon, code_format, 
                 counter_start, counter_end, display_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                name_fa = VALUES(name_fa),
                icon = VALUES(icon),
                display_order = VALUES(display_order)
            """, (
                pl['code_prefix'],
                pl['name_en'],
                pl['name_fa'],
                pl['icon'],
                pl['code_format'],
                pl['counter_start'],
                pl['counter_end'],
                pl['display_order']
            ))
        
        logging.info(f"✅ Product lines table created with {len(DEFAULT_PRODUCT_LINES)} products")
    
    @staticmethod
    def down(cursor):
        """Rollback: drop product_lines table"""
        cursor.execute("DROP TABLE IF EXISTS product_lines")
        logging.info("Product lines table dropped")