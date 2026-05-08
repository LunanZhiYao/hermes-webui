"""租户目录初始化。

首访租户时创建最小目录骨架，避免后续会话/技能/cron 等模块写盘失败。
该行为与 profiles.py 里的 profile 目录布局保持一致。
"""
from __future__ import annotations

from pathlib import Path

_PROFILE_DIRS = [
    "memories",
    "sessions",
    "skills",
    "skins",
    "logs",
    "plans",
    "workspace",
    "cron",
]


def ensure_tenant_layout(tenant_home: Path) -> None:
    """确保租户 HOME 下关键目录存在。"""
    home = Path(tenant_home).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    for subdir in _PROFILE_DIRS:
        (home / subdir).mkdir(parents=True, exist_ok=True)
    (home / "state.db").touch(exist_ok=True)
