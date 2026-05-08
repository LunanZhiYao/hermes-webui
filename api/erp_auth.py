"""与 deer-flow 对齐的 ERP workCode 认证实现。

实现目标：
1) 请求参数、签名规则、业务成功判定与 deer-flow 保持一致；
2) 作为 WebUI 的 /api/auth/sso-login 后端依赖，提供统一的 ERP 登录能力。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_ERP_METHOD_GET_USER_BY_WORK_CODE = "Common.CommonSync.GetWxUser"


def _erp_env(name: str) -> str:
    """读取并清理环境变量值（空值统一为 ''）。"""
    return (os.environ.get(name) or "").strip()


def _create_sign(params: dict[str, Any], app_secret: str) -> str:
    """生成 ERP 签名：MD5( app_secret + 排序拼接参数 + app_secret )，并转大写。"""
    sorted_items = sorted(params.items(), key=lambda item: item[0])
    payload = "".join(f"{k}{v}" for k, v in sorted_items)
    raw = f"{app_secret}{payload}{app_secret}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def _joint_params(params: dict[str, Any], method: str, app_id: str, app_secret: str, application_id: str) -> dict[str, Any]:
    """组装 ERP 请求参数并附加 sign。"""
    merged: dict[str, Any] = {
        **params,
        "method": method,
        "app_id": app_id,
        "app_secret": app_secret,
        "nonce": str(int(time.time())),
        "application_id": application_id,
    }
    merged["sign"] = _create_sign(merged, app_secret)
    return merged


def _ensure_erp_config() -> tuple[str, str, str, str, float]:
    """校验 ERP 必需配置。缺失时抛错，交由上层接口返回业务失败。"""
    base_url = _erp_env("ERP_BASE_URL")
    app_id = _erp_env("ERP_APP_ID")
    app_secret = _erp_env("ERP_APP_SECRET")
    application_id = _erp_env("ERP_APPLICATION_ID")
    raw_timeout = _erp_env("ERP_TIMEOUT_SECONDS")
    try:
        timeout_seconds = float(raw_timeout) if raw_timeout else 10.0
    except (TypeError, ValueError):
        logger.warning("Invalid ERP_TIMEOUT_SECONDS=%r — falling back to 10", raw_timeout)
        timeout_seconds = 10.0
    missing = []
    if not base_url:
        missing.append("ERP_BASE_URL")
    if not app_id:
        missing.append("ERP_APP_ID")
    if not app_secret:
        missing.append("ERP_APP_SECRET")
    if not application_id:
        missing.append("ERP_APPLICATION_ID")
    if missing:
        raise RuntimeError(f"ERP config missing: {','.join(missing)}")
    return base_url, app_id, app_secret, application_id, timeout_seconds


def login_by_work_code(work_code: str) -> Any:
    """调用 ERP 根据 workCode 拉取用户信息。

    返回：
      ERP 响应中的 data 字段（通常是用户对象）
    异常：
      - ValueError: 入参不合法
      - RuntimeError: 配置缺失 / 请求失败 / 解析失败 / 业务失败
    """
    if not work_code or not str(work_code).strip():
        raise ValueError("workCode is required")
    base_url, app_id, app_secret, application_id, timeout_seconds = _ensure_erp_config()
    biz_params = {"workCode": str(work_code).strip(), "type": "pc"}
    payload = _joint_params(
        biz_params,
        _ERP_METHOD_GET_USER_BY_WORK_CODE,
        app_id=app_id,
        app_secret=app_secret,
        application_id=application_id,
    )
    body = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(base_url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.exception("ERP request failed: %s", exc)
        raise RuntimeError("ERP请求失败") from exc
    try:
        payload_json = json.loads(raw)
    except Exception as exc:
        logger.exception("ERP response parse failed: %s", raw[:2000])
        raise RuntimeError("ERP响应解析失败") from exc
    if not isinstance(payload_json, dict):
        raise RuntimeError("ERP响应解析失败")

    # 以下成功判定与 deer-flow ``backend/app/services/erp.py`` 对齐，并保留一处兼容：
    # - 与 deer-flow 相同：``ok = bool(status 若存在 else state)``；``not body or code==500 or not ok`` 则失败。
    # - 兼容：响应里若完全没有 ``status`` / ``state`` 字段（仅有 code + data），
    #   deer-flow 单分支会把 ``state`` 缺省当成 ``bool(None)→False`` 误杀成功响应；
    #   此时改用 ``data is not None`` 作为成功依据。
    body = payload_json
    logger.info(
        "ERP login_by_work_code parsed. code=%s has_status=%s has_state=%s has_data=%s",
        body.get("code"),
        "status" in body,
        "state" in body,
        body.get("data") is not None,
    )

    if not body or body.get("code") == 500 or body.get("code") == "500":
        logger.error("ERP login_by_work_code business error (code500 or empty). body=%s", str(body)[:2000])
        raise RuntimeError("ERP认证失败")

    if "status" in body:
        ok = bool(body.get("status"))
    elif "state" in body:
        ok = bool(body.get("state"))
    else:
        ok = body.get("data") is not None

    if not ok:
        logger.error(
            "ERP login_by_work_code business error (ok=False). msg=%s body=%s",
            body.get("msg"),
            str(body)[:2000],
        )
        raise RuntimeError("ERP认证失败")

    # deer-flow ``sso_login``：``if not user_info`` 则失败；且下游按 dict 取 userid。
    result = _normalize_erp_user_payload(body.get("data"))
    if not result or not isinstance(result, dict):
        logger.error("ERP login_by_work_code: ok but user payload missing/invalid. body=%s", str(body)[:2000])
        raise RuntimeError("ERP认证失败")
    return result


def _normalize_erp_user_payload(data: Any) -> Any:
    """把 ERP 的 data 规整成可供上层取 userid 的结构。

    常见变体：data 为单用户 dict、或含单个元素的 list、或包一层 ``{"user": {...}}``。
    """
    if data is None:
        return None
    if isinstance(data, dict):
        nested = data.get("user")
        if isinstance(nested, dict) and (
            nested.get("userid") or nested.get("userId") or nested.get("workCode")
        ):
            return nested
        return data
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                return item
        return None
    return data
