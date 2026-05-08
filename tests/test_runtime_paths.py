import json
from pathlib import Path

from api.runtime_paths import get_session_dir, get_settings_path, get_state_dir
from api.tenant_context import clear_tenant, set_tenant

def test_runtime_paths_without_tenant_uses_default(monkeypatch):
    monkeypatch.delenv("HERMES_WEBUI_SAAS", raising=False)
    clear_tenant()
    state = get_state_dir()
    assert "webui" in str(state)


def test_runtime_paths_with_tenant_uses_tenant_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_WEBUI_SAAS", "1")
    tenant_state = tmp_path / "tenant_state"
    set_tenant(
        user_id="u1",
        user_key="u123",
        tenant_hermes_home=tmp_path / "tenant_home",
        tenant_webui_state_dir=tenant_state,
    )
    try:
        assert get_state_dir() == tenant_state
        assert get_session_dir() == tenant_state / "sessions"
        assert get_settings_path() == tenant_state / "settings.json"
    finally:
        clear_tenant()


def test_save_settings_writes_tenant_file_when_saas_tenant_bound(monkeypatch, tmp_path: Path):
    """SaaS 下界面设置应落在 tenant_webui_state_dir，而不是全局 STATE_DIR。"""
    from api import config

    monkeypatch.setenv("HERMES_WEBUI_SAAS", "1")
    tenant_state = tmp_path / "tenant_state"
    set_tenant(
        user_id="u1",
        user_key="u123",
        tenant_hermes_home=tmp_path / "tenant_home",
        tenant_webui_state_dir=tenant_state,
    )
    try:
        assert config._active_ui_settings_path() == tenant_state / "settings.json"
        tenant_state.mkdir(parents=True, exist_ok=True)
        config.save_settings({"language": "zh"})
        tf = tenant_state / "settings.json"
        assert tf.is_file()
        data = json.loads(tf.read_text(encoding="utf-8"))
        assert data.get("language") == "zh"
    finally:
        clear_tenant()
