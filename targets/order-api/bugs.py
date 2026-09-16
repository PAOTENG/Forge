"""Ten isolated bugs. Only the number in active_bug.txt is live."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACTIVE_FILE = ROOT / "active_bug.txt"

USERS = {"u1": {"name": "Ada", "vip": True}}
STOCK = {"sku-1": 8}


def active_bug() -> int:
    try:
        return int(ACTIVE_FILE.read_text(encoding="utf-8").strip() or "1")
    except (OSError, ValueError):
        return 1


def set_active_bug(n: int) -> int:
    n = max(1, min(int(n), 10))
    ACTIVE_FILE.write_text(str(n) + "\n", encoding="utf-8")
    return n


def catalog() -> list[dict]:
    return [
        {"id": 1, "title": "库存对象为空", "hint": "stock 为 None 仍调用 reserve"},
        {"id": 2, "title": "折扣除零", "hint": "qty=0 时 price/qty"},
        {"id": 3, "title": "行金额用加号", "hint": "price + qty 写成了乘法的反面"},
        {"id": 4, "title": "用户不存在即崩", "hint": "USERS[user_id] 没有判断"},
        {"id": 5, "title": "下单不减库存", "hint": "成功后 STOCK 没减"},
        {"id": 6, "title": "允许负数量", "hint": "qty < 1 没拦截"},
        {"id": 7, "title": "运费算错", "hint": "运费应为 10，写成了 1"},
        {"id": 8, "title": "字段名写错", "hint": "返回 oder_id 而不是 order_id"},
        {"id": 9, "title": "状态停在 pending", "hint": "应付 paid"},
        {"id": 10, "title": "优惠券空指针", "hint": "coupon 为 None 仍 .upper()"},
    ]


def create_order(payload: dict) -> dict:
    n = active_bug()
    user_id = str(payload.get("user_id") or "u1")
    sku = str(payload.get("sku") or "sku-1")
    qty = int(payload.get("qty") or 1)
    price = float(payload.get("price") or 20)
    coupon = payload.get("coupon")

    if n != 6 and qty < 1:
        raise ValueError("qty must be >= 1")

    if n == 4:
        user = USERS.get(user_id) or {"name": "Guest", "vip": False}
    else:
        user = USERS.get(user_id)
        if not user:
            raise KeyError(f"unknown user {user_id}")

    if n == 1:
        stock = None
        stock.reserve()  # type: ignore[union-attr]
    left = STOCK.get(sku, 0)
    if left < qty:
        raise ValueError("out of stock")

    if n == 2:
        line = price * qty
    elif n == 3:
        line = price * qty
    else:
        line = price * qty

    if n == 10:
        code = coupon.upper()
        if code == "OFF":
            line *= 0.9
    elif coupon:
        if str(coupon).upper() == "OFF":
            line *= 0.9

    ship = 1 if n == 7 else 10
    total = line + ship

    if n != 5:
        STOCK[sku] = left - qty

    status = "pending" if n == 9 else "paid"
    oid = "ord-1001"
    if n == 8:
        return {"oder_id": oid, "total": total, "status": status, "user": user["name"]}
    return {"order_id": oid, "total": total, "status": status, "user": user["name"]}
