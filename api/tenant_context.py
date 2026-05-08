"""SaaS 模式下的请求级租户上下文。

设计目的：
1) WebUI 是多线程 HTTP 服务，同一个进程会复用线程处理不同用户请求；
2) 多租户隔离需要“当前请求是谁”的上下文；
3) 该上下文不能放在进程全局变量，否则会串租户。

因此这里使用 threading.local() 保存请求级租户信息，并在 server.py 的
请求 finally 中清理，保证线程复用时不会继承上一次请求的租户数据。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

_tls = threading.local()


def saas_enabled() -> bool:
    """统一判断是否开启 SaaS 多租户模式。"""
    return os.getenv("HERMES_WEBUI_SAAS", "").strip().lower() in {"1", "true", "yes", "on"}


def set_tenant(*, user_id: str, user_key: str, tenant_hermes_home: Path, tenant_webui_state_dir: Path) -> None:
    """写入当前线程的租户上下文。

    这里会把路径 canonicalize（expanduser + resolve），避免后续模块
    用到相同目录时因路径表现形式不同（相对/绝对、~）导致比较或日志混乱。
    """
    _tls.user_id = user_id
    _tls.user_key = user_key
    _tls.tenant_hermes_home = str(Path(tenant_hermes_home).expanduser().resolve())
    _tls.tenant_webui_state_dir = str(Path(tenant_webui_state_dir).expanduser().resolve())


def clear_tenant() -> None:
    """清理当前线程的租户上下文（必须在请求 finally 调用）。"""
    _tls.user_id = None
    _tls.user_key = None
    _tls.tenant_hermes_home = None
    _tls.tenant_webui_state_dir = None


def get_tenant_user_id() -> str | None:
    return getattr(_tls, "user_id", None)


def get_tenant_user_key() -> str | None:
    return getattr(_tls, "user_key", None)


def get_tenant_hermes_home() -> Path | None:
    value = getattr(_tls, "tenant_hermes_home", None)
    return Path(value) if value else None


def get_tenant_webui_state_dir() -> Path | None:
    value = getattr(_tls, "tenant_webui_state_dir", None)
    return Path(value) if value else None


def snapshot_tenant_context() -> dict | None:
    """供 HTTP 线程在启动 ``threading.Thread`` 流式工作线程前调用。

    ``threading.local`` 不会被子线程继承；若不把租户上下文拷过去，
    ``api.runtime_paths.get_session_dir()`` 会回落到全局 ``STATE_DIR``，
    SaaS 模式下会话 JSON 会误写入全局目录。
    """
    if not saas_enabled():
        return None
    w = get_tenant_webui_state_dir()
    h = get_tenant_hermes_home()
    uk = get_tenant_user_key()
    if w is None or h is None or not uk:
        return None
    return {
        "user_id": get_tenant_user_id() or "",
        "user_key": uk,
        "tenant_hermes_home": h,
        "tenant_webui_state_dir": w,
    }
