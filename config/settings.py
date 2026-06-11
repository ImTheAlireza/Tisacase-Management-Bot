import os
from dotenv import load_dotenv
import logging

load_dotenv()

# ==================== BOT CONFIGURATION ====================
BOT_TOKEN = os.getenv("MAIN_BOT_TOKEN")

# ==================== CHAT IDS ====================
SUDO_USER_ID = int(os.getenv("MAIN_ALIREZA_CHAT_ID"))
NAZI_CHAT_ID = int(os.getenv("MAIN_NAZI_CHAT_ID"))
LOG_GROUP_ID = int(os.getenv("MAIN_LOG_GROUP_ID"))

# Legacy variable names for compatibility
DESIGNER_CHAT_ID = SUDO_USER_ID

# NOTE: GROUP_PRODUCTS and GROUP_PRINT have been removed.
# Each product line now stores its own group IDs in the database.
# Configure them via the bot's group management menu (Sudo → تنظیم گروه‌ها).

# ==================== DATABASE CONFIGURATION ====================
DB_CONFIG = {
    'host': os.getenv('MAIN_DB_HOST', 'localhost'),
    'user': os.getenv('MAIN_DB_USER'),
    'password': os.getenv('MAIN_DB_PASSWORD'),
    'database': os.getenv('MAIN_DB_NAME'),
    'charset': 'utf8mb4',
}

# ==================== TIMEZONE ====================
TIMEZONE = 'Asia/Tehran'

# ==================== PRODUCT LINES CONFIGURATION ====================
DEFAULT_PRODUCT_LINES = [
    {
        'code_prefix': 'TS',
        'name_en': 'case',
        'name_fa': 'قاب موبایل',
        'icon': '📱',
        'code_format': 'TS{counter:03d}',
        'counter_start': 1,
        'counter_end': 999,
        'display_order': 1
    },
    {
        'code_prefix': 'STI',
        'name_en': 'sticker',
        'name_fa': 'استیکر',
        'icon': '🎨',
        'code_format': 'STI{counter:03d}',
        'counter_start': 1,
        'counter_end': 999,
        'display_order': 2
    },
    {
        'code_prefix': 'TB',
        'name_en': 'frame',
        'name_fa': 'قاب تابلو',
        'icon': '🖼️',
        'code_format': 'TB{counter:03d}',
        'counter_start': 1,
        'counter_end': 999,
        'display_order': 3
    },
    {
        'code_prefix': 'TT',
        'name_en': 'tshirt',
        'name_fa': 'تیشرت',
        'icon': '👕',
        'code_format': 'TT{counter:03d}',
        'counter_start': 1,
        'counter_end': 999,
        'display_order': 4
    }
]

# ==================== VALIDATION ====================
required_env_vars = [
    'MAIN_BOT_TOKEN',
    'MAIN_ALIREZA_CHAT_ID',
    'MAIN_NAZI_CHAT_ID',
    'MAIN_LOG_GROUP_ID',
    'MAIN_DB_HOST',
    'MAIN_DB_USER',
    'MAIN_DB_PASSWORD',
    'MAIN_DB_NAME'
]

missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    logging.error(f"❌ Missing environment variables: {missing_vars}")
    exit(1)

# ==================== ROLES ====================
ROLES = {
    'sudo': {
        'permissions': ['*'],
        'can_switch_to': ['editor', 'reviewer'],
        'display_name': '👑 Sudo'
    },
    'editor': {
        'permissions': ['create_design', 'upload_files', 'view_own_designs'],
        'display_name': '🎨 Editor'
    },
    'reviewer': {
        'permissions': ['approve_design', 'reject_design', 'view_pending'],
        'display_name': '✅ Reviewer'
    }
}

# ==================== BACKUP SETTINGS ====================
BACKUP_TIME_HOUR = 23
BACKUP_TIME_MINUTE = 59

# ==================== FILE SETTINGS ====================
MAX_FILE_SIZE_MB = 20
ALLOWED_FILE_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'application/pdf',
                      'application/x-photoshop', 'application/postscript']

# ==================== LOGGING ====================
LOG_LEVEL = logging.INFO
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
