"""官方 OpenAPI 的 JSON 清單抓取：帶**間隔**的退避重試。

為什麼需要這支（2026-10-08 實測得出）：
TWSE 的 `openapi.twse.com.tw` 不是穩定封鎖，而是**間歇性**回 WAF 阻擋頁——
HTTP **200** 但內容是 HTML「因為安全性考量，您所執行的頁面無法呈現」。
同一天三次跑 run_all，月營收分別拿到 230 / 79 / 79 檔、除權息預告 46 / 10 / 10 檔；
中間單獨重測又回正常的 1085 筆 → **失敗是暫時的，隔幾秒重試就有機會拿到**。

原本的寫法救不了：
- `revenue_client._get` 雖有 `retries=3` 但**沒有間隔**，連打三次幾乎必然同樣被擋。
- `disposal._get` / `exdividend._get` **完全沒有重試**，一次失敗就靜默回空 →
  上市處置股警示與除權息預告整批消失，而報告看起來跟「今天真的沒有」一模一樣。

⚠️ 預設把「空清單」也當失敗而重試（`retry_on_empty`）：這三個端點正常情況下都該有幾十到
上千筆（上市月營收 ~1085 檔、處置股與除權息幾乎天天有），空幾乎等於被擋。代價只是
真的空的那天多等幾秒。若日後接上「空是合理結果」的端點，呼叫時傳 `retry_on_empty=False`。
"""
from __future__ import annotations

import time

import requests

_UA = {"User-Agent": "Mozilla/5.0"}
_BACKOFF = (1.0, 3.0, 7.0)          # 第 1/2/3 次失敗後各等幾秒；總等待上限 11 秒


def get_json_list(url: str, *, headers: dict | None = None, timeout: int = 30,
                  backoff: tuple = _BACKOFF, retry_on_empty: bool = True) -> list:
    """抓一個回 JSON array 的端點，失敗就隔幾秒重試；全失敗回 []（呼叫端自行判斷殘缺）。

    判定成功＝HTTP 200、內容解析得出 list，且（retry_on_empty 時）非空。
    WAF 阻擋頁是 200+HTML，`r.json()` 會丟 ValueError → 視為失敗重試。
    """
    attempts = len(backoff) + 1
    for i in range(attempts):
        try:
            r = requests.get(url, headers=headers or _UA, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and (data or not retry_on_empty):
                    return data
        except Exception:
            pass                     # 斷網/逾時/改版/WAF 阻擋頁(非 JSON) → 當失敗
            # 刻意 catch 寬：這支取代了 disposal/exdividend 原本的 catch-Exception
            # （底層 socket 問題不一定包成 RequestException，例如直接冒出 OSError），
            # 且 try 區塊只有一次 HTTP 呼叫＋一次 JSON 解析，不會吞掉別處的程式錯誤。
        if i < len(backoff):
            time.sleep(backoff[i])
    return []
