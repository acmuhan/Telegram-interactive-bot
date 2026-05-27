import datetime
import pytz
from telegram.ext import ContextTypes


async def _delete_message_cb(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    if not job:
        return
    try:
        await context.bot.delete_message(job.chat_id, job.data)
    except Exception:
        # Message may already be deleted or outside Telegram's deletion window.
        return


async def delete_message_later(delay: float, chat_id: int, msg_id: int, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if not context.job_queue:
        return None
    name = f"deljob_{chat_id}_{msg_id}"
    context.job_queue.run_once(_delete_message_cb, delay, chat_id=chat_id, name=name, data=msg_id)
    return name


async def _ban_user_cb(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    if not job or not isinstance(job.data, str):
        return
    user_id, minutes = job.data.split("-")
    until_date = datetime.datetime.now(pytz.utc) + datetime.timedelta(minutes=int(minutes))
    await context.bot.ban_chat_member(job.chat_id, int(user_id), until_date)


async def ban_user_later(delay: float, chat_id: int, user_id: int, minutes: int, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if not context.job_queue:
        return None
    name = f"banjob_{chat_id}_{user_id}"
    context.job_queue.run_once(_ban_user_cb, delay, chat_id=chat_id, name=name, data=f"{user_id}-{minutes}")
    return name


def remove_job_if_exists(name: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not context.job_queue:
        return False
    current_jobs = context.job_queue.get_jobs_by_name(name)
    if not current_jobs:
        return False
    for job in current_jobs:
        job.schedule_removal()
    return True
