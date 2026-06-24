"""
C组 数据库结构  ·  models.py   (SQLAlchemy 2.0)
PostgreSQL (Railway 线上) / SQLite (本地开发) 通用 —— 由 DATABASE_URL 切换。

数据底座: 字典(经纪人/艺人/妈咪/助理/场所) + 报单 + 审核队列 + 业务流水。
报单的结算值(应得/月底应结/反水...)不存表, 由 core.settle 即时算, 单一真相源。
"""
from datetime import datetime, date
from typing import Optional
from sqlalchemy import (String, Integer, Float, Boolean, Date, DateTime,
                        Text, ForeignKey, func)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ─────────────────────── 字典层 ───────────────────────
class MasterData(Base):                   # 统一基础资料表 (Block J): 人/地主数据源
    __tablename__ = "master_data"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(10))         # 艺人/主妈咪/助理/经纪人/场所
    name: Mapped[str] = mapped_column(String(50))         # 主名(系统认这个)
    aliases: Mapped[Optional[str]] = mapped_column(String(200))   # 逗号分隔; 错名不收
    linked_to: Mapped[Optional[int]] = mapped_column(ForeignKey("master_data.id"))  # 艺人→经纪人; 助理→主妈咪
    commission_pct: Mapped[Optional[float]] = mapped_column(Float)   # 仅经纪人
    is_shareholder: Mapped[Optional[bool]] = mapped_column(Boolean)  # 仅经纪人
    default_ticket: Mapped[Optional[float]] = mapped_column(Float)   # 仅场所
    rooms: Mapped[Optional[str]] = mapped_column(Text)              # 仅场所
    disambig: Mapped[Optional[str]] = mapped_column(String(50))     # 同名消歧标签, 录单弹此
    status: Mapped[str] = mapped_column(String(6), default="在用")   # 在用/停用(停用不删行)


class Broker(Base):                       # 经纪人 / 合伙人
    __tablename__ = "brokers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    pct: Mapped[float] = mapped_column(Float)            # 提成% (0.07 / 0.10)
    is_shareholder: Mapped[bool] = mapped_column(Boolean, default=False)
    artists: Mapped[list["Artist"]] = relationship(back_populates="broker")


class Artist(Base):                       # 艺人
    __tablename__ = "artists"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    aliases: Mapped[Optional[str]] = mapped_column(String(200))   # 逗号分隔
    broker_id: Mapped[Optional[int]] = mapped_column(ForeignKey("brokers.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    tg_user_id: Mapped[Optional[str]] = mapped_column(String(40))  # 私发月报用
    broker: Mapped[Optional[Broker]] = relationship(back_populates="artists")


class Mama(Base):                         # 主妈咪 (谁带的客, 不再有合作状态)
    __tablename__ = "mamas"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    aliases: Mapped[Optional[str]] = mapped_column(String(200))
    settlement_cycle: Mapped[str] = mapped_column(String(10), default="半月结")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    assistants: Mapped[list["MamaAssistant"]] = relationship(back_populates="mama")


class MamaAssistant(Base):                # 助理/带房/预约人 → 主妈咪 (模糊匹配)
    __tablename__ = "mama_assistants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    mama_id: Mapped[int] = mapped_column(ForeignKey("mamas.id"))
    mama: Mapped[Mama] = relationship(back_populates="assistants")


class Venue(Base):                        # 场所
    __tablename__ = "venues"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    aliases: Mapped[Optional[str]] = mapped_column(String(200))
    vtype: Mapped[str] = mapped_column(String(20), default="标准")     # 标准/酒店公寓
    default_ticket: Mapped[float] = mapped_column(Float, default=0.0)  # 默认门票
    rooms: Mapped[Optional[str]] = mapped_column(Text)                 # 包厢号, 逗号分隔


# ─────────────────────── 报单层 (核心) ───────────────────────
class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[Optional[str]] = mapped_column(String(8), unique=True, index=True)  # YYMMDDNN 业务主键(Block G)
    seq: Mapped[Optional[int]] = mapped_column(Integer)        # 业务序号(可断号)
    biz_date: Mapped[date] = mapped_column(Date)              # 归属日期(12点边界)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id"))
    venue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("venues.id"))
    room: Mapped[Optional[str]] = mapped_column(String(40))
    booker: Mapped[Optional[str]] = mapped_column(String(50))  # 预约人/助理 (F列)
    mama_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mamas.id"))  # 空=自单
    # 分成预设 (Block A): 标准/无水单/代收无水/自单/自定义; on_company_books 由 preset 唯一决定
    preset: Mapped[str] = mapped_column(String(10), default="标准")
    cust_a: Mapped[Optional[float]] = mapped_column(Float)     # 仅"自定义"档: 艺/妈/公比例
    cust_m: Mapped[Optional[float]] = mapped_column(Float)
    cust_c: Mapped[Optional[float]] = mapped_column(Float)
    flow: Mapped[Optional[str]] = mapped_column(String(4))     # 现金流向 A/B/D/E
    # 金额
    credit_k: Mapped[float] = mapped_column(Float, default=0.0)   # 挂账
    cash_m: Mapped[float] = mapped_column(Float, default=0.0)     # 现金
    ticket_o: Mapped[float] = mapped_column(Float, default=0.0)   # 门票
    wp: Mapped[Optional[float]] = mapped_column(Float)            # 工价(小费单用)
    currency_k: Mapped[str] = mapped_column(String(6), default="MYR")
    currency_m: Mapped[str] = mapped_column(String(6), default="MYR")
    settle_rate: Mapped[Optional[float]] = mapped_column(Float)   # 给艺人结算汇率
    market_rate: Mapped[Optional[float]] = mapped_column(Float)   # 公司换汇市价
    # 杂项
    customer: Mapped[Optional[str]] = mapped_column(String(20))   # 客人代号
    start_time: Mapped[Optional[str]] = mapped_column(String(8))
    end_time: Mapped[Optional[str]] = mapped_column(String(8))
    remark: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="已审核")  # 已审核/作废
    settle_status: Mapped[str] = mapped_column(String(6), default="待结")  # 待结/已结(Block G 单笔级)
    source_msg_id: Mapped[Optional[str]] = mapped_column(String(60))  # 溯源到群消息
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ─────────────────────── 审核队列 (机器人→人工) ───────────────────────
class ReviewItem(Base):
    __tablename__ = "review_queue"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_group: Mapped[str] = mapped_column(String(20))     # 报单群 / 财务群
    raw_message: Mapped[str] = mapped_column(Text)            # 原始群消息
    parsed_json: Mapped[Optional[str]] = mapped_column(Text)  # Claude解析结果(待审)
    parse_warnings: Mapped[Optional[str]] = mapped_column(Text)  # 必问清单触发的疑点
    status: Mapped[str] = mapped_column(String(10), default="待审")  # 待审/已确认/已拒
    tg_msg_id: Mapped[Optional[str]] = mapped_column(String(60))
    tg_sender: Mapped[Optional[str]] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


# ─────────────────────── 业务流水层 ───────────────────────
class Payment(Base):                      # 收款流水 (Block G 单一真相源)
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    pay_date: Mapped[date] = mapped_column(Date)
    mama_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mamas.id"))
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(6), default="MYR")
    covers: Mapped[Optional[str]] = mapped_column(Text)   # 逗号分隔 order_id (这笔收款冲哪几张工单)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MamaSettlement(Base):               # 妈咪结款 (旧表, 渐被 Payment 取代)
    __tablename__ = "mama_settlements"
    id: Mapped[int] = mapped_column(primary_key=True)
    mama_id: Mapped[int] = mapped_column(ForeignKey("mamas.id"))
    period: Mapped[Optional[str]] = mapped_column(String(20))   # 关联周期
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(6), default="MYR")
    settled_at: Mapped[date] = mapped_column(Date)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class Advance(Base):                      # 预支 / 罚款 (艺人+经纪人通用)
    __tablename__ = "advances"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(10))   # 艺人 / 经纪人
    subject_name: Mapped[str] = mapped_column(String(50))
    kind: Mapped[str] = mapped_column(String(10))           # 预支 / 罚款
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(6), default="MYR")
    status: Mapped[str] = mapped_column(String(10), default="待扣")
    related_month: Mapped[Optional[str]] = mapped_column(String(10))
    repay_date: Mapped[Optional[date]] = mapped_column(Date)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Expense(Base):                      # 运营成本 / 支出
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    spend_date: Mapped[date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(30))       # 场地/伙食/日用品/广告
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(6), default="MYR")
    notes: Mapped[Optional[str]] = mapped_column(Text)


class Fx(Base):                           # 换汇流水 (Block E 汇差来源, 次月发薪时实记)
    __tablename__ = "fx_flows"
    id: Mapped[int] = mapped_column(primary_key=True)
    fx_date: Mapped[date] = mapped_column(Date)
    out_ccy: Mapped[str] = mapped_column(String(6))        # 换出外币 MYR/USDT
    out_amount: Mapped[float] = mapped_column(Float)        # 换出金额
    in_rmb: Mapped[float] = mapped_column(Float)            # 实收 RMB (真实率藏在 in_rmb/out_amount)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ExchangeRate(Base):                 # 结算率配置 (对艺人记账用; 真实率不在此表, 见 Fx)
    __tablename__ = "exchange_rates"
    id: Mapped[int] = mapped_column(primary_key=True)
    rate_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(6))       # RMB / USDT
    real_rate: Mapped[float] = mapped_column(Float)        # 市价
    settle_rate: Mapped[float] = mapped_column(Float)      # 结算价(公司赚汇差)


class OperationLog(Base):                 # 操作日志 (留痕)
    __tablename__ = "operation_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    action: Mapped[str] = mapped_column(String(30))
    target: Mapped[Optional[str]] = mapped_column(String(60))
    detail: Mapped[Optional[str]] = mapped_column(Text)
    operator: Mapped[str] = mapped_column(String(30), default="系统")
