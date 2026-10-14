from app.services.duty import (
    dingtalk_accepted,
    forget_pending,
    format_investigation_prompt,
    h5_url,
    normalize_inbound,
    register_pending,
    session_for_pending,
)


def test_normalize_alertmanager():
    body = {
        "alerts": [
            {
                "labels": {"alertname": "Order500", "service": "order-api", "severity": "critical"},
                "annotations": {"summary": "下单接口 500", "description": "NoneType reserve"},
                "generatorURL": "http://grafana/d/1",
            }
        ]
    }
    data = normalize_inbound(body)
    assert data["source"] == "alertmanager"
    assert data["service"] == "order-api"
    assert "下单" in data["title"] or "下单" in data["summary"]


def test_normalize_manual():
    data = normalize_inbound({"title": "对账失败", "stack": "Traceback", "source": "wecom"})
    assert data["title"] == "对账失败"
    assert data["stack"] == "Traceback"


def test_ledger_prompt_opens_ledger_repo_not_order_api():
    text = format_investigation_prompt(
        {
            "title": "知识库问答超时 · abc",
            "service": "kb-qa",
            "summary": "检索超时",
            "stack": "worker down",
        }
    )
    assert "Ledger" in text
    assert "ledger-kb" in text
    assert "worker down" in text
    assert "禁止读取或修改 order-api 的 bugs.py" in text
    text = format_investigation_prompt({"title": "x", "stack": "boom"})
    assert "热修" in text
    assert "boom" in text
    assert "run_tests" in text
    assert "restart_service" in text


def test_pending_token_roundtrip():
    token = register_pending("sess-1")
    assert session_for_pending(token) == "sess-1"
    forget_pending(token)
    assert session_for_pending(token) is None


def test_card_urls_go_through_vite_proxy():
    card = h5_url("/api/duty/card", {"token": "abc"})
    assert "/api/duty/card" in card
    assert "token=abc" in card


def test_dingtalk_accepted_reads_errcode():
    assert dingtalk_accepted({"errcode": 0, "errmsg": "ok"})
    assert dingtalk_accepted({"errcode": "0"})
    assert not dingtalk_accepted({"errcode": 310000, "errmsg": "keywords not in content"})
    assert not dingtalk_accepted({})
    assert not dingtalk_accepted(None)
