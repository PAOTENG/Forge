from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from bugs import STOCK, USERS, active_bug, catalog, create_order

app = FastAPI(title="order-api", version="0.1.0")


class OrderIn(BaseModel):
    user_id: str = "u1"
    sku: str = "sku-1"
    qty: int = 1
    price: float = 20
    coupon: str | None = None


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "order-api", "active_bug": active_bug()}


@app.get("/bugs")
def list_bugs() -> dict:
    return {"active": active_bug(), "bugs": catalog()}


@app.post("/orders")
def place_order(body: OrderIn) -> dict:
    try:
        return create_order(body.model_dump())
    except Exception as exc:
        raise HTTPException(500, f"BUG-{active_bug()}: {exc}") from exc


@app.get("/canary")
def canary() -> dict:
    n = active_bug()
    payload = {"user_id": "u1", "sku": "sku-1", "qty": 1, "price": 20, "coupon": None}
    if n == 2:
        payload["qty"] = 0
    if n == 4:
        payload["user_id"] = "missing"
    if n == 6:
        payload["qty"] = -1
    STOCK["sku-1"] = 8
    try:
        data = create_order(payload)
        if n == 5 and STOCK.get("sku-1") == 8:
            raise RuntimeError("stock not decremented")
    except Exception as exc:
        raise HTTPException(500, f"BUG-{n}: {type(exc).__name__}: {exc}") from exc
    finally:
        STOCK["sku-1"] = 8
    problems = []
    if "order_id" not in data:
        problems.append("missing order_id")
    if data.get("status") != "paid":
        problems.append(f"status={data.get('status')}")
    if abs(float(data.get("total") or 0) - 30) > 0.01:
        problems.append(f"total={data.get('total')}")
    if problems:
        raise HTTPException(500, f"BUG-{n}: " + "; ".join(problems))
    return {"ok": True, "active_bug": n, "order": data}


@app.post("/reset-stock")
def reset_stock() -> dict:
    STOCK["sku-1"] = 8
    USERS.setdefault("u1", {"name": "Ada", "vip": True})
    return {"ok": True, "stock": STOCK}
