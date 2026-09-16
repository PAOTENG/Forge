from app.services.agent_runtime import SessionRuntime, is_active, run_status, runtimes


def test_unknown_session_is_idle():
    runtimes.pop("ghost", None)
    assert is_active("ghost") is False
    assert run_status("ghost") == "idle"


def test_running_and_approval_are_active():
    rt = SessionRuntime(status="running")
    runtimes["s1"] = rt
    try:
        assert is_active("s1") is True
        assert run_status("s1") == "running"
        rt.status = "approval"
        assert is_active("s1") is True
        rt.status = "done"
        assert is_active("s1") is False
    finally:
        runtimes.pop("s1", None)
