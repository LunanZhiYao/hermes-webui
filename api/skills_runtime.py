"""Per-request pinning of agent ``tools.skills_tool`` paths for multi-tenant WebUI.

``skills_tool`` snapshots ``HERMES_HOME`` / ``SKILLS_DIR`` at import time; HTTP
handlers must repoint them to :func:`api.profiles.get_active_hermes_home` so
SaaS tenants read/write only their own ``<tenant>/skills`` while
``skills.external_dirs`` in shared ``config.yaml`` still supplies global skills
(agent-side behaviour, unchanged).
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_skills_tool_patch_lock = threading.Lock()


@contextmanager
def skills_tool_paths_for_home(home: Path) -> Iterator[None]:
    """Serialize patches to ``tools.skills_tool`` / ``skill_manager_tool`` module globals."""
    home = Path(home).expanduser().resolve()
    _skills_tool_patch_lock.acquire()
    try:
        try:
            import tools.skills_tool as sk
        except ImportError:
            yield
            return

        prev_sk = (getattr(sk, "HERMES_HOME", None), getattr(sk, "SKILLS_DIR", None))
        sk.HERMES_HOME = home
        sk.SKILLS_DIR = home / "skills"

        sm = None
        sm_prev = None
        try:
            import tools.skill_manager_tool as sm
            sm_prev = (getattr(sm, "HERMES_HOME", None), getattr(sm, "SKILLS_DIR", None))
            sm.HERMES_HOME = home
            sm.SKILLS_DIR = home / "skills"
        except ImportError:
            pass

        try:
            yield
        finally:
            sk.HERMES_HOME, sk.SKILLS_DIR = prev_sk
            if sm_prev is not None and sm is not None:
                sm.HERMES_HOME, sm.SKILLS_DIR = sm_prev
    finally:
        _skills_tool_patch_lock.release()
