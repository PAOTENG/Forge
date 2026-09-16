from app.services.llm_catalog import provider_of
from app.services.llm_prefs import preferred_model, save_preferred_model


def test_provider_of_new_vendors():
    assert provider_of("zhipuai/glm-4.5") == "glm"
    assert provider_of("dashscope/qwen-plus") == "qwen"
    assert provider_of("xai/grok-4") == "grok"
    assert provider_of("cursor/grok-4") == "grok"
    assert provider_of("deepseek/deepseek-chat") == "deepseek"


def test_preferred_model_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "preferred_model.txt"
    monkeypatch.setattr("app.services.llm_prefs.PREF_PATH", path)
    assert preferred_model() == ""
    assert save_preferred_model("dashscope/qwen-plus") == "dashscope/qwen-plus"
    assert preferred_model() == "dashscope/qwen-plus"
    assert path.read_text(encoding="utf-8").strip() == "dashscope/qwen-plus"
