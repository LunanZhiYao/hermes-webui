"""租户路径推导工具。

与方案文档对齐：
- Hermes 租户根目录：<base_home>/users/<user_key>/
- WebUI 租户状态目录：<state_root>/users/<user_key>/
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from api.profiles import _resolve_base_hermes_home, _strip_trailing_saas_tenant_key_dirs

_SAAS_USERS_DIR = "users"

_RESERVED_TOP_LEVEL = {
    "profiles",
    "memories",
    "sessions",
    "workspace",
    "cron",
    "skills",
    "logs",
    "plans",
    "skins",
    "plugins",
    "webui",
}


def user_key_from_user_id(user_id: str) -> str:
    """把用户标识稳定映射成目录安全的 user_key。

    规则：
    - 使用 SHA256 截断，确保稳定且不泄漏原始 user_id；
    - 统一加 'u' 前缀，避免与已有顶层目录名冲突；
    - 拒绝空值与保留名碰撞。
    """
    raw = str(user_id or "").strip()
    if not raw:
        raise ValueError("user_id is required")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    key = f"u{digest}"
    if key in _RESERVED_TOP_LEVEL:
        raise ValueError("derived user key collides with reserved name")
    return key


def tenant_hermes_home(user_key: str) -> Path:
    """返回租户 Hermes HOME：<base_home>/users/<user_key>。"""
    base_home = _resolve_base_hermes_home().expanduser().resolve()
    return (base_home / _SAAS_USERS_DIR / user_key).resolve()


def tenant_webui_state_dir(user_key: str) -> Path:
    """返回租户 WebUI 状态目录。

    优先使用 HERMES_WEBUI_STATE_ROOT；未设置时默认 <base_home>/webui。
    """
    root = os.getenv("HERMES_WEBUI_STATE_ROOT", "").strip()
    if root:
        state_root = _strip_trailing_saas_tenant_key_dirs(Path(root).expanduser()).resolve()
    else:
        state_root = (_resolve_base_hermes_home().expanduser().resolve() / "webui").resolve()
    return (state_root / _SAAS_USERS_DIR / user_key).resolve()
