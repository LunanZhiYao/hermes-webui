"""运行时路径解析（支持 SaaS 租户动态切换）。

为什么要有这一层：
- 历史代码大量直接引用 config.py 的全局 STATE_DIR/SESSION_DIR；
- 多租户下这些路径必须按请求动态变化；
- 通过 runtime_paths 收口后，业务代码只需改调用点，不改原始配置常量。
"""
from __future__ import annotations

from pathlib import Path

from api.config import PROJECTS_FILE, SESSION_DIR, SESSION_INDEX_FILE, SETTINGS_FILE, STATE_DIR
from api.tenant_context import get_tenant_webui_state_dir, saas_enabled


def get_state_dir() -> Path:
    """返回当前请求应使用的 WebUI 状态根目录。"""
    if saas_enabled():
        tenant_dir = get_tenant_webui_state_dir()
        if tenant_dir is not None:
            return tenant_dir
    return STATE_DIR


def get_session_dir() -> Path:
    return get_state_dir() / "sessions"


def get_session_index_path() -> Path:
    return get_session_dir() / "_index.json"


def get_projects_file() -> Path:
    if saas_enabled():
        return get_state_dir() / "projects.json"
    return PROJECTS_FILE


def get_settings_path() -> Path:
    """界面 settings.json：SaaS 已绑定租户时用租户目录；否则用 SETTINGS_FILE（便于测试只 patch 该常量）。"""
    if saas_enabled():
        tenant_dir = get_tenant_webui_state_dir()
        if tenant_dir is not None:
            return tenant_dir / "settings.json"
    return SETTINGS_FILE
