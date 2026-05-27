from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, UniqueConstraint
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
    message_thread_id = Column(Integer, default=0, nullable=False)
