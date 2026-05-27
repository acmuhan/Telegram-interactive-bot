from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from interactive_bot import DATABASE_URL


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    """Create the parent directory for file-based SQLite URLs before connecting."""
    if not database_url.startswith("sqlite") or database_url in {"sqlite://", "sqlite:///:memory:"}:
        return

    parsed = urlparse(database_url)
    if parsed.path in {"", "/:memory:"}:
        return

    # SQLAlchemy SQLite URL forms:
    #   sqlite:///relative/path.db  -> parsed.path == /relative/path.db
    #   sqlite:////absolute/path.db -> parsed.path == //absolute/path.db
    if database_url.startswith("sqlite:////"):
        db_path = Path("/" + unquote(parsed.path).lstrip("/"))
    else:
        db_path = Path(unquote(parsed.path.lstrip("/")))

    if db_path.parent != Path(""):
        db_path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_parent_dir(DATABASE_URL)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)
SessionMaker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope():
    session = SessionMaker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
