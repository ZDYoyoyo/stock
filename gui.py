"""台股研究系統 — 圖形介面控制台（Python 內建 tkinter，免安裝）。

雙擊 啟動控制台.bat 或執行：python gui.py
所有功能一鍵點按，輸出即時顯示在下方主控台。
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext

ROOT = Path(__file__).resolve().parent
PY = sys.executable
FONT = ("Microsoft JhengHei", 10)


class Console:
    def __init__(self, root):
        self.root = root
        root.title("台股研究系統 · 控制台")
        root.geometry("900x620")
        root.minsize(720, 480)
        self.q = queue.Queue()
        self.proc = None

        top = tk.Frame(root)
        top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(top, text="台股研究系統控制台", font=("Microsoft JhengHei", 14, "bold")).pack(side="left")
        self.status = tk.Label(top, text="就緒", fg="green", font=FONT)
        self.status.pack(side="right")

        body = tk.Frame(root)
        body.pack(fill="both", expand=True, padx=10, pady=4)

        # 左：按鈕區（可捲動，小螢幕也能看到全部）
        left_box = tk.Frame(body)
        left_box.pack(side="left", fill="y", padx=(0, 8))
        canvas = tk.Canvas(left_box, width=210, highlightthickness=0)
        sb = tk.Scrollbar(left_box, orient="vertical", command=canvas.yview)
        left = tk.Frame(canvas)
        left.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=left, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="y")
        sb.pack(side="right", fill="y")
        _wheel = lambda e: canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        self._buttons(left)

        # 右：輸出主控台
        right = tk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        self.out = scrolledtext.ScrolledText(right, font=("Consolas", 10), bg="#1e1e1e",
                                             fg="#e6e6e6", insertbackground="#fff", wrap="word")
        self.out.pack(fill="both", expand=True)
        self.log("歡迎使用台股研究系統控制台。點左側按鈕開始。\n"
                 "⚠️ 研究用途非投資建議；偏空環境請以觀望為主。\n")

        self._poll()

    # ---- 按鈕區 ----
    def _section(self, parent, title):
        tk.Label(parent, text=title, font=("Microsoft JhengHei", 9, "bold"),
                 fg="#555").pack(anchor="w", pady=(10, 2))

    def _btn(self, parent, text, cmd, color="#f0f0f0"):
        b = tk.Button(parent, text=text, font=FONT, width=17, anchor="w",
                      bg=color, relief="groove", command=cmd)
        b.pack(fill="x", pady=1)
        return b

    def _buttons(self, p):
        self._section(p, "每日")
        self._btn(p, "🌅 盤前快報(環境+費半)", lambda: self.run(["scripts.run_morning"], "盤前快報"), "#fff7e6")
        self._btn(p, "📊 盤後選股+推播", lambda: self.run(["scripts.run_all", "--notify"], "盤後選股"), "#e8f0fe")
        self._btn(p, "⚡ 快速選股(略長期)", lambda: self.run(["scripts.run_all", "--skip-longterm", "--notify"], "快速選股"), "#e8f0fe")
        self._btn(p, "🛡️ 盤中持股守衛", lambda: self.run(["scripts.monitor_portfolio"], "盤中守衛"), "#fef3e8")

        self._section(p, "資料 / 每週")
        self._btn(p, "🔄 更新資料", lambda: self.run(["scripts.update_data", "--days", "40"], "更新資料"))
        self._btn(p, "🏦 更新千張大戶", lambda: self.run(["scripts.update_holders"], "更新千張大戶"))

        self._section(p, "單軌 / 研究")
        self._btn(p, "🚀 月營收動能T12", lambda: self.run(["scripts.run_t12"], "月營收動能"), "#e8fbef")
        self._btn(p, "🏦 主力籌碼排行", lambda: self.run(["scripts.run_mainforce"], "主力籌碼"), "#eef4ff")
        self._btn(p, "🟢 長期價值", lambda: self.run(["scripts.run_longterm"], "長期價值"))
        self._btn(p, "🔴 當沖候選", lambda: self.run(["scripts.run_daytrade"], "當沖候選"))
        self._btn(p, "🔬 基本面深掘", lambda: self.run(["scripts.run_t30"], "深掘"))
        self._btn(p, "🧨 地雷偵測(持股)", lambda: self.run(["scripts.run_landmine", "--holdings"], "地雷偵測-持股"), "#fde8e8")
        self._btn(p, "🧨 候選排雷", lambda: self.run(["scripts.run_landmine", "--from-daily"], "候選排雷"), "#fde8e8")

        self._section(p, "持股 / 風控")
        self._input_area(p)

        self._section(p, "報告 / 其他")
        self._btn(p, "📂 開報告資料夾", self.open_reports)
        self._btn(p, "📈 開最新HTML報告", self.open_latest_html)
        self._btn(p, "⏹ 停止執行中工作", self.stop, "#fde8e8")

    def _input_area(self, p):
        f = tk.Frame(p)
        f.pack(fill="x")
        self.e = {}
        # 每欄右側灰字提示：單位/是否必填，一眼就懂
        hints = {"sid": "如2330 必", "lots": "張 1張=1000股 必", "price": "每股價 必",
                 "stop": "每股價 選填", "target": "每股價 選填"}
        for label, key in [("代號", "sid"), ("張數", "lots"), ("成本", "price"),
                           ("停損", "stop"), ("停利", "target")]:
            row = tk.Frame(f)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, font=FONT, width=4, anchor="w").pack(side="left")
            ent = tk.Entry(row, font=FONT, width=8)
            ent.pack(side="left")
            tk.Label(row, text=hints[key], font=("Microsoft JhengHei", 7), fg="#999",
                     anchor="w").pack(side="left", padx=(3, 0))
            self.e[key] = ent
        tk.Label(f, text="「必」=必填。詳細用法點下方 ❓填寫說明",
                 font=("Microsoft JhengHei", 7), fg="#888",
                 wraplength=200, justify="left").pack(anchor="w", pady=(2, 3))
        self._btn(p, "❓ 填寫說明", self.show_help, "#f0f0f0")
        self._btn(p, "➕ 記錄持股(已買)", self.add_holding)
        self._btn(p, "➖ 移除持股", self.remove_holding)
        self._btn(p, "📋 查看持股損益", lambda: self.run(["scripts.portfolio"], "查看持股"))
        self._btn(p, "🧮 風控試算(算買幾張)", self.risk_calc)
        self._btn(p, "🧨 排雷(此代號)", self.landmine_one)
        self._btn(p, "🏦 查籌碼(此代號)", self.mainforce_one)
        self._btn(p, "🔍 籌碼診斷(法人+融資券+漲跌原因)", self.diagnose_one, "#eef4ff")

    def diagnose_one(self):
        sid = self.e["sid"].get().strip()
        if not sid:
            self.log("⚠️ 籌碼診斷需填代號（外資/投信/自營+融資融券逐日+今日漲跌歸因）\n")
            return
        self.run(["scripts.diagnose", sid], f"籌碼診斷 {sid}")

    def show_help(self):
        self.log(
            "\n" + "=" * 50 + "\n❓ 持股 / 風控 欄位填寫說明\n" + "=" * 50 + "\n"
            "上面 5 個欄位是「共用」的，配不同按鈕做不同事：\n\n"
            "【A. 已經買了 → 追蹤損益】按「➕ 記錄持股(已買)」\n"
            "  代號 = 股票代號，如 2330            (必填)\n"
            "  張數 = 幾張，1張=1000股；零股填小數  (必填)\n"
            "  成本 = 你的每股買入均價，如 1000     (必填)\n"
            "  停損 = 每股停損價，如 950           (選填，建議填)\n"
            "  停利 = 每股停利價，如 1100          (選填)\n"
            "  → 填完按 ➕；再按「📋 查看持股損益」看即時損益。\n"
            "  → 有填停損，「🛡️盤中持股守衛」才會在觸價時提醒你。\n\n"
            "【B. 還沒買 → 想算該買幾張】按「🧮 風控試算」\n"
            "  只需填『代號』一個，其餘欄位它不看。\n"
            "  它用 資金100萬、單筆風險1.5%、ATR停損 幫你算出：\n"
            "  建議進場/停損/停利價 + 建議張數 + 最大虧損。\n"
            "  (這是買『之前』算部位大小，跟記錄持股是兩回事)\n\n"
            "【只需填代號的按鈕】➖移除持股、🧨排雷、🏦查籌碼。\n\n"
            "── 完整範例 ──\n"
            "想買台積電 → 填 代號=2330 → 按🧮風控試算 → 它說買2張、\n"
            "停損950 → 你下單買2張@1000 → 填 張數=2 成本=1000 停損=950\n"
            "→ 按➕記錄持股 → 之後按📋查看持股損益 追蹤。\n"
            "持股存於 data/portfolio.csv（可用 Excel 直接編輯，不上傳）。\n"
            + "=" * 50 + "\n")

    # ---- 動作 ----
    def add_holding(self):
        sid, lots, price = self.e["sid"].get().strip(), self.e["lots"].get().strip(), self.e["price"].get().strip()
        if not (sid and lots and price):
            self.log("⚠️ 記錄持股需填：代號、張數、成本\n")
            return
        args = ["scripts.portfolio", "add", sid, "--lots", lots, "--price", price]
        if self.e["stop"].get().strip():
            args += ["--stop", self.e["stop"].get().strip()]
        if self.e["target"].get().strip():
            args += ["--target", self.e["target"].get().strip()]
        self.run(args, f"記錄持股 {sid}")

    def remove_holding(self):
        sid = self.e["sid"].get().strip()
        if not sid:
            self.log("⚠️ 移除持股需填代號\n")
            return
        self.run(["scripts.portfolio", "remove", sid], f"移除 {sid}")

    def risk_calc(self):
        sid = self.e["sid"].get().strip()
        if not sid:
            self.log("⚠️ 風控試算需填代號（資金預設100萬、風險1.5%，可在指令調）\n")
            return
        self.run(["scripts.risk_calc", "--stock", sid, "--account", "1000000", "--risk", "1.5"],
                 f"風控試算 {sid}")

    def landmine_one(self):
        sid = self.e["sid"].get().strip()
        if not sid:
            self.log("⚠️ 排雷需填代號（或用左側「地雷偵測(持股)」檢查全部持股）\n")
            return
        self.run(["scripts.run_landmine", "--stocks", sid], f"排雷 {sid}")

    def mainforce_one(self):
        sid = self.e["sid"].get().strip()
        if not sid:
            self.log("⚠️ 查籌碼需填代號（或用左側「主力籌碼排行」看全市場）\n")
            return
        self.run(["scripts.run_mainforce", "--stocks", sid], f"查籌碼 {sid}")

    def open_reports(self):
        d = ROOT / "reports" / "screener"
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(d))
        except Exception as e:
            self.log(f"開啟失敗：{e}\n")

    def open_latest_html(self):
        d = ROOT / "reports" / "screener"
        files = sorted(d.glob("*_run_all.html")) if d.exists() else []
        if not files:
            self.log("還沒有 run_all 報告，先跑一次「盤後選股」。\n")
            return
        try:
            os.startfile(str(files[-1]))
        except Exception as e:
            self.log(f"開啟失敗：{e}\n")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.log("\n⏹ 已送出停止訊號。\n")
        else:
            self.log("目前沒有執行中的工作。\n")

    # ---- 執行子程序 ----
    def run(self, args, name):
        if self.proc and self.proc.poll() is None:
            self.log("⚠️ 有工作正在執行，請等它結束或按「停止」。\n")
            return
        self.log(f"\n{'='*50}\n▶ {name}\n{'='*50}\n")
        self.status.config(text=f"執行中：{name}", fg="#c0392b")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        def worker():
            try:
                self.proc = subprocess.Popen(
                    [PY, "-u", "-m"] + args, cwd=str(ROOT),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    encoding="utf-8", errors="replace", env=env)
                for line in self.proc.stdout:
                    self.q.put(line)
                self.proc.wait()
                self.q.put(f"\n✔ 完成：{name}\n")
            except Exception as e:
                self.q.put(f"\n✖ 錯誤：{e}\n")
            self.q.put(("__done__", None))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple) and item[0] == "__done__":
                    self.status.config(text="就緒", fg="green")
                else:
                    self.log(item)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def log(self, text):
        self.out.insert(tk.END, text)
        self.out.see(tk.END)


if __name__ == "__main__":
    root = tk.Tk()
    Console(root)
    root.mainloop()
