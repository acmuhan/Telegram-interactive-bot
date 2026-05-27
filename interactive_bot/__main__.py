import os
import random
import time
from collections.abc import Iterable
from string import ascii_letters as letters

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
from db.model import ForumStatus, MediaGroupMessage, MessageMap, User

from . import (
    PERSISTENCE_PATH,
    admin_group_id,
    admin_user_ids,
    app_name,
    bot_token,
    disable_captcha,
    is_delete_topic_as_ban_forever,
    is_delete_user_messages,
    logger,
    media_group_delay,
    message_interval,
    welcome_message,
)
from .utils import delete_message_later

Base.metadata.create_all(bind=engine)


def _chunked(values: list[int], size: int = 100) -> Iterable[list[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


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
    caption = f"👤 {mention_html(user.id, user.first_name)}\n\n📱 {user.id}\n\n🔗 @{user.username if user.username else '无'}"
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
    update_user_db(user)

    if user.id in admin_user_ids:
        try:
            admin_group = await context.bot.get_chat(admin_group_id)
            if admin_group.type not in {"supergroup", "group"}:
                raise RuntimeError("ADMIN_GROUP_ID 不是群组")
        except BadRequest as exc:
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
                f"⚠️ 后台管理群组设置错误，请检查机器人是否已入群并拥有消息/话题管理权限。\n错误细节：{exc}"
            )
            return
        await update.message.reply_html(
            f"你好管理员 {user.first_name}({user.id})\n\n欢迎使用 {app_name} 机器人。\n当前后台群组：<b>{admin_group.title}</b>"
        )
        return

    await update.message.reply_html(f"{mention_html(user.id, user.full_name)} 同学：\n\n{welcome_message}")


async def check_human(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    message = update.message
    if not user or not message:
        return False
    if context.user_data.get("is_human", False):
        return True
    if context.user_data.get("is_human_error_time", 0) > time.time() - 120:
        await message.reply_html("你已经被禁言，请稍后再尝试。")
        return False

    image_dir = "./assets/imgs"
    file_name = random.choice(os.listdir(image_dir))
    code = file_name.replace("image_", "").replace(".png", "")
    codes = ["".join(random.sample(letters, 5)) for _ in range(7)] + [code]
    random.shuffle(codes)

    photo = context.bot_data.get(f"image|{code}") or f"{image_dir}/{file_name}"
    buttons = [InlineKeyboardButton(x, callback_data=f"vcode_{x}_{user.id}") for x in codes]
    button_matrix = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    sent = await message.reply_photo(
        photo,
        f"{mention_html(user.id, user.first_name)} 请选择图片中的文字。回答错误将无法联系客服。",
        reply_markup=InlineKeyboardMarkup(button_matrix),
        parse_mode="HTML",
    )
    if sent.photo:
        biggest_photo = max(sent.photo, key=lambda x: x.file_size or 0)
        context.bot_data[f"image|{code}"] = biggest_photo.file_id
    context.user_data["vcode"] = code
    await delete_message_later(60, sent.chat.id, sent.message_id, context)
    return False


async def callback_query_vcode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    user = query.from_user
    parts = query.data.split("_") if query.data else []
    if len(parts) != 3:
        await query.answer("验证码无效")
        return
    _, code, user_id = parts
    if user_id != str(user.id):
        await query.answer("这不是你的验证码")
        return
    if code == context.user_data.get("vcode"):
        await query.answer("正确，欢迎。")
        await context.bot.send_message(
            update.effective_chat.id,
            f"{mention_html(user.id, user.first_name)}，欢迎。",
            parse_mode="HTML",
        )
        context.user_data["is_human"] = True
    else:
        await query.answer("错误，禁言 2 分钟")
        context.user_data["is_human_error_time"] = time.time()
    try:
        await query.message.delete()
    except TelegramError:
        logger.debug("Failed to delete captcha message", exc_info=True)


async def ensure_user_topic(user: telegram.User, context: ContextTypes.DEFAULT_TYPE) -> int:
    db_user = get_user_by_id(user.id)
    if db_user and db_user.message_thread_id:
        return db_user.message_thread_id

    forum_topic = await context.bot.create_forum_topic(
        admin_group_id,
        name=f"{user.full_name}|{user.id}"[:128],
    )
    message_thread_id = forum_topic.message_thread_id
    with session_scope() as db:
        item = db.query(User).filter(User.user_id == user.id).first()
        if item:
            item.message_thread_id = message_thread_id
            db.add(item)
    set_thread_status(message_thread_id, "opened")
    await context.bot.send_message(
        admin_group_id,
        f"新的用户 {mention_html(user.id, user.full_name)} 开始了一个新的会话。",
        message_thread_id=message_thread_id,
        parse_mode="HTML",
    )
    await send_contact_card(admin_group_id, message_thread_id, user, context)
    return message_thread_id


async def _send_media_group_later(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    if not job or not isinstance(job.data, dict):
        return
    data = job.data
    from_chat_id = int(data["from_chat_id"])
    target_id = int(data["target_id"])
    direction = data["direction"]
    media_group_id = str(data["media_group_id"])

    media_messages = get_media_group_messages(from_chat_id, media_group_id)
    if not media_messages:
        return

    try:
        if direction == "u2a":
            db_user = get_user_by_id(from_chat_id)
            if not db_user or not db_user.message_thread_id:
                return
            copied = await context.bot.copy_messages(
                chat_id=target_id,
                from_chat_id=from_chat_id,
                message_ids=[m.message_id for m in media_messages],
                message_thread_id=db_user.message_thread_id,
            )
            for sent, original in zip(copied, media_messages):
                save_message_map(from_chat_id, original.message_id, sent.message_id)
        else:
            copied = await context.bot.copy_messages(
                chat_id=target_id,
                from_chat_id=from_chat_id,
                message_ids=[m.message_id for m in media_messages],
            )
            for sent, original in zip(copied, media_messages):
                save_message_map(target_id, sent.message_id, original.message_id)
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
    if not disable_captcha and not await check_human(update, context):
        return
    if message_interval and context.user_data.get("last_message_time", 0) > time.time() - message_interval:
        await message.reply_html("请不要频繁发送消息。")
        return
    context.user_data["last_message_time"] = time.time()

    update_user_db(user)
    message_thread_id = await ensure_user_topic(user, context)
    if get_thread_status(message_thread_id) == "closed":
        await message.reply_html("客服已经关闭对话。如需联系，请通过其他途径联系对方重新打开对话。")
        return

    if message.media_group_id:
        save_media_group_message(message.chat.id, message.message_id, str(message.media_group_id), message.caption_html)
        await schedule_media_group(media_group_delay, user.id, admin_group_id, str(message.media_group_id), "u2a", context)
        return

    params = {"message_thread_id": message_thread_id}
    if message.reply_to_message:
        reply_id = find_group_reply_id(message.reply_to_message.message_id)
        if reply_id:
            params["reply_to_message_id"] = reply_id
    try:
        copied = await context.bot.copy_message(
            chat_id=admin_group_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            **params,
        )
        save_message_map(user.id, message.message_id, copied.message_id)
    except BadRequest as exc:
        logger.warning("User to admin forwarding failed: %s", exc)
        if is_delete_topic_as_ban_forever:
            await message.reply_html("发送失败，你的对话已经被客服删除。请联系客服重新打开对话。")
        else:
            with session_scope() as db:
                item = db.query(User).filter(User.user_id == user.id).first()
                if item:
                    item.message_thread_id = 0
                    db.add(item)
            await message.reply_html("发送失败，你的对话已经被客服删除。请再发送一条消息来重新激活对话。")
    except TelegramError as exc:
        logger.exception("User to admin forwarding failed")
        await message.reply_html(f"发送失败：{exc}")


async def forwarding_message_a2u(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    admin = update.effective_user
    if not message or not admin:
        return
    update_user_db(admin)

    message_thread_id = message.message_thread_id
    if not message_thread_id:
        return
    target_user = get_user_by_thread(message_thread_id)
    if not target_user:
        logger.debug("No user mapped for thread %s", message_thread_id)
        return
    user_id = target_user.user_id

    if message.forum_topic_created:
        set_thread_status(message_thread_id, "opened")
        return
    if message.forum_topic_closed:
        set_thread_status(message_thread_id, "closed")
        await context.bot.send_message(user_id, "对话已经结束。对方已经关闭了对话。你的留言将被忽略。")
        return
    if message.forum_topic_reopened:
        set_thread_status(message_thread_id, "opened")
        await context.bot.send_message(user_id, "对方重新打开了对话。可以继续对话了。")
        return
    if get_thread_status(message_thread_id) == "closed":
        await message.reply_html("对话已经结束。希望和对方联系，需要打开对话。")
        return

    if message.media_group_id:
        save_media_group_message(message.chat.id, message.message_id, str(message.media_group_id), message.caption_html)
        await schedule_media_group(media_group_delay, message.chat.id, user_id, str(message.media_group_id), "a2u", context)
        return

    params = {}
    if message.reply_to_message:
        reply_id = find_user_reply_id(message.reply_to_message.message_id)
        if reply_id:
            params["reply_to_message_id"] = reply_id
    try:
        copied = await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            **params,
        )
        save_message_map(user_id, copied.message_id, message.message_id)
    except TelegramError as exc:
        logger.exception("Admin to user forwarding failed")
        await message.reply_html(f"发送失败：{exc}")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    if user.id not in admin_user_ids:
        await message.reply_html("你没有权限执行此操作。")
        return
    if not message.message_thread_id:
        await message.reply_html("请在用户会话话题内使用 /clear。")
        return

    target_user = get_user_by_thread(message.message_thread_id)
    await context.bot.delete_forum_topic(message.chat.id, message.message_thread_id)
    set_thread_status(message.message_thread_id, "closed")
    if not is_delete_user_messages or not target_user:
        return

    with session_scope() as db:
        message_ids = [
            item.user_chat_message_id
            for item in db.query(MessageMap).filter(MessageMap.user_id == target_user.user_id).all()
        ]
    for chunk in _chunked(message_ids):
        try:
            await context.bot.delete_messages(target_user.user_id, chunk)
        except TelegramError:
            logger.warning("Failed to delete a chunk of user messages", exc_info=True)


async def _broadcast(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.job or not isinstance(context.job.data, dict):
        return
    msg_id = context.job.data["message_id"]
    chat_id = context.job.data["chat_id"]
    with session_scope() as db:
        users = db.query(User).all()
    success = 0
    failed = 0
    for item in users:
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
        await message.reply_html("你没有权限执行此操作。")
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


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update %s: %s", update, context.error, exc_info=context.error)


def build_application():
    os.makedirs(os.path.dirname(PERSISTENCE_PATH), exist_ok=True)
    persistence = PicklePersistence(filepath=PERSISTENCE_PATH)
    application = ApplicationBuilder().token(bot_token).persistence(persistence=persistence).build()
    application.add_handler(CommandHandler("start", start, filters.ChatType.PRIVATE))
    application.add_handler(MessageHandler(~filters.COMMAND & filters.ChatType.PRIVATE, forwarding_message_u2a))
    application.add_handler(MessageHandler(~filters.COMMAND & filters.Chat([admin_group_id]), forwarding_message_a2u))
    application.add_handler(CommandHandler("clear", clear, filters.Chat([admin_group_id])))
    application.add_handler(CommandHandler("broadcast", broadcast, filters.Chat([admin_group_id])))
    application.add_handler(CallbackQueryHandler(callback_query_vcode, pattern="^vcode_"))
    application.add_error_handler(error_handler)
    return application


if __name__ == "__main__":
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)
