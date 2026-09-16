from fastapi.testclient import TestClient

from app import app
from bugs import STOCK, active_bug, set_active_bug


def client() -> TestClient:
    return TestClient(app)


def test_health():
    res = client().get("/health")
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_standard_order_is_correct():
    """Current active bug should already be fixed. Switch active_bug.txt to drill the next one."""
    n = active_bug()
    STOCK["sku-1"] = 8
    res = client().post(
        "/orders",
        json={"user_id": "u1", "sku": "sku-1", "qty": 1, "price": 20, "coupon": None},
    )
    assert res.status_code == 200, f"active BUG-{n} still broken: {res.text}"
    data = res.json()
    assert "order_id" in data
    assert data["status"] == "paid"
    assert data["total"] == 30
    assert STOCK["sku-1"] == 7


def test_rejects_bad_qty():
    STOCK["sku-1"] = 8
    res = client().post(
        "/orders",
        json={"user_id": "u1", "sku": "sku-1", "qty": -2, "price": 20},
    )
    assert res.status_code in {400, 422, 500}
    if res.status_code == 200:
        raise AssertionError("negative qty must not create an order")
