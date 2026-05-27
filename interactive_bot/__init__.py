import logging
import os
import re
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


def _token_prefix(token: str) -> str:
    return token.split(":", 1)[0] if ":" in token else "unknown"


def _validate_bot_token(token: str) -> str:
    token = token.strip()
    # BotFather tokens are exactly '<numeric bot id>:<secret>'. A duplicated prefix like
    # '123:123:ABC' is a common copy/paste mistake and Telegram will echo the invalid
    # token in PTB's InvalidToken exception, so fail locally before network bootstrap.
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token):
        raise RuntimeError(
            "BOT_TOKEN 格式不正确。请从 BotFather 复制完整 token，格式应为 "
            "'<数字ID>:<密钥>'，中间只能有一个冒号。当前 token 前缀: "
            f"{_token_prefix(token)}"
        )
    return token


bot_token = _validate_bot_token(_required("BOT_TOKEN"))
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
