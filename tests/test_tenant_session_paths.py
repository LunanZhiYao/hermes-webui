"""SaaS: 会话文件应落在租户 WebUI state 下，后台线程无 TLS 时也能写对路径。"""
from pathlib import Path

from api.models import Session
from api.tenant_context import clear_tenant, set_tenant


def test_session_path_uses_state_stamp_after_tls_cleared(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_WEBUI_SAAS", "1")
    tenant_state = tmp_path / "u1"
    (tenant_state / "sessions").mkdir(parents=True)
    set_tenant(
        user_id="a",
        user_key="uk1",
        tenant_hermes_home=tmp_path / "hermes_u1",
        tenant_webui_state_dir=tenant_state,
    )
    try:
        sid = "abc123"
        p = tenant_state / "sessions" / f"{sid}.json"
        p.write_text(
            '{"session_id":"abc123","title":"t","created_at":1,"updated_at":1,'
            '"messages":[],"tool_calls":[]}',
            encoding="utf-8",
        )
        s = Session.load(sid)
        assert s is not None
        clear_tenant()
        assert s.path.resolve() == p.resolve()
    finally:
        clear_tenant()


def test_worker_thread_save_respects_tenant_snapshot(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_WEBUI_SAAS", "1")
    tenant_state = tmp_path / "state_u1"
    tenant_home = tmp_path / "home_u1"
    tenant_state.mkdir(parents=True)
    (tenant_state / "sessions").mkdir(parents=True)

    set_tenant(
        user_id="user-one",
        user_key="u111",
        tenant_hermes_home=tenant_home,
        tenant_webui_state_dir=tenant_state,
    )
    try:
        sid = "sessworker01"
        s = Session(
            session_id=sid,
            title="w",
            workspace=str(tmp_path / "ws"),
            model="m",
            messages=[{"role": "user", "content": "hi", "timestamp": 1}],
            tool_calls=[],
            created_at=1.0,
            updated_at=1.0,
        )
        s.save()
        expected = tenant_state / "sessions" / f"{sid}.json"
        assert expected.exists()

        clear_tenant()

        def _bg_save():
            from api.tenant_context import clear_tenant as _ct, set_tenant as _st

            _st(
                user_id="user-one",
                user_key="u111",
                tenant_hermes_home=tenant_home,
                tenant_webui_state_dir=tenant_state,
            )
            try:
                s2 = Session.load(sid)
                assert s2 is not None
                s2.messages.append(
                    {"role": "assistant", "content": "yo", "timestamp": 2}
                )
                s2.save()
            finally:
                _ct()

        import threading

        th = threading.Thread(target=_bg_save, daemon=True)
        th.start()
        th.join(timeout=5)
        assert not th.is_alive()
        data = expected.read_text(encoding="utf-8")
        assert "yo" in data
    finally:
        clear_tenant()
