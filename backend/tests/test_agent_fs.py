from app.services.agent_runtime import fs_changed_event, path_from_tool_args


def test_path_from_write_args():
    assert path_from_tool_args("write_file", '{"path": "docs\\\\a.md", "content": "x"}') == "docs/a.md"
    assert path_from_tool_args("delete_file", '{"path": "./src/app.py"}') == "src/app.py"
    assert path_from_tool_args("bash", '{"command": "rm x"}') == ""


def test_fs_changed_event_ops():
    ev = fs_changed_event("write_file", '{"path": "n.txt"}')
    assert ev["type"] == "fs_changed"
    assert ev["op"] == "write"
    assert ev["path"] == "n.txt"
    assert fs_changed_event("delete_file", '{"path": "n.txt"}')["op"] == "delete"
    assert fs_changed_event("bash", '{"command": "touch a"}')["op"] == "bash"
