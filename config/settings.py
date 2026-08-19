import os
from dotenv import load_dotenv
import logging
from utils.enums import DesignStatus

load_dotenv()

# ==================== REQUIRED ENVIRONMENT VARIABLES ====================
# Validate presence BEFORE any parsing so a missing variable produces a clear
# message instead of e.g. `int(None)` raising an obscure TypeError.
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


def _require_int_env(name: str) -> int:
    """Read an integer env var; exit with a clear message on invalid input."""
    raw = os.getenv(name)
    try:
        return int(raw)
    except (TypeError, ValueError):
        logging.error(
            f"❌ Environment variable {name} must be an integer, got: {raw!r}"
        )
        exit(1)


# ==================== BOT CONFIGURATION ====================
BOT_TOKEN = os.getenv("MAIN_BOT_TOKEN")

# ==================== CHAT IDS ====================
SUDO_USER_ID = _require_int_env("MAIN_ALIREZA_CHAT_ID")
NAZI_CHAT_ID = _require_int_env("MAIN_NAZI_CHAT_ID")
LOG_GROUP_ID = _require_int_env("MAIN_LOG_GROUP_ID")

# ==================== DATABASE CONFIGURATION ====================
DB_CONFIG = {
    'host': os.getenv('MAIN_DB_HOST', 'localhost'),
    'user': os.getenv('MAIN_DB_USER'),
    'password': os.getenv('MAIN_DB_PASSWORD'),
    'database': os.getenv('MAIN_DB_NAME'),
    'charset': 'utf8mb4',
    'connect_timeout': 20,
    'read_timeout': 30,
    'write_timeout': 30,
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
    }
]

# ==================== TELEGRAM UPLOAD / LOG FORWARDING ====================
# Bot API document upload limit is 50 MB for the standard bot API.
TELEGRAM_UPLOAD_LIMIT_BYTES = int(os.getenv(
    'TELEGRAM_UPLOAD_LIMIT_BYTES', str(50 * 1024 * 1024)
))
# Minimum level forwarded to the Telegram log group. Default INFO preserves the
# current behaviour; set to e.g. WARNING to reduce log-group noise.
TELEGRAM_LOG_LEVEL = os.getenv('TELEGRAM_LOG_LEVEL', 'INFO').upper()

# ==================== ROLES ====================
ROLES = {
    'sudo': {
        'permissions': ['*'],
        'can_switch_to': ['editor', 'reviewer'],
        'display_name': '👑 Sudo'
    },
    'editor': {
        'permissions': ['create_design', 'upload_files', 'view_own_designs'],
        'display_name': '✏️ Editor'
    },
    'reviewer': {
        'permissions': ['approve_design', 'reject_design', 'view_pending'],
        'display_name': '👁 Reviewer'
    }
}

# ==================== BACKUP SETTINGS ====================
BACKUP_TIME_HOUR = 23
BACKUP_TIME_MINUTE = 59

# ==================== SERVER BILL REMINDER SETTINGS ====================
SERVER_BILL_REMINDER_HOUR = int(os.getenv('SERVER_BILL_REMINDER_HOUR', '9'))
SERVER_BILL_REMINDER_MINUTE = int(os.getenv('SERVER_BILL_REMINDER_MINUTE', '0'))

# ==================== FILE SETTINGS ====================
MAX_FILE_SIZE_MB = 20
ALLOWED_FILE_TYPES = [
    'image/jpeg', 'image/png', 'image/webp',
    'application/pdf', 'application/x-photoshop', 'application/postscript'
]

# ==================== LOGGING ====================
LOG_LEVEL = logging.INFO
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# ==================== RATE LIMITING ====================
RATE_LIMIT_FILE_UPLOAD = float(os.getenv('RATE_LIMIT_FILE_UPLOAD', '1.0'))
RATE_LIMIT_CODE_GEN = float(os.getenv('RATE_LIMIT_CODE_GEN', '3.0'))
RATE_LIMIT_COMMAND = float(os.getenv('RATE_LIMIT_COMMAND', '0.5'))
RATE_LIMIT_SUBMIT = float(os.getenv('RATE_LIMIT_SUBMIT', '5.0'))
RATE_LIMIT_REVIEW = float(os.getenv('RATE_LIMIT_REVIEW', '2.0'))

# ==================== PERFORMANCE ====================
TELEGRAM_SEND_DELAY = float(os.getenv('TELEGRAM_SEND_DELAY', '0.3'))
MAX_FILE_SIZE_DOWNLOAD_MB = int(os.getenv('MAX_FILE_SIZE_DOWNLOAD_MB', '20'))
DB_POOL_MIN_CACHED = int(os.getenv('DB_POOL_MIN_CACHED', '2'))
DB_POOL_MAX_CACHED = int(os.getenv('DB_POOL_MAX_CACHED', '5'))
DB_POOL_MAX_CONNECTIONS = int(os.getenv('DB_POOL_MAX_CONNECTIONS', '10'))

# ==================== SUPERVISOR SETTINGS ====================
SUPERVISORD_CONF = os.getenv('SUPERVISORD_CONF', '/etc/supervisor/supervisord.conf')
SUPERVISOR_PROCESS = os.getenv('SUPERVISOR_PROCESS', 'tisa_bot')