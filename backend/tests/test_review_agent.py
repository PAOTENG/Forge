from unittest.mock import patch

from app.services.agent_runtime import INSTRUCTIONS, build_agent
from app.tools import all_tools, readonly_review_tools


def _tool_name(tool) -> str:
    return str(getattr(tool, "name", "") or "")


def test_reviewer_tools_are_read_only():
    names = {_tool_name(t) for t in readonly_review_tools()}
    assert names == {
        "read_file",
        "list_dir",
        "grep_files",
        "glob_files",
        "git_status",
        "git_diff",
    }
    mutating = {
        "write_file",
        "delete_file",
        "bash",
        "git_commit",
        "git_push",
        "run_tests",
        "restart_service",
    }
    assert names.isdisjoint(mutating)


def test_build_agent_attaches_review_hotfix():
    with patch("app.services.agent_runtime.resolve_model", return_value="gpt-4.1"):
        agent = build_agent("build")
    names = [_tool_name(t) for t in agent.tools]
    assert "review_hotfix" in names
    for tool in all_tools():
        assert _tool_name(tool) in names
    assert "review_hotfix" in INSTRUCTIONS
    assert agent.name == "Forge"
