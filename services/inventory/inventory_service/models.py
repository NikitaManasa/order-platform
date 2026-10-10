from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (CheckConstraint("stock >= 0", name="stock_non_negative"),)

    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    price_cents: Mapped[int]
    stock: Mapped[int]


class Reservation(Base):
    """Stock held for an order, kept so it can be released if payment fails."""

    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(36), index=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"))
    quantity: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


SEED_PRODUCTS = [
    Product(sku="SKU-KEYBOARD", name="Mechanical keyboard", price_cents=8999, stock=50),
    Product(sku="SKU-MOUSE", name="Wireless mouse", price_cents=2999, stock=100),
    Product(sku="SKU-MONITOR", name='27" monitor', price_cents=24999, stock=10),
    Product(sku="SKU-LAPTOP", name="Laptop", price_cents=129999, stock=5),
]


def seed_products(session_factory: sessionmaker) -> None:
    with session_factory.begin() as session:
        if session.scalar(select(Product).limit(1)) is None:
            session.add_all(
                Product(sku=p.sku, name=p.name, price_cents=p.price_cents, stock=p.stock) for p in SEED_PRODUCTS
            )
