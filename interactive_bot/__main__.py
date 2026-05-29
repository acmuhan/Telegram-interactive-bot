import asyncio
import os
import random
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from html import escape
from typing import ParamSpec, TypeVar

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PicklePersistence,
    filters,
)
from telegram.helpers import mention_html

from db.database import Base, engine, session_scope
from db.model import ForumStatus, MediaGroupMessage, MessageMap, User, run_schema_migrations

from . import (
    PERSISTENCE_PATH,
    admin_group_id,
    admin_user_ids,
    app_name,
    bot_token,
    captcha_cooldown_seconds,
    disable_captcha,
    enable_pickle_persistence,
    is_delete_topic_as_ban_forever,
    is_delete_user_messages,
    logger,
    media_group_delay,
    message_interval,
    welcome_message,
)

Base.metadata.create_all(bind=engine)
run_schema_migrations(engine)

P = ParamSpec("P")
T = TypeVar("T")
CAPTCHA_COOLDOWN_SECONDS = captcha_cooldown_seconds
_topic_locks: dict[int, asyncio.Lock] = {}
_topic_locks_guard = asyncio.Lock()


def _chunked(values: list[int], size: int = 100) -> Iterable[list[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


async def db_call(func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Run synchronous SQLAlchemy helpers outside the asyncio event loop."""
    return await asyncio.to_thread(func, *args, **kwargs)


async def _get_topic_lock(user_id: int) -> asyncio.Lock:
    async with _topic_locks_guard:
        lock = _topic_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _topic_locks[user_id] = lock
        return lock


def update_user_db(user: telegram.User) -> None:
    with session_scope() as db:
        existing = db.query(User).filter(User.user_id == user.id).first()
        if existing:
            existing.first_name = user.first_name
            existing.last_name = user.last_name
            existing.username = user.username
            existing.is_premium = bool(getattr(user, "is_premium", False))
            db.add(existing)
            return
        db.add(
            User(
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                username=user.username,
                is_premium=bool(getattr(user, "is_premium", False)),
            )
        )


def get_user_by_id(user_id: int) -> User | None:
    with session_scope() as db:
        return db.query(User).filter(User.user_id == user_id).first()


def get_user_by_thread(message_thread_id: int) -> User | None:
    with session_scope() as db:
        return db.query(User).filter(User.message_thread_id == message_thread_id).first()


def get_thread_status(message_thread_id: int) -> str:
    with session_scope() as db:
        status = (
            db.query(ForumStatus)
            .filter(
                ForumStatus.chat_id == admin_group_id,
                ForumStatus.message_thread_id == message_thread_id,
            )
            .first()
        )
        return status.status if status else "opened"


def set_thread_status(message_thread_id: int, status: str) -> None:
    with session_scope() as db:
        item = (
            db.query(ForumStatus)
            .filter(
                ForumStatus.chat_id == admin_group_id,
                ForumStatus.message_thread_id == message_thread_id,
            )
            .first()
        )
        if item:
            item.status = status
            db.add(item)
        else:
            db.add(
                ForumStatus(
                    chat_id=admin_group_id,
                    message_thread_id=message_thread_id,
                    status=status,
                )
            )


def reset_user_topic(user_id: int) -> None:
    with session_scope() as db:
        item = db.query(User).filter(User.user_id == user_id).first()
        if item:
            item.message_thread_id = 0
            db.add(item)


def save_message_map(user_id: int, user_message_id: int, group_message_id: int) -> None:
    with session_scope() as db:
        db.add(
            MessageMap(
                user_chat_message_id=user_message_id,
                group_chat_message_id=group_message_id,
                user_id=user_id,
            )
        )


def find_group_reply_id(user_message_id: int) -> int | None:
    with session_scope() as db:
        msg_map = (
            db.query(MessageMap)
            .filter(MessageMap.user_chat_message_id == user_message_id)
            .order_by(MessageMap.id.desc())
            .first()
        )
        return msg_map.group_chat_message_id if msg_map else None


def find_user_reply_id(group_message_id: int) -> int | None:
    with session_scope() as db:
        msg_map = (
            db.query(MessageMap)
            .filter(MessageMap.group_chat_message_id == group_message_id)
            .order_by(MessageMap.id.desc())
            .first()
        )
        return msg_map.user_chat_message_id if msg_map else None


def save_media_group_message(chat_id: int, message_id: int, media_group_id: str, caption_html: str | None) -> None:
    with session_scope() as db:
        existing = (
            db.query(MediaGroupMessage)
            .filter(
                MediaGroupMessage.chat_id == chat_id,
                MediaGroupMessage.message_id == message_id,
            )
            .first()
        )
        if existing:
            return
        db.add(
            MediaGroupMessage(
                chat_id=chat_id,
                message_id=message_id,
                media_group_id=str(media_group_id),
                caption_html=caption_html,
            )
        )


def get_media_group_messages(chat_id: int, media_group_id: str) -> list[MediaGroupMessage]:
    with session_scope() as db:
        return (
            db.query(MediaGroupMessage)
            .filter(
                MediaGroupMessage.chat_id == chat_id,
                MediaGroupMessage.media_group_id == str(media_group_id),
            )
            .order_by(MediaGroupMessage.message_id.asc())
            .all()
        )


def get_user_message_ids(user_id: int) -> list[int]:
    with session_scope() as db:
        return [item.user_chat_message_id for item in db.query(MessageMap).filter(MessageMap.user_id == user_id).all()]


def get_all_users() -> list[User]:
    with session_scope() as db:
        return db.query(User).all()


def count_all_users() -> int:
    with session_scope() as db:
        return db.query(User).count()


def count_banned_users() -> int:
    with session_scope() as db:
        return db.query(User).filter(User.is_banned.is_(True)).count()


def get_banned_users(limit: int = 20) -> list[User]:
    with session_scope() as db:
        return (
            db.query(User)
            .filter(User.is_banned.is_(True))
            .order_by(User.banned_at.desc().nullslast(), User.id.desc())
            .limit(limit)
            .all()
        )


def count_message_maps() -> int:
    with session_scope() as db:
        return db.query(MessageMap).count()


def count_topics_by_status(status: str) -> int:
    with session_scope() as db:
        return db.query(ForumStatus).filter(ForumStatus.chat_id == admin_group_id, ForumStatus.status == status).count()


def set_user_ban(user_id: int, is_banned: bool, admin_id: int | None, reason: str | None = None) -> User | None:
    with session_scope() as db:
        item = db.query(User).filter(User.user_id == user_id).first()
        if not item:
            return None
        item.is_banned = is_banned
        if is_banned:
            item.banned_at = datetime.now(timezone.utc)
            item.banned_by = admin_id
            item.ban_reason = reason
        else:
            item.banned_at = None
            item.banned_by = None
            item.ban_reason = None
        db.add(item)
        db.flush()
        db.refresh(item)
        return item


def format_user_label(user: User) -> str:
    name = " ".join(part for part in [user.first_name, user.last_name] if part).strip() or "未知用户"
    username = f"@{user.username}" if user.username else "无 username"
    return f"{escape(name)} / {escape(username)} / <code>{user.user_id}</code>"


def format_datetime(value: datetime | None) -> str:
    if not value:
        return "未知时间"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return escape(value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))


def admin_panel_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("系统状态", callback_data="admin:status"),
                InlineKeyboardButton("封禁列表", callback_data="admin:banlist"),
            ],
            [InlineKeyboardButton("管理指令说明", callback_data="admin:help")],
        ]
    )


def user_start_markup(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("开始安全验证", callback_data=f"captcha:{user_id}:refresh:0")]]
    )


def admin_help_text() -> str:
    return (
        "<b>管理指令说明</b>\n\n"
        "<code>/status</code>：查看系统状态。\n"
        "<code>/ban 备注</code>：在用户会话话题内封禁当前用户，备注必填。\n"
        "<code>/ban 用户ID 备注</code>：封禁指定用户。\n"
        "<code>/ban list [数量]</code>：查看封禁用户列表。\n"
        "<code>/unban</code>：在用户会话话题内解除当前用户封禁。\n"
        "<code>/unban 用户ID</code>：解除指定用户封禁。\n"
        "<code>/clear</code>：删除当前用户会话话题。\n"
        "<code>/broadcast</code>：回复一条消息后广播给未封禁用户。"
    )


async def build_ban_list_text(limit: int = 20) -> str:
    banned_users = await db_call(get_banned_users, limit)
    total = await db_call(count_banned_users)
    if not banned_users:
        return "当前没有封禁用户。"
    lines = [f"<b>封禁用户列表</b>（显示 {len(banned_users)}/{total}）"]
    for index, item in enumerate(banned_users, start=1):
        reason = escape(item.ban_reason or "无备注")
        banned_by = f"<code>{item.banned_by}</code>" if item.banned_by else "未知管理员"
        lines.append(
            f"\n{index}. {format_user_label(item)}\n"
            f"时间：{format_datetime(item.banned_at)}\n"
            f"管理员：{banned_by}\n"
            f"备注：{reason}"
        )
    return "\n".join(lines)


async def build_status_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    db_ok = True
    db_error = ""
    try:
        total_users = await db_call(count_all_users)
        banned_users = await db_call(count_banned_users)
        open_topics = await db_call(count_topics_by_status, "opened")
        closed_topics = await db_call(count_topics_by_status, "closed")
        deleted_topics = await db_call(count_topics_by_status, "deleted")
        message_maps = await db_call(count_message_maps)
    except Exception as exc:
        logger.exception("Status database check failed")
        db_ok = False
        db_error = str(exc)
        total_users = banned_users = open_topics = closed_topics = deleted_topics = message_maps = 0

    try:
        admin_group = await context.bot.get_chat(admin_group_id)
        group_title = admin_group.title or str(admin_group_id)
        group_ok = True
    except TelegramError as exc:
        group_title = str(admin_group_id)
        group_ok = False
        db_error = db_error or str(exc)

    return (
        f"<b>{escape(app_name)} 状态</b>\n\n"
        f"Bot：online\n"
        f"后台群：{escape(group_title)} (<code>{admin_group_id}</code>)\n"
        f"后台群检查：{'ok' if group_ok else 'failed'}\n"
        f"数据库：{'ok' if db_ok else 'failed'}\n"
        f"管理员数：{len(admin_user_ids)}\n"
        f"已记录用户：{total_users}\n"
        f"封禁用户：{banned_users}\n"
        f"话题：opened={open_topics} closed={closed_topics} deleted={deleted_topics}\n"
        f"消息映射：{message_maps}\n"
        f"验证码：{'disabled' if disable_captcha else 'enabled'}\n"
        f"消息间隔：{message_interval}s\n"
        f"媒体组延迟：{media_group_delay}s"
        + (f"\n\n错误：<code>{escape(db_error)}</code>" if db_error else "")
    )


async def send_contact_card(chat_id: int, message_thread_id: int, user: telegram.User, context: ContextTypes.DEFAULT_TYPE) -> None:
    buttons = [
        [
            InlineKeyboardButton(
                "🏆 高级会员" if getattr(user, "is_premium", False) else "✈️ 普通会员",
                url="https://github.com/acmuhan/Telegram-interactive-bot",
            )
        ]
    ]
    if user.username:
        buttons.append([InlineKeyboardButton("👤 直接联络", url=f"https://t.me/{user.username}")])

    user_photo = await context.bot.get_user_profile_photos(user.id)
    caption = f"👤 {mention_html(user.id, user.first_name)}\n\n📱 {user.id}\n\n🔗 @{escape(user.username) if user.username else '无'}"
    if user_photo.total_count:
        photo = user_photo.photos[0][-1].file_id
        await context.bot.send_photo(
            chat_id,
            photo=photo,
            caption=caption,
            message_thread_id=message_thread_id,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML",
        )
    else:
        await context.bot.send_contact(
            chat_id,
            phone_number="00000000000",
            first_name=user.first_name,
            last_name=user.last_name or "",
            message_thread_id=message_thread_id,
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not update.message or not user:
        return
    await db_call(update_user_db, user)

    if user.id in admin_user_ids:
        try:
            admin_group = await context.bot.get_chat(admin_group_id)
            if admin_group.type not in {"supergroup", "group"}:
                raise RuntimeError("ADMIN_GROUP_ID 不是群组")
        except BadRequest:
            logger.error(
                "Admin group check failed: chat not found. ADMIN_GROUP_ID=%s. "
                "Make sure the bot has been added to the admin group and the group id is correct.",
                admin_group_id,
            )
            await update.message.reply_html(
                "⚠️ 后台管理群组设置错误：Telegram 返回 <code>Chat not found</code>。\n\n"
                f"当前 ADMIN_GROUP_ID：<code>{admin_group_id}</code>\n\n"
                "请检查：\n"
                "1. 机器人是否已经加入后台群；\n"
                "2. 后台群 ID 是否完整，超级群通常以 <code>-100</code> 开头；\n"
                "3. 如果刚把普通群升级为话题群/超级群，请重新获取群 ID；\n"
                "4. 机器人是否拥有消息管理和话题管理权限。"
            )
            return
        except Exception as exc:
            logger.exception("Admin group check failed")
            await update.message.reply_html(
                "⚠️ 后台管理群组设置错误，请检查机器人是否已入群并拥有消息/话题管理权限。\n"
                f"错误细节：{escape(str(exc))}"
            )
            return
        await update.message.reply_html(
            f"管理员您好，{mention_html(user.id, user.first_name)}（<code>{user.id}</code>）。\n\n"
            f"<b>{escape(app_name)}</b> 已连接后台群：<b>{escape(admin_group.title or '')}</b>。\n"
            "您可以通过下方按钮查看状态或打开管理指令说明。",
            reply_markup=admin_panel_markup(),
        )
        return

    await update.message.reply_html(
        f"{mention_html(user.id, user.full_name)}，您好。\n\n{escape(welcome_message)}\n\n"
        "发送正式消息前，请先完成安全验证。",
        reply_markup=user_start_markup(user.id),
    )


def _captcha_penalty_seconds(fail_count: int) -> int:
    base = max(CAPTCHA_COOLDOWN_SECONDS, 30)
    return min(base * (2 ** max(fail_count - 1, 0)), 15 * 60)


def _new_numeric_captcha() -> dict:
    digits = random.sample(range(10), 5)
    solution = sorted(str(digit) for digit in digits)
    now = time.time()
    return {
        "type": "numeric_sequence",
        "digits": [str(digit) for digit in digits],
        "solution": solution,
        "progress": [],
        "created_at": now,
        "expires_at": now + 120,
    }


def _captcha_markup(user_id: int, state: dict) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(str(digit), callback_data=f"captcha:{user_id}:digit:{digit}") for digit in state["digits"]]
    rows = [buttons[index : index + 5] for index in range(0, len(buttons), 5)]
    rows.append([InlineKeyboardButton("刷新验证题", callback_data=f"captcha:{user_id}:refresh:0")])
    return InlineKeyboardMarkup(rows)


def _captcha_text(state: dict) -> str:
    progress = " ".join(state.get("progress") or []) or "尚未选择"
    expires_at = format_datetime(datetime.fromtimestamp(float(state["expires_at"]), tz=timezone.utc))
    return (
        "<b>安全验证</b>\n\n"
        "为保障服务质量，请先完成验证。验证通过后，系统将为您建立正式对话。\n\n"
        "请按照从小到大的顺序依次点击下方数字。\n"
        f"当前选择：<code>{escape(progress)}</code>\n"
        f"有效期至：{expires_at}"
    )


async def send_captcha_challenge(
    chat_id: int,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    force_new: bool = False,
    query: telegram.CallbackQuery | None = None,
) -> None:
    state = context.user_data.get("captcha_state")
    now = time.time()
    if force_new or not state or state.get("expires_at", 0) <= now:
        state = _new_numeric_captcha()
        context.user_data["captcha_state"] = state
        context.user_data["last_captcha_time"] = now

    text = _captcha_text(state)
    markup = _captcha_markup(user_id, state)
    if query:
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


async def check_human(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    message = update.message
    if not user or not message:
        return False
    if context.user_data.get("is_human", False):
        return True

    now = time.time()
    blocked_until = float(context.user_data.get("captcha_block_until", 0) or 0)
    if blocked_until > now:
        remain = int(blocked_until - now)
        await message.reply_html(f"验证失败次数过多，请在 <code>{remain}</code> 秒后重试。")
        return False

    await send_captcha_challenge(message.chat.id, user.id, context, force_new=False)
    return False


async def callback_query_vcode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    user = query.from_user
    parts = query.data.split(":") if query.data else []
    if len(parts) != 4 or parts[0] != "captcha":
        await query.answer("验证请求无效。")
        return
    _, user_id, action, value = parts
    if user_id != str(user.id):
        await query.answer("该验证不属于当前用户。", show_alert=True)
        return

    chat = update.effective_chat
    if not chat:
        await query.answer("验证请求无效。")
        return

    now = time.time()
    blocked_until = float(context.user_data.get("captcha_block_until", 0) or 0)
    if blocked_until > now:
        remain = int(blocked_until - now)
        await query.answer(f"验证已暂时锁定，请 {remain} 秒后重试。", show_alert=True)
        return

    if action == "refresh":
        await query.answer("验证题已刷新。")
        await send_captcha_challenge(chat.id, user.id, context, force_new=True, query=query)
        return

    state = context.user_data.get("captcha_state")
    if not state or state.get("expires_at", 0) <= now:
        await query.answer("验证题已过期，已为您刷新。")
        await send_captcha_challenge(chat.id, user.id, context, force_new=True, query=query)
        return
    if action != "digit":
        await query.answer("验证请求无效。")
        return

    progress = list(state.get("progress") or [])
    solution = list(state.get("solution") or [])
    expected = solution[len(progress)] if len(progress) < len(solution) else None
    if value != expected:
        fail_count = int(context.user_data.get("captcha_fail_count", 0) or 0) + 1
        context.user_data["captcha_fail_count"] = fail_count
        penalty = _captcha_penalty_seconds(fail_count)
        context.user_data["captcha_block_until"] = now + penalty
        context.user_data["captcha_state"] = _new_numeric_captcha()
        await query.answer(f"验证失败，请 {penalty} 秒后重试。", show_alert=True)
        if query.message:
            await query.edit_message_text(
                "<b>安全验证未通过</b>\n\n"
                f"请在 <code>{penalty}</code> 秒后重新验证。连续失败会延长等待时间。",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("重新验证", callback_data=f"captcha:{user.id}:refresh:0")]]
                ),
            )
        return

    progress.append(value)
    state["progress"] = progress
    context.user_data["captcha_state"] = state
    if progress == solution:
        context.user_data["is_human"] = True
        context.user_data["captcha_verified_at"] = now
        context.user_data.pop("captcha_state", None)
        context.user_data.pop("captcha_block_until", None)
        context.user_data.pop("captcha_fail_count", None)
        context.user_data.pop("last_captcha_time", None)
        await query.answer("验证已通过。")
        if query.message:
            await query.edit_message_text(
                "<b>验证已通过</b>\n\n您现在可以发送消息，系统将为您建立正式对话。",
                parse_mode="HTML",
            )
        return

    await query.answer("选择已记录，请继续。")
    await send_captcha_challenge(chat.id, user.id, context, query=query)


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    user = query.from_user
    if user.id not in admin_user_ids:
        await query.answer("您没有权限执行此操作。", show_alert=True)
        return
    parts = query.data.split(":") if query.data else []
    if len(parts) != 2 or parts[0] != "admin":
        await query.answer("请求无效。")
        return
    action = parts[1]
    if action == "status":
        await query.answer("正在获取状态。")
        if query.message:
            await query.edit_message_text(await build_status_text(context), parse_mode="HTML", reply_markup=admin_panel_markup())
        return
    if action == "banlist":
        await query.answer("正在获取封禁列表。")
        if query.message:
            await query.edit_message_text(await build_ban_list_text(), parse_mode="HTML", reply_markup=admin_panel_markup())
        return
    if action == "help":
        await query.answer("正在打开管理说明。")
        if query.message:
            await query.edit_message_text(admin_help_text(), parse_mode="HTML", reply_markup=admin_panel_markup())
        return
    await query.answer("请求无效。")


async def ensure_user_topic(user: telegram.User, context: ContextTypes.DEFAULT_TYPE) -> int:
    lock = await _get_topic_lock(user.id)
    async with lock:
        db_user = await db_call(get_user_by_id, user.id)
        if db_user and db_user.message_thread_id:
            return db_user.message_thread_id

        forum_topic = await context.bot.create_forum_topic(
            admin_group_id,
            name=f"{user.full_name}|{user.id}"[:128],
        )
        message_thread_id = forum_topic.message_thread_id
        await db_call(_set_user_topic, user.id, message_thread_id)
        await db_call(set_thread_status, message_thread_id, "opened")
    await context.bot.send_message(
        admin_group_id,
        f"用户 {mention_html(user.id, user.full_name)} 已通过验证并开始新的会话。",
        message_thread_id=message_thread_id,
        parse_mode="HTML",
    )
    await send_contact_card(admin_group_id, message_thread_id, user, context)
    return message_thread_id


def _set_user_topic(user_id: int, message_thread_id: int) -> None:
    with session_scope() as db:
        item = db.query(User).filter(User.user_id == user_id).first()
        if item:
            item.message_thread_id = message_thread_id
            db.add(item)


async def _send_media_group_later(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    if not job or not isinstance(job.data, dict):
        return
    data = job.data
    from_chat_id = int(data["from_chat_id"])
    target_id = int(data["target_id"])
    direction = data["direction"]
    media_group_id = str(data["media_group_id"])

    media_messages = await db_call(get_media_group_messages, from_chat_id, media_group_id)
    if not media_messages:
        return

    try:
        if direction == "u2a":
            db_user = await db_call(get_user_by_id, from_chat_id)
            if not db_user or not db_user.message_thread_id:
                return
            copied = await context.bot.copy_messages(
                chat_id=target_id,
                from_chat_id=from_chat_id,
                message_ids=[m.message_id for m in media_messages],
                message_thread_id=db_user.message_thread_id,
            )
            for sent, original in zip(copied, media_messages):
                await db_call(save_message_map, from_chat_id, original.message_id, sent.message_id)
        else:
            copied = await context.bot.copy_messages(
                chat_id=target_id,
                from_chat_id=from_chat_id,
                message_ids=[m.message_id for m in media_messages],
            )
            for sent, original in zip(copied, media_messages):
                await db_call(save_message_map, target_id, sent.message_id, original.message_id)
    except TelegramError:
        logger.exception("Failed to send media group %s", media_group_id)


async def schedule_media_group(
    delay: float,
    from_chat_id: int,
    target_id: int,
    media_group_id: str,
    direction: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    name = f"sendmediagroup_{direction}_{from_chat_id}_{target_id}_{media_group_id}"
    if context.job_queue.get_jobs_by_name(name):
        return
    context.job_queue.run_once(
        _send_media_group_later,
        delay,
        name=name,
        data={
            "from_chat_id": from_chat_id,
            "target_id": target_id,
            "media_group_id": str(media_group_id),
            "direction": direction,
        },
    )


async def forwarding_message_u2a(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    if message_interval and context.user_data.get("last_message_time", 0) > time.time() - message_interval:
        await message.reply_html("消息发送过于频繁，请稍后再试。")
        return
    await db_call(update_user_db, user)
    db_user = await db_call(get_user_by_id, user.id)
    if db_user and db_user.is_banned:
        await message.reply_html("当前账号已被限制使用本服务。")
        return
    if not disable_captcha and not await check_human(update, context):
        return
    context.user_data["last_message_time"] = time.time()

    message_thread_id = await ensure_user_topic(user, context)
    if await db_call(get_thread_status, message_thread_id) == "closed":
        await message.reply_html("当前会话已关闭。如需继续联系，请通过其他渠道联系管理员重新开启会话。")
        return

    if message.media_group_id:
        await db_call(save_media_group_message, message.chat.id, message.message_id, str(message.media_group_id), message.caption_html)
        await schedule_media_group(media_group_delay, user.id, admin_group_id, str(message.media_group_id), "u2a", context)
        return

    params = {"message_thread_id": message_thread_id}
    if message.reply_to_message:
        reply_id = await db_call(find_group_reply_id, message.reply_to_message.message_id)
        if reply_id:
            params["reply_to_message_id"] = reply_id
    try:
        copied = await context.bot.copy_message(
            chat_id=admin_group_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            **params,
        )
        await db_call(save_message_map, user.id, message.message_id, copied.message_id)
    except BadRequest as exc:
        logger.warning("User to admin forwarding failed: %s", exc)
        if is_delete_topic_as_ban_forever:
            await message.reply_html("发送失败：当前会话话题已被删除，请联系管理员重新开启会话。")
        else:
            await db_call(reset_user_topic, user.id)
            await message.reply_html("发送失败：当前会话话题已被删除。请重新发送消息以创建新的会话。")
    except TelegramError as exc:
        logger.exception("User to admin forwarding failed")
        await message.reply_html(f"发送失败：{escape(str(exc))}")


async def forwarding_message_a2u(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    admin = update.effective_user
    if not message or not admin:
        return
    await db_call(update_user_db, admin)

    message_thread_id = message.message_thread_id
    if not message_thread_id:
        return
    target_user = await db_call(get_user_by_thread, message_thread_id)
    if not target_user:
        logger.debug("No user mapped for thread %s", message_thread_id)
        return
    user_id = target_user.user_id

    if message.forum_topic_created:
        await db_call(set_thread_status, message_thread_id, "opened")
        return
    if message.forum_topic_closed:
        await db_call(set_thread_status, message_thread_id, "closed")
        await context.bot.send_message(user_id, "会话已结束。管理员已关闭当前会话，后续留言将不会被转发。")
        return
    if message.forum_topic_reopened:
        await db_call(set_thread_status, message_thread_id, "opened")
        await context.bot.send_message(user_id, "会话已重新开启，您可以继续发送消息。")
        return
    if await db_call(get_thread_status, message_thread_id) == "closed":
        await message.reply_html("当前会话已关闭。请重新开启会话后再回复用户。")
        return

    if message.media_group_id:
        await db_call(save_media_group_message, message.chat.id, message.message_id, str(message.media_group_id), message.caption_html)
        await schedule_media_group(media_group_delay, message.chat.id, user_id, str(message.media_group_id), "a2u", context)
        return

    params = {}
    if message.reply_to_message:
        reply_id = await db_call(find_user_reply_id, message.reply_to_message.message_id)
        if reply_id:
            params["reply_to_message_id"] = reply_id
    try:
        copied = await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            **params,
        )
        await db_call(save_message_map, user_id, copied.message_id, message.message_id)
    except TelegramError as exc:
        logger.exception("Admin to user forwarding failed")
        await message.reply_html(f"发送失败：{escape(str(exc))}")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    if user.id not in admin_user_ids:
        await message.reply_html("您没有权限执行此操作。")
        return
    if not message.message_thread_id:
        await message.reply_html("请在用户会话话题内使用 /clear。")
        return

    target_user = await db_call(get_user_by_thread, message.message_thread_id)
    await context.bot.delete_forum_topic(message.chat.id, message.message_thread_id)
    if is_delete_topic_as_ban_forever:
        await db_call(set_thread_status, message.message_thread_id, "closed")
    elif target_user:
        await db_call(reset_user_topic, target_user.user_id)
        await db_call(set_thread_status, message.message_thread_id, "deleted")

    if not is_delete_user_messages or not target_user:
        return

    message_ids = await db_call(get_user_message_ids, target_user.user_id)
    for chunk in _chunked(message_ids):
        try:
            await context.bot.delete_messages(target_user.user_id, chunk)
        except TelegramError:
            logger.warning("Failed to delete a chunk of user messages", exc_info=True)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    if user.id not in admin_user_ids:
        await message.reply_html("您没有权限执行此操作。")
        return

    await message.reply_html(await build_status_text(context), reply_markup=admin_panel_markup())


async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    admin = update.effective_user
    if not message or not admin:
        return
    if admin.id not in admin_user_ids:
        await message.reply_html("您没有权限执行此操作。")
        return

    if context.args and context.args[0].lower() == "list":
        limit = 20
        if len(context.args) > 1:
            try:
                limit = max(1, min(int(context.args[1]), 50))
            except ValueError:
                await message.reply_html("用法：<code>/ban list [数量]</code>，数量必须是 1-50。")
                return
        await message.reply_html(await build_ban_list_text(limit), reply_markup=admin_panel_markup())
        return

    target_user_id: int | None = None
    reason_parts: list[str] = []
    if context.args:
        try:
            target_user_id = int(context.args[0])
            reason_parts = context.args[1:]
        except ValueError:
            await message.reply_html("用法：请在用户话题内发送 <code>/ban 备注</code>，或发送 <code>/ban 用户ID 备注</code>。备注必填。")
            return
    elif message.message_thread_id:
        target_user = await db_call(get_user_by_thread, message.message_thread_id)
        if target_user:
            target_user_id = target_user.user_id

    if not target_user_id:
        await message.reply_html("请在用户会话话题内使用 /ban，或使用 <code>/ban 用户ID 备注</code>。备注必填。")
        return
    if target_user_id in admin_user_ids:
        await message.reply_html("无法封禁管理员账号。")
        return

    reason = " ".join(reason_parts).strip()
    if not reason:
        await message.reply_html("封禁备注必填。用法：在用户话题内发送 <code>/ban 备注</code>，或发送 <code>/ban 用户ID 备注</code>。")
        return
    target_user = await db_call(set_user_ban, target_user_id, True, admin.id, reason)
    if not target_user:
        await message.reply_html(f"未找到用户 <code>{target_user_id}</code>。请确认该用户已经与机器人产生过会话记录。")
        return

    if target_user.message_thread_id:
        await db_call(set_thread_status, target_user.message_thread_id, "closed")

    try:
        await context.bot.send_message(target_user_id, "当前账号已被限制使用本服务。")
    except TelegramError:
        logger.info("Failed to notify banned user %s", target_user_id, exc_info=True)

    await message.reply_html(
        f"已封禁用户 <code>{target_user_id}</code>。"
        f"\n备注：{escape(reason)}"
    )


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    admin = update.effective_user
    if not message or not admin:
        return
    if admin.id not in admin_user_ids:
        await message.reply_html("您没有权限执行此操作。")
        return

    target_user_id: int | None = None
    if context.args:
        try:
            target_user_id = int(context.args[0])
        except ValueError:
            await message.reply_html("用法：在用户话题内发送 <code>/unban</code>，或发送 <code>/unban 用户ID</code>。")
            return
    elif message.message_thread_id:
        target_user = await db_call(get_user_by_thread, message.message_thread_id)
        if target_user:
            target_user_id = target_user.user_id

    if not target_user_id:
        await message.reply_html("请在用户会话话题内使用 /unban，或使用 <code>/unban 用户ID</code>。")
        return

    target_user = await db_call(set_user_ban, target_user_id, False, admin.id)
    if not target_user:
        await message.reply_html(f"未找到用户 <code>{target_user_id}</code>。请确认该用户已经与机器人产生过会话记录。")
        return

    if target_user.message_thread_id:
        await db_call(set_thread_status, target_user.message_thread_id, "opened")

    try:
        await context.bot.send_message(target_user_id, "封禁已解除，您可以继续使用本服务。")
    except TelegramError:
        logger.info("Failed to notify unbanned user %s", target_user_id, exc_info=True)

    await message.reply_html(f"已解除封禁用户 <code>{target_user_id}</code>。")


async def _broadcast(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.job or not isinstance(context.job.data, dict):
        return
    msg_id = context.job.data["message_id"]
    chat_id = context.job.data["chat_id"]
    users = await db_call(get_all_users)
    success = 0
    failed = 0
    for item in users:
        if item.is_banned:
            failed += 1
            continue
        try:
            chat = await context.bot.get_chat(item.user_id)
            await chat.send_copy(chat_id, msg_id)
            success += 1
        except (Forbidden, BadRequest, TelegramError):
            failed += 1
    logger.info("Broadcast finished: success=%s failed=%s", success, failed)


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    if user.id not in admin_user_ids:
        await message.reply_html("您没有权限执行此操作。")
        return
    if not message.reply_to_message:
        await message.reply_html("这条指令需要回复一条消息，被回复的消息将被广播。")
        return
    context.job_queue.run_once(
        _broadcast,
        0,
        data={"message_id": message.reply_to_message.message_id, "chat_id": message.chat.id},
    )
    await message.reply_html("广播任务已加入队列。")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    if user.id not in admin_user_ids:
        await message.reply_html("您没有权限执行此操作。")
        return
    await message.reply_html(admin_help_text(), reply_markup=admin_panel_markup())


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update %s: %s", update, context.error, exc_info=context.error)


async def post_init(application) -> None:
    """Register Telegram command suggestions after the bot starts."""
    commands = [
        ("start", "开始使用 / 检查后台群配置"),
        ("status", "查看机器人状态"),
        ("ban", "封禁用户或查看封禁列表"),
        ("unban", "解除用户封禁"),
        ("clear", "删除当前用户会话话题"),
        ("broadcast", "广播被回复的消息"),
        ("help", "查看管理指令说明"),
    ]
    try:
        await application.bot.set_my_commands(commands, scope=telegram.BotCommandScopeDefault())
        await application.bot.set_my_commands(commands, scope=telegram.BotCommandScopeAllGroupChats())
        await application.bot.set_my_commands(
            [("start", "开始使用 / 管理员检查后台群配置")],
            scope=telegram.BotCommandScopeAllPrivateChats(),
        )
    except TelegramError:
        logger.warning("Failed to register Telegram bot commands", exc_info=True)


def build_application():
    builder = ApplicationBuilder().token(bot_token).post_init(post_init)
    if enable_pickle_persistence:
        os.makedirs(os.path.dirname(PERSISTENCE_PATH), exist_ok=True)
        persistence = PicklePersistence(filepath=PERSISTENCE_PATH)
        builder = builder.persistence(persistence=persistence)
    application = builder.build()
    application.add_handler(CommandHandler("start", start, filters.ChatType.PRIVATE))
    application.add_handler(MessageHandler(~filters.COMMAND & filters.ChatType.PRIVATE, forwarding_message_u2a))
    application.add_handler(MessageHandler(~filters.COMMAND & filters.Chat([admin_group_id]), forwarding_message_a2u))
    application.add_handler(CommandHandler("clear", clear, filters.Chat([admin_group_id])))
    application.add_handler(CommandHandler("ban", ban, filters.Chat([admin_group_id])))
    application.add_handler(CommandHandler("unban", unban, filters.Chat([admin_group_id])))
    application.add_handler(CommandHandler("status", status, filters.Chat([admin_group_id])))
    application.add_handler(CommandHandler("broadcast", broadcast, filters.Chat([admin_group_id])))
    application.add_handler(CommandHandler("help", help_command, filters.Chat([admin_group_id])))
    application.add_handler(CallbackQueryHandler(callback_query_vcode, pattern="^captcha:"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin:"))
    application.add_error_handler(error_handler)
    return application


if __name__ == "__main__":
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)
