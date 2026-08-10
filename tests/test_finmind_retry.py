"""FinMind fetch 對暫時性 5xx 伺服器錯誤應退避重試、不直接炸掉（跑法：python -m pytest tests/ -q）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src import finmind_client as fc


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"raise_for_status 不該在 {self.status_code} 被呼叫(應已重試)")

    def json(self):
        return self._payload


def test_fetch_retries_on_502(monkeypatch):
    monkeypatch.setattr(fc.time, "sleep", lambda *a: None)      # 免真的等待
    seq = [_Resp(502), _Resp(502), _Resp(200, {"status": 200, "data": [{"x": 1}]})]
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        r = seq[calls["n"]]
        calls["n"] += 1
        return r

    monkeypatch.setattr(fc.requests, "get", fake_get)
    data = fc.fetch("AnyDataset", start_date="2026-01-01", retries=3)
    assert data == [{"x": 1}]
    assert calls["n"] == 3                                       # 502→502→200，確實重試


def test_fetch_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(fc.time, "sleep", lambda *a: None)
    monkeypatch.setattr(fc.requests, "get", lambda *a, **k: _Resp(503))
    with pytest.raises(RuntimeError):                            # 一直 5xx → 退避耗盡拋 RuntimeError(非 HTTPError)
        fc.fetch("AnyDataset", start_date="2026-01-01", retries=2)
