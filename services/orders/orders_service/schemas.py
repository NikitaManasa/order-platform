from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OrderItemIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=100)
    unit_price_cents: int = Field(ge=0)


class OrderCreate(BaseModel):
    # No customer_id here: the customer is whoever the access token says it is.
    items: list[OrderItemIn] = Field(min_length=1, max_length=50)


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sku: str
    quantity: int
    unit_price_cents: int


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str
    status: str
    total_cents: int
    cancel_reason: str | None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemOut]
