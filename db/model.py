from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, UniqueConstraint, text
from sqlalchemy.engine import Engine
from sqlalchemy.sql import func

from .database import Base


class MediaGroupMessage(Base):
    __tablename__ = "media_group_message"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    message_id = Column(Integer, nullable=False)
    media_group_id = Column(String(128), nullable=False, index=True)
    caption_html = Column(String(1024 * 64))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_media_group_chat_message"),
    )


# Backwards-compatible alias for the original misspelled model name used by old imports.
MediaGroupMesssage = MediaGroupMessage


class ForumStatus(Base):
    __tablename__ = "formn_status"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    message_thread_id = Column(Integer, nullable=False, index=True)
    status = Column(String(64), nullable=False, default="opened")

    __table_args__ = (
        UniqueConstraint("chat_id", "message_thread_id", name="uq_forum_status_thread"),
    )


# Backwards-compatible alias for the original misspelled model name used by old imports.
FormnStatus = ForumStatus


class MessageMap(Base):
    __tablename__ = "message_map"

    id = Column(Integer, primary_key=True, index=True)
    user_chat_message_id = Column(Integer, nullable=False, index=True)
    group_chat_message_id = Column(Integer, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, unique=True, index=True, nullable=False)
    first_name = Column(String(256))
    last_name = Column(String(256))
    username = Column(String(256))
    is_premium = Column(Boolean, default=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_message_at = Column(DateTime(timezone=True))
    message_thread_id = Column(Integer, default=0, nullable=False)
    is_banned = Column(Boolean, default=False, nullable=False)
    banned_at = Column(DateTime(timezone=True))
    banned_by = Column(BigInteger)
    ban_reason = Column(String(1024))


def _sqlite_columns(engine: Engine, table_name: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table_name})")).mappings().all()
    return {str(row["name"]) for row in rows}


def run_schema_migrations(engine: Engine) -> None:
    """Apply small backwards-compatible SQLite migrations after create_all().

    This is intentionally conservative: it only adds missing nullable/defaulted
    columns that the app needs. Larger changes should move to Alembic.
    """
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as conn:
        user_columns = _sqlite_columns(engine, "user")
        if "message_thread_id" not in user_columns:
            conn.execute(text('ALTER TABLE "user" ADD COLUMN message_thread_id INTEGER NOT NULL DEFAULT 0'))
        if "is_premium" not in user_columns:
            conn.execute(text('ALTER TABLE "user" ADD COLUMN is_premium BOOLEAN DEFAULT 0'))
        if "updated_at" not in user_columns:
            conn.execute(text('ALTER TABLE "user" ADD COLUMN updated_at DATETIME'))
        if "first_seen_at" not in user_columns:
            conn.execute(text('ALTER TABLE "user" ADD COLUMN first_seen_at DATETIME'))
        if "last_message_at" not in user_columns:
            conn.execute(text('ALTER TABLE "user" ADD COLUMN last_message_at DATETIME'))
        if "is_banned" not in user_columns:
            conn.execute(text('ALTER TABLE "user" ADD COLUMN is_banned BOOLEAN NOT NULL DEFAULT 0'))
        if "banned_at" not in user_columns:
            conn.execute(text('ALTER TABLE "user" ADD COLUMN banned_at DATETIME'))
        if "banned_by" not in user_columns:
            conn.execute(text('ALTER TABLE "user" ADD COLUMN banned_by BIGINT'))
        if "ban_reason" not in user_columns:
            conn.execute(text('ALTER TABLE "user" ADD COLUMN ban_reason VARCHAR(1024)'))

        media_columns = _sqlite_columns(engine, "media_group_message")
        if "caption_html" not in media_columns:
            conn.execute(text("ALTER TABLE media_group_message ADD COLUMN caption_html VARCHAR(65536)"))
        if "created_at" not in media_columns:
            conn.execute(text("ALTER TABLE media_group_message ADD COLUMN created_at DATETIME"))

        message_map_columns = _sqlite_columns(engine, "message_map")
        if "created_at" not in message_map_columns:
            conn.execute(text("ALTER TABLE message_map ADD COLUMN created_at DATETIME"))
