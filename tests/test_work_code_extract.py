"""workCode 解析与 deer-flow middleware 对齐（请求头优先、头名大小写不敏感）。"""

from http.client import HTTPMessage
from types import SimpleNamespace

from urllib.parse import parse_qs

from api.auth import extract_work_code_with_meta


def _handler_with_headers(**hdrs: str) -> SimpleNamespace:
    msg = HTTPMessage()
    for k, v in hdrs.items():
        msg[k] = v
    return SimpleNamespace(headers=msg)


def test_header_wins_over_query():
    h = _handler_with_headers(workCode="from-header")
    qs = parse_qs("workCode=from-query", keep_blank_values=False)
    wc, meta = extract_work_code_with_meta(h, qs)
    assert wc == "from-header"
    assert meta["source"] == "header:workCode"


def test_header_name_case_insensitive():
    h = _handler_with_headers(WORKCODE="wc-upper")
    qs = parse_qs("", keep_blank_values=False)
    wc, meta = extract_work_code_with_meta(h, qs)
    assert wc == "wc-upper"
    assert meta["source"] == "header:workCode"


def test_query_fallback_when_no_header():
    h = _handler_with_headers()
    qs = parse_qs("work_code=alias-val", keep_blank_values=False)
    wc, meta = extract_work_code_with_meta(h, qs)
    assert wc == "alias-val"
    assert meta["source"] == "query:work_code"


def test_x_work_code_before_query():
    h = _handler_with_headers(**{"X-Work-Code": "xwc"})
    qs = parse_qs("workCode=q", keep_blank_values=False)
    wc, meta = extract_work_code_with_meta(h, qs)
    assert wc == "xwc"
    assert meta["source"] == "header:X-Work-Code"
