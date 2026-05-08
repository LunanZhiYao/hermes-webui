from pathlib import Path

from api.tenant_paths import tenant_hermes_home, tenant_webui_state_dir, user_key_from_user_id


def test_user_key_is_stable():
    assert user_key_from_user_id("alice") == user_key_from_user_id("alice")


def test_user_key_prefix_and_length():
    key = user_key_from_user_id("tenant-user")
    assert key.startswith("u")
    assert len(key) == 25


def test_tenant_hermes_home_no_double_user_key_when_hermes_home_is_tenant_dir(
    monkeypatch, tmp_path: Path
):
    """HERMES_HOME 误设为 ~/.hermes/<user_key> 时，不应再拼出一层 uXXX/uXXX。"""
    uk = user_key_from_user_id("same-user")
    fake_base = tmp_path / ".hermes"
    fake_base.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(fake_base / uk))
    monkeypatch.delenv("HERMES_BASE_HOME", raising=False)
    assert tenant_hermes_home(uk).resolve() == (fake_base / "users" / uk).resolve()


def test_tenant_webui_state_no_double_when_state_root_ends_with_user_key(
    monkeypatch, tmp_path: Path
):
    """HERMES_WEBUI_STATE_ROOT 误设为 .../webui/<user_key> 时不应套娃。"""
    uk = user_key_from_user_id("tenant-x")
    webui_root = tmp_path / "webui"
    monkeypatch.setenv("HERMES_WEBUI_STATE_ROOT", str(webui_root / uk))
    monkeypatch.delenv("HERMES_BASE_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert tenant_webui_state_dir(uk).resolve() == (webui_root / "users" / uk).resolve()


def test_tenant_hermes_home_when_hermes_home_is_users_segment(monkeypatch, tmp_path: Path):
    """HERMES_HOME 设为 .../users/<user_key> 时应正确归一化 base，避免 users/users 套娃。"""
    uk = user_key_from_user_id("path-with-users-seg")
    fake_base = tmp_path / ".hermes"
    fake_base.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(fake_base / "users" / uk))
    monkeypatch.delenv("HERMES_BASE_HOME", raising=False)
    assert tenant_hermes_home(uk).resolve() == (fake_base / "users" / uk).resolve()


def test_tenant_webui_state_when_root_ends_with_users_and_user_key(monkeypatch, tmp_path: Path):
    """HERMES_WEBUI_STATE_ROOT 为 .../webui/users/<user_key> 时不应再多一层 users。"""
    uk = user_key_from_user_id("webui-users-suffix")
    webui_root = tmp_path / "webui"
    monkeypatch.setenv("HERMES_WEBUI_STATE_ROOT", str(webui_root / "users" / uk))
    monkeypatch.delenv("HERMES_BASE_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert tenant_webui_state_dir(uk).resolve() == (webui_root / "users" / uk).resolve()


def test_strip_handles_alternating_users_and_user_key_suffix(monkeypatch, tmp_path: Path):
    """错误配置 .../users/<uk_a>/users/<uk_b> 时应剥净后再拼 users/<uk_b>，避免残留中间 uk。"""
    uk_a = user_key_from_user_id("alt-user-a")
    uk_b = user_key_from_user_id("alt-user-b")
    fake_base = tmp_path / ".hermes"
    fake_base.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(fake_base / "users" / uk_a / "users" / uk_b))
    monkeypatch.delenv("HERMES_BASE_HOME", raising=False)
    assert tenant_hermes_home(uk_b).resolve() == (fake_base / "users" / uk_b).resolve()
