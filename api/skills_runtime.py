"""Per-request pinning of agent ``tools.skills_tool`` paths for multi-tenant WebUI.

``skills_tool`` snapshots ``HERMES_HOME`` / ``SKILLS_DIR`` at import time; HTTP
handlers must repoint them to :func:`api.profiles.get_active_hermes_home` so
SaaS tenants read/write only their own ``<tenant>/skills`` while
``skills.external_dirs`` in shared ``config.yaml`` still supplies global skills
(agent-side behaviour, unchanged).

Bundled skills (the ``skills/`` tree shipped with hermes-agent) are merged into
``GET /api/skills`` so isolated profiles with an empty personal ``skills/``
folder still see defaults in the Web UI.
"""
from __future__ import annotations

import json
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


def hermes_agent_repo_root() -> Path | None:
    """Return the hermes-agent checkout root if ``skills/`` exists beside ``tools/``."""
    try:
        import tools.skills_tool as sk

        root = Path(sk.__file__).resolve().parent.parent
        if (root / "skills").is_dir():
            return root
    except Exception:
        pass
    return None


def skills_list_metadata_for_home(home: Path) -> list[dict]:
    """Return skill metadata dicts for ``<home>/skills`` via :func:`skills_list`."""
    from tools.skills_tool import skills_list as _skills_list

    with skills_tool_paths_for_home(Path(home).expanduser().resolve()):
        raw = _skills_list()
    data = json.loads(raw) if isinstance(raw, str) else raw
    return list(data.get("skills") or [])


def build_skills_api_response(active_home: Path) -> dict[str, object]:
    """JSON payload for ``GET /api/skills``: personal dir, bundled repo tree, merged list."""
    home = Path(active_home).expanduser().resolve()
    personal = skills_list_metadata_for_home(home)
    bundled: list[dict] = []
    agent_root = hermes_agent_repo_root()
    if agent_root:
        bundled = skills_list_metadata_for_home(agent_root)

    personal_names = {s["name"] for s in personal}
    merged = list(personal)
    for s in bundled:
        if s["name"] not in personal_names:
            merged.append(s)
    merged.sort(key=lambda x: (x.get("category") or "", x["name"]))

    return {
        "skills": merged,
        "bundled_skills": bundled,
        "personal_skills": personal,
    }
