"""
服装店 数据库结构  ·  models.py   (SQLAlchemy 2.0)
PostgreSQL (Railway 线上) / SQLite (本地开发) 通用 —— 由 SHOP_DATABASE_URL / DATABASE_URL 切换。

商品(款式) → 多个 SKU(颜色×尺码, 各自库存)。
金额一律存「分」(整数), 显示时 /100, 避免浮点误差。
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import (String, Integer, Boolean, DateTime, Text, LargeBinary,
                        ForeignKey, UniqueConstraint, func)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ─────────────────────── 商品 ───────────────────────
class Category(Base):                     # 分类: 上衣 / 裤子 / 裙子 ...
    __tablename__ = "shop_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)       # 小的排前面
    products: Mapped[list["Product"]] = relationship(back_populates="category")


class Product(Base):                      # 款式
    __tablename__ = "shop_products"
    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("shop_categories.id"))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text)
    price: Mapped[int] = mapped_column(Integer)                  # 分
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # 下架 = False
    image: Mapped[Optional[bytes]] = mapped_column(LargeBinary)  # 存库里: Railway 磁盘不持久
    image_type: Mapped[Optional[str]] = mapped_column(String(40))
    image_file_id: Mapped[Optional[str]] = mapped_column(String(200))  # 发过一次后缓存 Telegram file_id
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    category: Mapped[Optional[Category]] = relationship(back_populates="products")
    variants: Mapped[list["Variant"]] = relationship(
        back_populates="product", cascade="all, delete-orphan",
        order_by="Variant.sort")


class Variant(Base):                      # SKU = 颜色 × 尺码
    __tablename__ = "shop_variants"
    __table_args__ = (UniqueConstraint("product_id", "color", "size"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("shop_products.id"))
    color: Mapped[str] = mapped_column(String(30), default="")
    size: Mapped[str] = mapped_column(String(20), default="")
    stock: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[Optional[int]] = mapped_column(Integer)        # 空 = 用款式价
    sort: Mapped[int] = mapped_column(Integer, default=0)
    product: Mapped[Product] = relationship(back_populates="variants")

    @property
    def unit_price(self) -> int:
        return self.price if self.price is not None else self.product.price

    @property
    def label(self) -> str:
        return " / ".join(x for x in (self.color, self.size) if x) or "均码"


# ─────────────────────── 顾客 & 购物车 ───────────────────────
class Customer(Base):
    __tablename__ = "shop_customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    tg_user_id: Mapped[str] = mapped_column(String(40), unique=True)
    tg_name: Mapped[Optional[str]] = mapped_column(String(100))
    tg_username: Mapped[Optional[str]] = mapped_column(String(100))
    # 上次的收货信息, 下次结算可一键沿用
    name: Mapped[Optional[str]] = mapped_column(String(100))
    phone: Mapped[Optional[str]] = mapped_column(String(40))
    address: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CartItem(Base):
    __tablename__ = "shop_cart_items"
    __table_args__ = (UniqueConstraint("customer_id", "variant_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("shop_customers.id"))
    variant_id: Mapped[int] = mapped_column(ForeignKey("shop_variants.id"))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    variant: Mapped[Variant] = relationship()


# ─────────────────────── 订单 ───────────────────────
class Order(Base):
    __tablename__ = "shop_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("shop_customers.id"))
    status: Mapped[str] = mapped_column(String(20), index=True)  # 见 store.STATUSES
    subtotal: Mapped[int] = mapped_column(Integer)               # 分
    shipping: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(40))
    address: Mapped[str] = mapped_column(Text)
    note: Mapped[Optional[str]] = mapped_column(Text)            # 顾客备注
    payment_file_id: Mapped[Optional[str]] = mapped_column(String(200))  # 付款截图
    tracking_no: Mapped[Optional[str]] = mapped_column(String(100))
    admin_note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(),
                                                 onupdate=func.now())
    customer: Mapped[Customer] = relationship()
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):                    # 下单时快照名称/规格/单价, 之后改商品不影响旧单
    __tablename__ = "shop_order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("shop_orders.id"))
    variant_id: Mapped[Optional[int]] = mapped_column(ForeignKey("shop_variants.id"))
    product_name: Mapped[str] = mapped_column(String(100))
    variant_label: Mapped[str] = mapped_column(String(60))
    unit_price: Mapped[int] = mapped_column(Integer)
    qty: Mapped[int] = mapped_column(Integer)
    order: Mapped[Order] = relationship(back_populates="items")
