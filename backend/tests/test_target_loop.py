from pathlib import Path

from app.core.config import REPO_ROOT
from app.services.duty import format_investigation_prompt
from app.services.supervisor import bug_catalog, target_root
from app.services.watchdog import watchdog_key


def test_default_target_is_order_api():
    root = target_root()
    assert root.name == "order-api"
    assert (root / "active_bug.txt").is_file()
    assert (root / ".forge-host-tests").is_file()


def test_prompt_asks_for_restart_and_push():
    text = format_investigation_prompt({"title": "x", "stack": "boom"})
    assert "restart_service" in text
    assert "git_push" in text


def test_watchdog_key_changes_when_down_or_bug_changes():
    fail = {"ok": False, "status_code": 500}
    assert watchdog_key("order-api", 1, True, fail) == "order-api:1:500:False"
    assert watchdog_key("order-api", 1, False, fail) == "order-api:1:down"
    assert watchdog_key("order-api", 2, True, fail) != watchdog_key("order-api", 1, True, fail)


def test_ten_bugs_catalog():
    import sys

    sys.path.insert(0, str(REPO_ROOT / "targets" / "order-api"))
    from bugs import catalog

    assert len(catalog()) == 10
    assert Path(REPO_ROOT / "targets" / "order-api" / "app.py").is_file()
    loaded = bug_catalog()
    assert len(loaded) == 10
    assert loaded[0]["id"] == 1
