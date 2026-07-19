"""每日報告推播（Telegram 優先，其次 Email）。

Line Notify 已於 2025/3/31 停止服務，改用 Telegram（免費、手機推播、設定最簡單）。
設定寫在 .env：
  Telegram：TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID
  Email   ：SMTP_HOST、SMTP_PORT、SMTP_USER、SMTP_PASS、MAIL_TO
"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv

load_dotenv()


def _tg():
    return os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(text: str):
    token, chat = _tg()
    if not token or not chat:
        return False, "未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text[:4000],
                                "parse_mode": "HTML", "disable_web_page_preview": "true"},
                          timeout=20)
        return r.ok, r.text[:200]
    except requests.RequestException as e:
        return False, str(e)[:200]


def send_telegram_file(path, caption: str = ""):
    token, chat = _tg()
    if not token or not chat:
        return False, "未設定 Telegram"
    try:
        with open(path, "rb") as f:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendDocument",
                              data={"chat_id": chat, "caption": caption[:1000]},
                              files={"document": f}, timeout=60)
        return r.ok, r.text[:200]
    except (requests.RequestException, OSError) as e:
        return False, str(e)[:200]


def send_email(subject: str, body: str):
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    pw = os.getenv("SMTP_PASS")
    to = os.getenv("MAIL_TO", user)
    if not host or not user:
        return False, "未設定 SMTP_HOST / SMTP_USER"
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"], msg["From"], msg["To"] = subject, user, to
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            s.login(user, pw)
            s.sendmail(user, [to], msg.as_string())
        return True, "sent"
    except Exception as e:
        return False, str(e)[:200]


def notify(text: str, subject: str = "台股每日報告", file_path: str | None = None):
    """依 .env 自動選管道。回傳 (ok, channel, detail)。"""
    token, chat = _tg()
    if token and chat:
        ok, d = send_telegram(text)
        if ok and file_path:
            send_telegram_file(file_path, "完整報告")
        return ok, "telegram", d
    if os.getenv("SMTP_HOST"):
        ok, d = send_email(subject, text)
        return ok, "email", d
    return False, "none", "未設定通知管道（.env 填 TELEGRAM_* 或 SMTP_*）"
