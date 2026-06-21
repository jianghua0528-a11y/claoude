"""数据库连接: DATABASE_URL 有则 Postgres(线上), 无则 SQLite(本地)."""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base

_url = os.getenv("DATABASE_URL", "sqlite:///cgroup_local.db")
# Railway 给的是 postgres://, SQLAlchemy 要 postgresql://
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql://", 1)

engine = create_engine(_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)


def init_db():
    Base.metadata.create_all(engine)          # 只建缺失的表
    # 已有表补结款两轨列 (create_all 不改已有表) + 回填空状态
    from ..core.status import ensure_columns, backfill
    ensure_columns(engine)
    s = SessionLocal()
    try:
        backfill(s)
    finally:
        s.close()


def get_session():
    return SessionLocal()
