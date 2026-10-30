"""数据库连接: SHOP_DATABASE_URL > DATABASE_URL > 本地 SQLite。
表名都带 shop_ 前缀, 和 C组系统共用一个 Postgres 也不冲突。"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base

_url = os.getenv("SHOP_DATABASE_URL") or os.getenv("DATABASE_URL") or "sqlite:///shop_local.db"
# Railway 给的是 postgres://, SQLAlchemy 要 postgresql://
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql://", 1)

engine = create_engine(_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db():
    Base.metadata.create_all(engine)          # 只建缺失的表


def get_session():
    return SessionLocal()
