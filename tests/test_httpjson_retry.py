"""官方 OpenAPI 退避重試（src/httpjson）＋ TWSE 法人 stat 重試。

盯的是 2026-10 實際踩到的兩種『靜默降級』：
  1. openapi.twse.com.tw 間歇回 WAF 阻擋頁（HTTP 200 但內容是 HTML）→ 月營收/處置股/
     除權息整批消失，報告看起來跟「今天真的沒有」一樣。
  2. TWSE T86 法人整批回 0（stat 不是 OK）而同輪 price/margin 正常 → 法人欄整批留白。
兩者都是**暫時**失敗，重試就拿得到；所以測的重點是「失敗後會再試，且最終拿到正確資料」。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import httpjson


class _Resp:
    def __init__(self, status=200, payload=None, text=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("not json")       # WAF 阻擋頁：200 但內容是 HTML
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def _patch(monkeypatch, responses):
    """讓 requests.get 依序回 responses，並記錄呼叫次數；sleep 設成不真的等。"""
    calls = {"n": 0}

    def fake_get(*a, **kw):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        r = responses[i]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(httpjson.requests, "get", fake_get)
    monkeypatch.setattr(httpjson.time, "sleep", lambda s: None)
    return calls


_WAF = "<html><body>因為安全性考量，您所執行的頁面無法呈現。</body></html>"


def test_waf_block_then_success_retries_and_returns_data(monkeypatch):
    """阻擋頁(200+HTML) → 重試 → 第三次拿到資料。這就是 10/08 當天的實際情形。"""
    good = [{"公司代號": "2330"}]
    calls = _patch(monkeypatch, [_Resp(200, None, _WAF), _Resp(200, None, _WAF), _Resp(200, good)])
    out = httpjson.get_json_list("http://x")
    assert out == good
    assert calls["n"] == 3, "前兩次失敗後必須真的再試"


def test_all_attempts_blocked_returns_empty(monkeypatch):
    """全被擋 → 回 []（呼叫端自行判斷殘缺），且用掉全部嘗試次數。"""
    calls = _patch(monkeypatch, [_Resp(200, None, _WAF)])
    assert httpjson.get_json_list("http://x") == []
    assert calls["n"] == len(httpjson._BACKOFF) + 1


def test_empty_list_is_retried_by_default(monkeypatch):
    """空清單預設當失敗：這幾個端點正常都有幾十到上千筆，空幾乎等於被擋。"""
    good = [{"a": 1}]
    calls = _patch(monkeypatch, [_Resp(200, []), _Resp(200, good)])
    assert httpjson.get_json_list("http://x") == good
    assert calls["n"] == 2


def test_empty_list_accepted_when_retry_on_empty_false(monkeypatch):
    """呼叫端若認為「空是合理結果」，就不該浪費重試。"""
    calls = _patch(monkeypatch, [_Resp(200, [])])
    assert httpjson.get_json_list("http://x", retry_on_empty=False) == []
    assert calls["n"] == 1


def test_first_try_success_does_not_retry(monkeypatch):
    """正常情況不能多打——否則每天對每個端點都多花時間。"""
    good = [{"a": 1}]
    calls = _patch(monkeypatch, [_Resp(200, good)])
    assert httpjson.get_json_list("http://x") == good
    assert calls["n"] == 1


def test_network_error_then_success(monkeypatch):
    import requests
    good = [{"a": 1}]
    calls = _patch(monkeypatch, [requests.RequestException("boom"), _Resp(200, good)])
    assert httpjson.get_json_list("http://x") == good
    assert calls["n"] == 2


def test_non_list_payload_treated_as_failure(monkeypatch):
    """回 dict（改版/錯誤物件）不是我們要的 array → 當失敗。"""
    good = [{"a": 1}]
    calls = _patch(monkeypatch, [_Resp(200, {"msg": "nope"}), _Resp(200, good)])
    assert httpjson.get_json_list("http://x") == good
    assert calls["n"] == 2


# --- TWSE 法人 stat 重試 ---

def _patch_twse(monkeypatch, responses):
    from src import twse_client as tw
    calls = {"n": 0}

    def fake_get(*a, **kw):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[i]

    monkeypatch.setattr(tw.requests, "get", fake_get)
    monkeypatch.setattr(tw.time, "sleep", lambda s: None)
    return calls


def test_twse_stat_not_ok_is_retried_when_need_ok(monkeypatch):
    """法人 T86 回 stat 不 OK → 要重試；拿到 OK 就回那筆。"""
    from src import twse_client as tw
    ok = {"stat": "OK", "fields": [], "data": []}
    calls = _patch_twse(monkeypatch, [_Resp(200, {"stat": "很抱歉，沒有符合條件的資料!"}),
                                      _Resp(200, ok)])
    out = tw._get("fund/T86", {}, need_ok=True)
    assert out == ok
    assert calls["n"] == 2


def test_twse_stat_not_ok_exhausts_then_empty(monkeypatch):
    """真的未公布（每次都不 OK）→ 重試用完回 {}，呼叫端照舊得到空清單。"""
    from src import twse_client as tw
    calls = _patch_twse(monkeypatch, [_Resp(200, {"stat": "no data"})])
    assert tw._get("fund/T86", {}, need_ok=True) == {}
    assert calls["n"] == 3


def test_twse_default_does_not_require_stat(monkeypatch):
    """沒傳 need_ok 的呼叫端行為不可變（price/margin 等仍自行判斷內容）。"""
    from src import twse_client as tw
    payload = {"stat": "no data", "tables": []}
    calls = _patch_twse(monkeypatch, [_Resp(200, payload)])
    assert tw._get("afterTrading/MI_INDEX", {}) == payload
    assert calls["n"] == 1, "預設不該因 stat 而多打"


def test_institutional_passes_need_ok(monkeypatch):
    """確認 institutional() 真的用了 need_ok（否則上面的重試形同未接上）。"""
    from src import twse_client as tw
    seen = {}

    def fake_get(path, params, retries=3, *, need_ok=False):
        seen["need_ok"] = need_ok
        return {}

    monkeypatch.setattr(tw, "_get", fake_get)
    assert tw.institutional("20261008") == []
    assert seen["need_ok"] is True
