"""FinMind API 精簡 client。

FinMind 免費資料源：https://finmindtrade.com/
以「日期區間、全市場」方式抓取（一次 call 回傳所有股票），
比逐檔查詢省很多額度、也避免打爆 rate limit。
"""
from __future__ import annotations

import os
import time

import requests
from dotenv import load_dotenv

from .config import FINMIND_URL

load_dotenv()
_TOKEN = os.getenv("FINMIND_TOKEN", "")


def fetch(dataset: str, start_date: str, end_date: str | None = None,
          data_id: str | None = None, retries: int = 3) -> list[dict]:
    """呼叫 FinMind /data，回傳 data 陣列。失敗會退避重試。"""
    params = {"dataset": dataset, "start_date": start_date}
    if end_date:
        params["end_date"] = end_date
    if data_id:
        params["data_id"] = data_id
    if _TOKEN:
        params["token"] = _TOKEN

    for attempt in range(retries):
        try:
            resp = requests.get(FINMIND_URL, params=params, timeout=60)
        except requests.RequestException as e:
            # 連線中斷/逾時（如 Connection reset by peer）→ 退避重試
            wait = 5 * (attempt + 1)
            print(f"  [FinMind] 連線問題({type(e).__name__})，等 {wait}s 後重試…")
            time.sleep(wait)
            continue
        if resp.status_code in (402, 429):
            # 額度用盡 / 觸發限流 → 退避
            wait = 60 * (attempt + 1)
            print(f"  [FinMind] 限流/額度({resp.status_code})，等 {wait}s 後重試…")
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            # 伺服器暫時性錯誤(502 Bad Gateway/503/504 等)→ 退避重試，別讓一次抖動炸掉整份日報
            wait = 5 * (attempt + 1)
            print(f"  [FinMind] 伺服器暫時錯誤({resp.status_code})，等 {wait}s 後重試…")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != 200:
            raise RuntimeError(f"FinMind error: {payload.get('msg')}")
        return payload.get("data", [])
    raise RuntimeError(f"FinMind {dataset} 重試 {retries} 次仍失敗")


def usage() -> dict | None:
    """查這個 token 本小時已用幾次／上限（FinMind user_info；查詢本身不計次，2026-11-25 實測）。

    計數每個整點歸零。用途是診斷 402：撞到限流時印出來，就分得出是「這個 token 真的用完」
    還是「token 沒用完卻被擋」（後者代表不是我們的用量問題）。查不到回 None，不影響主流程。
    """
    if not _TOKEN:
        return None
    try:
        r = requests.get("https://api.web.finmindtrade.com/v2/user_info",
                         params={"token": _TOKEN}, timeout=20)
        d = r.json()
        if d.get("status") != 200:
            return None
        return {"used": d.get("user_count"), "limit": d.get("api_request_limit"),
                "level": d.get("level_title")}
    except Exception:
        return None

