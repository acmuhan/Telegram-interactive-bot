import logging
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("interactive_bot")


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} 未填写")
    return value


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().upper() in {"1", "TRUE", "YES", "ON"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} 应该是数字") from exc


def _int_list_env(name: str) -> list[int]:
    raw = _required(name)
    try:
        return [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError as exc:
        raise RuntimeError(f"{name} 应该是以英文逗号分隔的数字") from exc


bot_token = _required("BOT_TOKEN")
app_name = os.getenv("APP_NAME", "interactive-bot")
welcome_message = os.getenv("WELCOME_MESSAGE", "欢迎使用本机器人")
admin_group_id = _int_env("ADMIN_GROUP_ID", 0)
if not admin_group_id:
    raise RuntimeError("ADMIN_GROUP_ID 未填写")
admin_user_ids = _int_list_env("ADMIN_USER_IDS")

# Safer defaults for destructive operations.
is_delete_topic_as_ban_forever = _bool_env("DELETE_TOPIC_AS_FOREVER_BAN", False)
is_delete_user_messages = _bool_env("DELETE_USER_MESSAGE_ON_CLEAR_CMD", False)
disable_captcha = _bool_env("DISABLE_CAPTCHA", False)
message_interval = _int_env("MESSAGE_INTERVAL", 5)
media_group_delay = _int_env("MEDIA_GROUP_DELAY", 3)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'db.sqlite3'}")
PERSISTENCE_PATH = os.getenv("PERSISTENCE_PATH", str(BASE_DIR / "data" / f"{app_name}.pickle"))
