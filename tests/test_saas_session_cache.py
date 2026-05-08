"""SaaS: shared SESSIONS LRU must not leak rows across tenants."""

from pathlib import Path

import pytest

from api.config import SESSIONS
from api.tenant_context import clear_tenant, set_tenant


@pytest.fixture(autouse=True)
def _clear_tenant():
    clear_tenant()
    yield
    clear_tenant()


def test_all_sessions_skips_other_tenant_memory_rows(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_WEBUI_SAAS", "1")
    from api import models

    a_state = tmp_path / "ta"
    b_state = tmp_path / "tb"
    (a_state / "sessions").mkdir(parents=True)
    (b_state / "sessions").mkdir(parents=True)

    set_tenant(
        user_id="user-a",
        user_key="uka",
        tenant_hermes_home=tmp_path / "ha",
        tenant_webui_state_dir=a_state,
    )
    sa = models.new_session()
    sid_a = sa.session_id
    clear_tenant()

    set_tenant(
        user_id="user-b",
        user_key="ukb",
        tenant_hermes_home=tmp_path / "hb",
        tenant_webui_state_dir=b_state,
    )
    try:
        ids = {s["session_id"] for s in models.all_sessions()}
        assert sid_a not in ids
    finally:
        clear_tenant()


def test_get_session_ignores_foreign_cache_hit(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_WEBUI_SAAS", "1")
    from api import models

    a_state = tmp_path / "ta"
    b_state = tmp_path / "tb"
    (a_state / "sessions").mkdir(parents=True)
    (b_state / "sessions").mkdir(parents=True)

    set_tenant(
        user_id="user-a",
        user_key="uka",
        tenant_hermes_home=tmp_path / "ha",
        tenant_webui_state_dir=a_state,
    )
    sa = models.new_session()
    sid_a = sa.session_id
    clear_tenant()

    set_tenant(
        user_id="user-b",
        user_key="ukb",
        tenant_hermes_home=tmp_path / "hb",
        tenant_webui_state_dir=b_state,
    )
    try:
        assert sid_a in SESSIONS
        with pytest.raises(KeyError):
            models.get_session(sid_a)
        assert sid_a in SESSIONS
    finally:
        clear_tenant()
