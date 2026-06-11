import logging

class Migration003:
    """Create unified designs and locked_codes tables"""
    
    name = "003_create_unified_tables"
    
    @staticmethod
    def up(cursor):
        """Create new unified tables"""
        logging.info("Creating unified designs table...")
        
        # Unified designs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS designs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(20) UNIQUE NOT NULL,
                product_line_id INT NOT NULL,
                status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending',
                
                editor_user_id BIGINT NOT NULL,
                editor_name VARCHAR(255),
                
                reviewer_user_id BIGINT,
                reviewer_name VARCHAR(255),
                
                mockup_file_ids JSON NOT NULL,
                print_file_ids JSON NOT NULL,
                mockup_message_ids_reviewer JSON,
                
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                reviewed_at DATETIME,
                
                final_name TEXT,
                metadata JSON,
                
                INDEX idx_code (code),
                INDEX idx_product_line (product_line_id),
                INDEX idx_status (status),
                INDEX idx_editor (editor_user_id),
                INDEX idx_reviewer (reviewer_user_id),
                INDEX idx_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        
        # Unified locked_codes table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS designs_locked_codes (
                code VARCHAR(20) PRIMARY KEY,
                product_line_id INT NOT NULL,
                locked_by BIGINT,
                locked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_manual BOOLEAN DEFAULT FALSE,
                notes TEXT,
                INDEX idx_product_line (product_line_id),
                INDEX idx_locked_by (locked_by),
                INDEX idx_manual (is_manual)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        
        logging.info("✅ Unified tables created")
    
    @staticmethod
    def down(cursor):
        """Rollback: drop unified tables"""
        cursor.execute("DROP TABLE IF EXISTS designs")
        cursor.execute("DROP TABLE IF EXISTS designs_locked_codes")
        logging.info("Unified tables dropped")