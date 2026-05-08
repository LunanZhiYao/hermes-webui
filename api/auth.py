"""
Hermes Web UI -- Optional password authentication.
Off by default. Enable by setting HERMES_WEBUI_PASSWORD env var
or configuring a password in the Settings panel.
"""
import hashlib
import hmac
import http.cookies
import json
import logging
import os
import secrets
import tempfile
import time
import base64
from typing import Any

from urllib.parse import parse_qs, quote, unquote, urlencode

from api.config import STATE_DIR, load_settings

logger = logging.getLogger(__name__)

# ── Public paths (no auth required) ─────────────────────────────────────────
PUBLIC_PATHS = frozenset({
    '/login', '/health', '/favicon.ico',
    '/api/auth/login', '/api/auth/status', '/api/auth/sso-login', '/api/auth/health',
    '/manifest.json', '/manifest.webmanifest',
})

COOKIE_NAME = 'hermes_session'
# 注意：Cookie 只按域名隔离，不按端口隔离。
# 如果这里沿用 deerflow_* 命名，不同项目（同域不同端口）会互相串登录态。
# 因此 WebUI 必须使用独立的 cookie key。
WEBUI_SSO_AUTH_COOKIE = "hermes_webui_auth"
WEBUI_SSO_USER_ID_COOKIE = "hermes_webui_user_id"
WEBUI_SSO_USER_NAME_COOKIE = "hermes_webui_user_name"
SESSION_TTL = 86400 * 30  # 30 days

_SESSIONS_FILE = STATE_DIR / '.sessions.json'


def _load_sessions() -> dict[str, float]:
    """Load persisted sessions from STATE_DIR, pruning expired entries.

    Returns an empty dict on any read or parse error so startup is never
    blocked by a corrupt or missing sessions file.
    """
    try:
        if _SESSIONS_FILE.exists():
            data = json.loads(_SESSIONS_FILE.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('malformed sessions file — expected dict')
            now = time.time()
            return {t: exp for t, exp in data.items()
                    if isinstance(t, str) and isinstance(exp, (int, float)) and exp > now}
    except Exception as e:
        logger.debug("Failed to load sessions file, starting fresh: %s", e)
    return {}


def _save_sessions(sessions: dict[str, float]) -> None:
    """Atomically persist sessions to STATE_DIR/.sessions.json (0600).

    Uses a temp file + os.replace() so a crash mid-write never leaves a
    truncated file.  Mirrors the same pattern as .signing_key persistence.
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=STATE_DIR, suffix='.sessions.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(sessions, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, _SESSIONS_FILE)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.debug("Failed to persist sessions: %s", e)


# Active sessions: token -> expiry timestamp (persisted across restarts via STATE_DIR)
_sessions = _load_sessions()

# ── Login rate limiter ──────────────────────────────────────────────────────
_login_attempts = {}  # ip -> [timestamp, ...]
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW = 60  # seconds

def _check_login_rate(ip: str) -> bool:
    """Return True if the IP is allowed to attempt login."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Prune old attempts
    attempts = [t for t in attempts if now - t < _LOGIN_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) < _LOGIN_MAX_ATTEMPTS

def _record_login_attempt(ip: str) -> None:
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    attempts.append(now)
    _login_attempts[ip] = attempts


def _signing_key():
    """Return a random signing key, generating and persisting one on first call."""
    key_file = STATE_DIR / '.signing_key'
    try:
        if key_file.exists():
            raw = key_file.read_bytes()
            if len(raw) >= 32:
                return raw[:32]
    except Exception:
        logger.debug("Failed to read or access signing key file, using in-memory key")
    # Generate a new random key
    key = secrets.token_bytes(32)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(key)
        key_file.chmod(0o600)
    except Exception:
        logger.debug("Failed to persist signing key, using in-memory key only")
    return key


def _hash_password(password):
    """PBKDF2-SHA256 with 600k iterations (OWASP recommendation).
    Salt is the persisted random signing key, which is secret and unique per
    installation. This keeps the stored hash format a plain hex string
    (no format change to settings.json) while replacing the predictable
    STATE_DIR-derived salt from the original implementation."""
    salt = _signing_key()
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000)
    return dk.hex()


def get_password_hash() -> str | None:
    """Return the active password hash, or None if auth is disabled.
    Priority: env var > settings.json."""
    env_pw = os.getenv('HERMES_WEBUI_PASSWORD', '').strip()
    if env_pw:
        return _hash_password(env_pw)
    settings = load_settings()
    return settings.get('password_hash') or None


def is_auth_enabled() -> bool:
    """True if a password is configured (env var or settings)."""
    return get_password_hash() is not None


def verify_password(plain) -> bool:
    """Verify a plaintext password against the stored hash."""
    expected = get_password_hash()
    if not expected:
        return False
    return hmac.compare_digest(_hash_password(plain), expected)


def create_session() -> str:
    """Create a new auth session. Returns signed cookie value."""
    token = secrets.token_hex(32)
    _sessions[token] = time.time() + SESSION_TTL
    _save_sessions(_sessions)
    sig = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{token}.{sig}"


def _prune_expired_sessions():
    """Remove all expired session entries to prevent unbounded memory growth."""
    now = time.time()
    expired = [t for t, exp in _sessions.items() if now > exp]
    if expired:
        for token in expired:
            _sessions.pop(token, None)
        _save_sessions(_sessions)


def verify_session(cookie_value) -> bool:
    """Verify a signed session cookie. Returns True if valid and not expired."""
    if not cookie_value or '.' not in cookie_value:
        return False
    _prune_expired_sessions()  # lazy cleanup on every verification attempt
    token, sig = cookie_value.rsplit('.', 1)
    expected_sig = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected_sig):
        return False
    expiry = _sessions.get(token)
    if not expiry or time.time() > expiry:
        _sessions.pop(token, None)
        return False
    return True


def invalidate_session(cookie_value) -> None:
    """Remove a session token."""
    if cookie_value and '.' in cookie_value:
        token = cookie_value.rsplit('.', 1)[0]
        if token in _sessions:
            _sessions.pop(token, None)
            _save_sessions(_sessions)


def _parse_cookie_value(handler, cookie_name: str) -> str | None:
    cookie_header = handler.headers.get('Cookie', '')
    if not cookie_header:
        return None
    cookie = http.cookies.SimpleCookie()
    try:
        cookie.load(cookie_header)
    except http.cookies.CookieError:
        return None
    morsel = cookie.get(cookie_name)
    return morsel.value if morsel else None


def parse_cookie(handler) -> str | None:
    """Extract the webui auth cookie from the request headers."""
    return _parse_cookie_value(handler, COOKIE_NAME)


def _webui_sso_cookie_user_id(handler) -> str | None:
    """读取 WebUI 自有 SSO cookies（不兼容 deerflow_*，避免跨项目串会话）。"""
    auth = (_parse_cookie_value(handler, WEBUI_SSO_AUTH_COOKIE) or "").strip().lower()
    uid = (_parse_cookie_value(handler, WEBUI_SSO_USER_ID_COOKIE) or "").strip()
    if auth == "true" and uid:
        return uid
    return None


_WORKCODE_QUERY_KEYS = ("workCode", "workcode", "WORKCODE", "work_code")


def _header_ci_get(handler, name: str) -> str:
    """读取请求头（大小写不敏感），与 nginx / 反向代理常见写法对齐。

    deer-flow Next middleware 使用 ``headers.get(\"workCode\")``（Fetch API 大小写不敏感）；
    Python ``Message.get(\"workCode\")`` 对任意大小写混写不一定命中，这里统一折叠比较。
    """
    want = name.lower().strip()
    if not want:
        return ""
    try:
        items = handler.headers.items()
    except Exception:
        return ""
    for key, value in items:
        if key.lower() == want:
            return (value or "").strip()
    return ""


def _redact_work_code_for_log(token: str) -> str:
    """仅记录长度与首尾少量字符，避免完整 ticket 进入日志。"""
    if not token:
        return "(empty)"
    n = len(token)
    if n <= 12:
        return f"len={n}"
    t = token.replace("\r", "").replace("\n", "")
    return f"len={n} head={t[:6]!r} tail={t[-4]!r}"


def _parse_request_qs(raw: str) -> dict[str, list[str]]:
    try:
        return parse_qs(raw or "", keep_blank_values=False)
    except Exception as exc:
        logger.warning(
            "workCode related: parse_qs failed query_prefix=%r err=%s",
            (raw or "")[:200],
            exc,
        )
        return {}


def extract_work_code_with_meta(handler, qs: dict[str, list[str]]) -> tuple[str, dict[str, Any]]:
    """解析 workCode，并返回可用于观测的元数据（不写完整明文）。

    与 deer-flow ``frontend/src/middleware.ts`` 一致：**优先请求头，再查询参数**
    （``headers.get(\"workCode\") ?? url.searchParams.get(\"workCode\")``）。
    原先先解析 query 会导致网关注入的头与 URL 参数不一致时与 deer-flow 行为相反。
    """
    meta: dict[str, Any] = {
        "source": "none",
        "unquote_rounds": 0,
        "qs_keys_present": [k for k in _WORKCODE_QUERY_KEYS if k in qs],
    }
    raw = ""
    found_key: str | None = None

    h1 = _header_ci_get(handler, "workCode")
    if h1:
        raw = h1
        meta["source"] = "header:workCode"
    else:
        h2 = _header_ci_get(handler, "X-Work-Code")
        if h2:
            raw = h2
            meta["source"] = "header:X-Work-Code"

    if not raw:
        for key in _WORKCODE_QUERY_KEYS:
            vals = qs.get(key)
            if not vals:
                continue
            for item in vals:
                chunk = (item or "").strip()
                if chunk:
                    raw = chunk
                    found_key = key
                    break
            if raw:
                break
        if raw:
            meta["source"] = f"query:{found_key}"

    if not raw:
        return "", meta

    meta["unquote_rounds"] = 1
    wc = unquote(raw).strip()
    if "%" in wc:
        wc = unquote(wc).strip()
        meta["unquote_rounds"] = 2
    return wc, meta


def log_work_code_parse_result(
    *,
    where: str,
    path: str,
    work_code: str,
    meta: dict[str, Any],
    extra: str = "",
) -> None:
    """统一输出 workCode 解析日志（脱敏）。"""
    qkp = meta.get("qs_keys_present") or []
    sk = ",".join(qkp) if qkp else "-"
    tail = f" {extra}" if extra else ""
    logger.info(
        "workCode parse [%s] path=%s source=%s unquote_rounds=%s qs_keys=%s preview=%s%s",
        where,
        path,
        meta.get("source"),
        meta.get("unquote_rounds"),
        sk,
        _redact_work_code_for_log(work_code),
        tail,
    )


def extract_work_code(handler, qs: dict[str, list[str]]) -> str:
    """从请求头与查询串提取 ERP workCode（与 deer-flow middleware 行为对齐）。

    - **顺序**：``workCode`` 请求头 → ``X-Work-Code`` → 查询参数别名；
    - 请求头名称 **大小写不敏感**（对齐 Fetch / nginx 行为）；
    - 兼容查询键 ``workcode``、``work_code`` 等；
    - 对双重 ``application/x-www-form-urlencoded`` 编码做二次 ``unquote``；
    - 同一键重复出现时（如 ``workCode=&workCode=真实值``）取第一个非空值。
    """
    wc, _ = extract_work_code_with_meta(handler, qs)
    return wc


def qs_without_work_code(qs: dict[str, list[str]]) -> dict[str, list[str]]:
    clean = dict(qs)
    for key in _WORKCODE_QUERY_KEYS:
        clean.pop(key, None)
    return clean


def check_auth(handler, parsed) -> bool:
    """Check if request is authorized. Returns True if OK.
    If not authorized, sends 401 (API) or 302 redirect (page) and returns False."""
    # 认证策略说明：
    # - 非 SaaS 且未启用密码：保持历史行为（放行）。
    # - SaaS 模式：强制要求“可识别用户身份”（Bearer / X-User-ID / deerflow cookies）。
    #   这样重启后不会因为无密码模式而直接进入会话，符合多租户隔离预期。
    saas_mode = os.getenv("HERMES_WEBUI_SAAS", "").strip().lower() in {"1", "true", "yes", "on"}
    if not is_auth_enabled() and not saas_mode:
        return True

    # SaaS + 页面 URL 携带 workCode：
    # 1) 必须优先于 PUBLIC_PATHS。deer-flow 场景里入口常为 ``/login?workCode=``；若先放行
    #    ``/login``，则永远不会执行 ERP，用户只会看到「无效 workCode」或反复未登录。
    # 2) 必须优先于 Cookie/Bearer，否则换号链接无法覆盖浏览器里上一用户的 cookie。
    # 成功后 Set-Cookie 并 302 到去掉 workCode 的同一路径（静态资源除外）。
    if saas_mode:
        qs = _parse_request_qs(parsed.query or "")
        work_code, wc_meta = extract_work_code_with_meta(handler, qs)
        _static_like = parsed.path.startswith("/static/") or parsed.path.startswith("/session/static/")
        _try_erp = bool(work_code and not parsed.path.startswith("/api/") and not _static_like)
        log_work_code_parse_result(
            where="check_auth",
            path=parsed.path,
            work_code=work_code,
            meta=wc_meta,
            extra=f"try_erp={_try_erp} query_len={len(parsed.query or '')}",
        )
        qkp = wc_meta.get("qs_keys_present") or []
        if not work_code and qkp:
            logger.warning(
                "workCode query param keys present %s but every value empty after strip/unquote",
                qkp,
            )
        if work_code and not parsed.path.startswith("/api/") and not _static_like:
            try:
                from api.erp_auth import login_by_work_code

                user_info = login_by_work_code(work_code)
                user_id = str(
                    (user_info or {}).get("userid")
                    or (user_info or {}).get("userId")
                    or (user_info or {}).get("workCode")
                    or work_code
                ).strip()
                if user_id:
                    user_name = (user_info or {}).get("name") or user_id
                    handler.send_response(302)
                    set_webui_sso_cookies(handler, user_id=user_id, user_name=user_name)
                    clean_qs = qs_without_work_code(qs)
                    suffix = ("?" + urlencode(clean_qs, doseq=True)) if clean_qs else ""
                    handler.send_header("Location", f"{parsed.path}{suffix}")
                    handler.end_headers()
                    return False
            except ValueError as exc:
                # 包含 ``login_by_work_code`` 入参校验失败；历史上 ``_ensure_erp_config`` 里
                # ``float(ERP_TIMEOUT_SECONDS)`` 也会抛 ValueError，已改为安全解析。
                logger.warning("SaaS workCode rejected: %s", exc)
            except Exception:
                logger.exception(
                    "SaaS workCode ERP login failed (work_code_len=%s)",
                    len(work_code or ""),
                )
            handler.send_response(302)
            handler.send_header("Location", "login?error=invalid_workcode")
            handler.end_headers()
            return False

    # Public paths don't require auth
    if parsed.path in PUBLIC_PATHS or parsed.path.startswith('/static/') or parsed.path.startswith('/session/static/'):
        return True

    # Bearer / Header / Cookie（页面入口已通过 workCode 机会性刷新过 cookie）
    if get_authenticated_user_id(handler):
        return True
    if saas_mode:
        # SaaS mode enforces identity even without password auth enabled.
        if parsed.path.startswith('/api/'):
            handler.send_response(401)
            handler.send_header('Content-Type', 'application/json')
            handler.end_headers()
            handler.wfile.write(b'{"error":"Authentication required"}')
            return False
        handler.send_response(302)
        handler.send_header('Location', 'login?error=unauthorized')
        handler.end_headers()
        return False
    # Check session cookie
    cookie_val = parse_cookie(handler)
    if cookie_val and verify_session(cookie_val):
        return True
    # Not authorized
    if parsed.path.startswith('/api/'):
        handler.send_response(401)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(b'{"error":"Authentication required"}')
    else:
        handler.send_response(302)
        # Pass the original path as ?next= so login.js redirects back after auth.
        # SECURITY/CORRECTNESS: the inner `?` and `&` MUST be percent-encoded
        # when stuffed into the outer `?next=` parameter, otherwise:
        #   (a) multi-param query strings get truncated at the first inner `&`
        #       (e.g. `/api/sessions?limit=50&offset=0` would round-trip as
        #       just `/api/sessions?limit=50` after the browser parses the
        #       outer URL — `offset=0` becomes a separate top-level query
        #       parameter that the login page ignores).
        #   (b) attacker-controlled paths could inject a second `next=`
        #       parameter; per RFC 3986 the duplicate behaviour is undefined
        #       and parsers diverge (Python's parse_qs returns last-match,
        #       URLSearchParams returns first-match), opening a query-pollution
        #       footgun even though _safeNextPath() rejects most malicious
        #       shapes downstream.
        # Encoding the entire `path?query` blob with quote(safe='/') turns
        # `?` → `%3F` and `&` → `%26`, so the outer parameter holds exactly
        # one path-with-query string and `searchParams.get('next')` returns
        # the full original URL (the browser auto-decodes once).
        # (Opus pre-release advisor finding for v0.50.258.)
        import urllib.parse as _urlparse
        _path_with_query = parsed.path or '/'
        if parsed.query:
            _path_with_query += '?' + parsed.query
        # safe='/' keeps path separators readable; everything else (including
        # `?`, `&`, `=`) gets percent-encoded.
        _next = _urlparse.quote(_path_with_query, safe='/')
        handler.send_header('Location', 'login?next=' + _next)
        handler.end_headers()
    return False


def _decode_b64url(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode((segment + pad).encode("ascii"))


def _verify_hs256_jwt(token: str) -> dict | None:
    """最小可用 HS256 JWT 校验。

    注意：这里仅用于内部 SaaS 接入（对齐当前需求），不包含 kid/JWKS 等高级能力。
    """
    secret = os.getenv("HERMES_WEBUI_JWT_SECRET", "").strip()
    if not secret:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_decode_b64url(header_b64).decode("utf-8"))
        payload = json.loads(_decode_b64url(payload_b64).decode("utf-8"))
    except Exception:
        return None
    if header.get("alg") != "HS256":
        return None
    signed = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).digest()
    try:
        got = _decode_b64url(sig_b64)
    except Exception:
        return None
    if not hmac.compare_digest(expected, got):
        return None
    exp = payload.get("exp")
    if exp is not None:
        try:
            if time.time() >= float(exp):
                return None
        except Exception:
            return None
    return payload if isinstance(payload, dict) else None


def get_authenticated_user_id(handler) -> str | None:
    """统一提取当前请求的 user_id。

    优先级：
    1) Authorization: Bearer <jwt>（从 sub/user_id 取值）
    2) hermes_webui_auth + hermes_webui_user_id（WebUI 自有 SSO cookie）
    3) X-User-ID header（兼容链路）

    注意：SaaS 页面入口若带 workCode，应在 check_auth 中先于本函数处理并完成
    Set-Cookie，这样此处读到的是刷新后的身份。
    """
    authz = (handler.headers.get("Authorization") or "").strip()
    if authz.lower().startswith("bearer "):
        claims = _verify_hs256_jwt(authz[7:].strip())
        if claims:
            uid = claims.get("sub") or claims.get("user_id")
            if uid:
                return str(uid)
    cookie_uid = _webui_sso_cookie_user_id(handler)
    if cookie_uid:
        return cookie_uid
    # Compatibility fallback for internal callers when JWT is not wired yet.
    hdr_uid = (handler.headers.get("X-User-ID") or "").strip()
    if hdr_uid:
        return hdr_uid
    return None


def get_authenticated_display_name(handler) -> str | None:
    """用于界面问候等：JWT claims 中的姓名，或 WebUI SSO 的 ``hermes_webui_user_name`` cookie。"""
    authz = (handler.headers.get("Authorization") or "").strip()
    if authz.lower().startswith("bearer "):
        claims = _verify_hs256_jwt(authz[7:].strip())
        if claims:
            for key in ("name", "preferred_username", "given_name", "nickname"):
                v = claims.get(key)
                if v is not None and str(v).strip():
                    return str(v).strip()
    raw = _parse_cookie_value(handler, WEBUI_SSO_USER_NAME_COOKIE)
    if not raw:
        return None
    try:
        decoded = unquote(raw, errors="replace")
    except Exception:
        decoded = raw
    decoded = (decoded or "").strip()
    return decoded or None


def set_webui_sso_cookies(handler, user_id: str, user_name: str | None = None) -> None:
    """写入 WebUI 独立 SSO cookies。

    说明：接口协议可对齐 deer-flow（/api/auth/sso-login 的请求/返回），
    但 cookie key 不能复用 deer-flow，以免同域跨项目互相污染登录态。
    """
    cookie = http.cookies.SimpleCookie()
    cookie[WEBUI_SSO_AUTH_COOKIE] = "true"
    cookie[WEBUI_SSO_AUTH_COOKIE]["path"] = "/"
    cookie[WEBUI_SSO_AUTH_COOKIE]["max-age"] = str(SESSION_TTL)
    cookie[WEBUI_SSO_USER_ID_COOKIE] = str(user_id)
    cookie[WEBUI_SSO_USER_ID_COOKIE]["path"] = "/"
    cookie[WEBUI_SSO_USER_ID_COOKIE]["max-age"] = str(SESSION_TTL)
    if user_name:
        # ``SimpleCookie.OutputString()`` 按 latin-1 编码；中文姓名需百分比编码（对齐 deer-flow 对 name 的 encodeURIComponent）。
        cookie[WEBUI_SSO_USER_NAME_COOKIE] = quote(str(user_name), safe="")
        cookie[WEBUI_SSO_USER_NAME_COOKIE]["path"] = "/"
        cookie[WEBUI_SSO_USER_NAME_COOKIE]["max-age"] = str(SESSION_TTL)
    for morsel in cookie.values():
        handler.send_header("Set-Cookie", morsel.OutputString())


def set_auth_cookie(handler, cookie_value) -> None:
    """Set the auth cookie on the response."""
    cookie = http.cookies.SimpleCookie()
    cookie[COOKIE_NAME] = cookie_value
    cookie[COOKIE_NAME]['httponly'] = True
    cookie[COOKIE_NAME]['samesite'] = 'Lax'
    cookie[COOKIE_NAME]['path'] = '/'
    cookie[COOKIE_NAME]['max-age'] = str(SESSION_TTL)
    # Set Secure flag when connection is HTTPS
    if getattr(handler.request, 'getpeercert', None) is not None or handler.headers.get('X-Forwarded-Proto', '') == 'https':
        cookie[COOKIE_NAME]['secure'] = True
    handler.send_header('Set-Cookie', cookie[COOKIE_NAME].OutputString())


def clear_auth_cookie(handler) -> None:
    """Clear the auth cookie on the response."""
    cookie = http.cookies.SimpleCookie()
    cookie[COOKIE_NAME] = ''
    cookie[COOKIE_NAME]['httponly'] = True
    cookie[COOKIE_NAME]['path'] = '/'
    cookie[COOKIE_NAME]['max-age'] = '0'
    handler.send_header('Set-Cookie', cookie[COOKIE_NAME].OutputString())
