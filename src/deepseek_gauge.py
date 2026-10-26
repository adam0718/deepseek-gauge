#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek Gauge — DeepSeek 余额悬浮窗
=====================================

一个常驻桌面的极简悬浮窗，实时显示 DeepSeek 平台账户余额：

    可用余额  — 充值余额 + 赠金余额（能花出去的钱）
    累计消费  — 这个账号总共花掉多少

交互：
    鼠标移入  → 完全显示
    鼠标移出  → 半透明淡出，不挡视线
    左键拖动  → 移动位置（位置自动记忆）
    双击      → 立即刷新
    右键      → 菜单

数据来源：DeepSeek Harness（dsh）本地登录凭证 + DeepSeek 平台官方接口
（https://platform.deepseek.com/api/v0/users/get_user_summary）。
复用 dsh 已有的账号授权，不需要另外填 API Key。

凭证只读、不落盘、不写回。窗口只缓存余额数字，不缓存 token。

依赖：Python 3.9+ 标准库。零第三方依赖。
"""

import ctypes
import json
import os
import queue
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox

# ---------------------------------------------------------------- 常量

APP_NAME = "DeepSeekGauge"
BALANCE_URL = "https://platform.deepseek.com/api/v0/users/get_user_summary"
# dsh 的凭证文件里，平台账号授权记录在这个键下
CREDENTIAL_KEY = "deepseek-account-platform/default"
CREDENTIAL_FILE = ".credentials.yaml"

CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_FILE = CONFIG_DIR / "cache.json"

WIN_W, WIN_H = 252, 132
CARD = (6, 6, 246, 126)
CARD_R = 14

# 卡片内各行基线：标题 22 / 大额余额 52 / 明细 80 / 脚注 106

# 魔术色：圆角之外的所有像素都会被系统抠掉
KEY_COLOR_HEX = "#010203"
KEY_COLOR_REF = 0x00030201  # COLORREF: 0x00BBGGRR

C_BG = "#0F1218"
C_BORDER = "#242C3A"
C_EDGE_ALERT = "#B33A46"
C_TITLE = "#E8EEF9"
C_DIM = "#697687"
C_FOOT = "#6E7A8C"

C_OK = "#37D39A"
C_WARN = "#F5C542"
C_DANGER = "#FF5D5D"
C_OFFLINE = "#4B5563"

# DeepSeek 品牌蓝，用作货币徽章底色
C_BRAND = "#4D6BFE"

CURRENCY_SYMBOL = {"CNY": "¥", "USD": "$"}

DEFAULT_CONFIG = {
    "idle_alpha": 0.55,
    "hover_alpha": 1.0,
    "refresh_seconds": 60,
    "pos": None,
    "rounded": True,
    "dsh_home": None,
}

# 余额低于这些金额时变色：钱快花完了比"用掉几成"更直观
LOW_BALANCE_DANGER = 2.0
LOW_BALANCE_WARN = 10.0


# ---------------------------------------------------------------- 配置


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def load_cache():
    try:
        if CACHE_FILE.exists():
            raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("total") is not None:
                return raw
    except Exception:
        pass
    return None


def save_cache(data: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


# ---------------------------------------------------------------- 数据层


class BalanceError(Exception):
    """余额读取失败。message 直接用于界面提示，必须是人话。"""


def find_dsh_home(explicit=None):
    """定位 dsh 的 home 目录（里面有 .credentials.yaml）。

    优先级：配置里指定的 > 环境变量 > 常见安装位置自动探测。
    显式指定的路径失效时**继续往下探测**——配置过期（比如把 dsh 挪了盘）
    不该让程序直接罢工，能找到真的就用真的。
    全都没找到返回 None，由界面提示用户手动选。
    """
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("DSH_HOME")
    if env:
        candidates.append(Path(env))

    home = Path.home()
    candidates += [
        home / "DeepSeek" / "dsh-home",
        home / ".dsh",
        home / "dsh-home",
        home / "Documents" / "deepseek-harness",
    ]
    # dsh 常被装在非系统盘（这台机器在 H:），扫一遍盘符成本很低
    for letter in "CDEFGH":
        candidates.append(Path("%s:/DeepSeek/dsh-home" % letter))

    for c in candidates:
        try:
            if (c / CREDENTIAL_FILE).is_file():
                return c
        except OSError:
            continue
    return None


def read_token(dsh_home):
    """从 dsh 的凭证文件里读出平台授权 token。

    只做最小解析：找到 CREDENTIAL_KEY 那一段，取其中的 token 行。
    不引入 yaml 依赖，也不碰文件里的其他任何字段。
    """
    if not dsh_home:
        return None
    path = Path(dsh_home) / CREDENTIAL_FILE
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except Exception:
        return None

    inside = False
    base = 0
    for line in lines:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()
        if text.startswith(CREDENTIAL_KEY + ":"):
            inside, base = True, indent
            continue
        if inside:
            if indent <= base:
                break  # 离开这一段了
            if text.startswith("token:"):
                value = text.split(":", 1)[1].strip().strip("\"'")
                return value or None
    return None


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_balance(payload):
    """把平台响应整理成界面要用的扁平结构。认不出来就返回 None。

    真实响应形状（2026-10 实测）：
        {"code":0,"data":{"biz_data":{
            "normal_wallets":[{"currency":"CNY","balance":"5.33","token_estimation":"0"}],
            "bonus_wallets":[{"currency":"CNY","balance":"0",...}],
            "total_costs":[{"currency":"CNY","amount":"11.03"}]}}}
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("code") not in (0, None):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    biz = data.get("biz_data")
    if not isinstance(biz, dict):
        return None

    normal = biz.get("normal_wallets") or []
    bonus = biz.get("bonus_wallets") or []
    costs = biz.get("total_costs") or []
    if not isinstance(normal, list):
        normal = []
    if not isinstance(bonus, list):
        bonus = []

    # 按币种归并；同一币种出现多条时累加
    wallets = {}
    for item in list(normal) + list(bonus):
        if not isinstance(item, dict):
            continue
        cur = str(item.get("currency") or "").upper()
        if not cur:
            continue
        wallets[cur] = wallets.get(cur, 0.0) + _num(item.get("balance"))

    if not wallets:
        return None

    spent = {}
    for item in costs if isinstance(costs, list) else []:
        if not isinstance(item, dict):
            continue
        cur = str(item.get("currency") or "").upper()
        if cur:
            spent[cur] = spent.get(cur, 0.0) + _num(item.get("amount"))

    # 主币种：优先有余额的，其次人民币，最后随便取一个
    primary = None
    for cur, amount in wallets.items():
        if amount > 0:
            primary = cur
            break
    if not primary:
        primary = "CNY" if "CNY" in wallets else sorted(wallets)[0]

    normal_sum = 0.0
    bonus_sum = 0.0
    for item in normal if isinstance(normal, list) else []:
        if isinstance(item, dict) and str(item.get("currency") or "").upper() == primary:
            normal_sum += _num(item.get("balance"))
    for item in bonus if isinstance(bonus, list) else []:
        if isinstance(item, dict) and str(item.get("currency") or "").upper() == primary:
            bonus_sum += _num(item.get("balance"))

    return {
        "currency": primary,
        "normal": round(normal_sum, 6),
        "bonus": round(bonus_sum, 6),
        "total": round(wallets[primary], 6),
        "spent": round(spent.get(primary, 0.0), 6),
        "fetched_at": time.time(),
    }


def fetch_balance(dsh_home):
    """拉一次余额。任何失败都抛 BalanceError，message 是给用户看的中文。"""
    token = read_token(dsh_home)
    if not token:
        raise BalanceError("未找到 DSH 登录信息")

    req = urllib.request.Request(
        BALANCE_URL,
        headers={
            "x-dsh-auth-token": token,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (DeepSeekGauge)",
        },
    )

    last = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise BalanceError("授权已过期，请在 DSH 重新登录")
            last = "网络不可用"
            if attempt == 0:
                time.sleep(1.2)
        except Exception:
            last = "网络不可用"
            if attempt == 0:
                time.sleep(1.2)
    else:
        raise BalanceError(last or "读取失败")

    try:
        payload = json.loads(raw)
    except Exception:
        raise BalanceError("返回内容异常")

    result = normalize_balance(payload)
    if result is None:
        # 平台在登录失效时也可能回 200 + 业务错误码
        code = payload.get("code") if isinstance(payload, dict) else None
        if code not in (0, None):
            raise BalanceError("授权已过期，请在 DSH 重新登录")
        raise BalanceError("返回内容异常")
    return result


# ---------------------------------------------------------------- 展示辅助


def fmt_money(amount: float, currency: str = "CNY") -> str:
    symbol = CURRENCY_SYMBOL.get((currency or "").upper(), "")
    try:
        return "%s%.2f" % (symbol, float(amount))
    except (TypeError, ValueError):
        return "%s--" % symbol


def balance_color(total: float) -> str:
    """余额越少越红。钱花完了比"用掉几成"更需要一眼看见。"""
    try:
        value = float(total)
    except (TypeError, ValueError):
        return C_DIM
    if value < LOW_BALANCE_DANGER:
        return C_DANGER
    if value < LOW_BALANCE_WARN:
        return C_WARN
    return C_OK


def fmt_ago(seconds: float) -> str:
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return "%d 分钟前" % int(seconds // 60)
    if seconds < 86400:
        return "%d 小时前" % int(seconds // 3600)
    return "%d 天前" % int(seconds // 86400)


def fmt_clock(ts: float) -> str:
    if not ts:
        return "--"
    try:
        return time.strftime("%H:%M", time.localtime(ts))
    except Exception:
        return "--"


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    """圆角矩形（平滑多边形近似）。"""
    r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1,
        x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2,
        x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(pts, smooth=True, **kw)


# ---------------------------------------------------------------- 窗口


class Gauge:
    def __init__(self, root: tk.Tk, cfg: dict):
        self.root = root
        self.cfg = cfg
        self.dsh_home = find_dsh_home(cfg.get("dsh_home"))
        self.data = load_cache()
        self.error = None
        self.fetching = False
        self._fetch_started = 0.0
        # 子线程 → 主线程的唯一通道。tkinter 不是线程安全的：
        # 在子线程里调 root.after() 一旦失败（休眠唤醒、主循环忙时常见），
        # 回调就永久丢失，fetching 再也没人复位，整个刷新链会静默死掉。
        self._q = queue.Queue()
        self._last_auto = time.time()
        self._drag = None
        self._idle_job = None
        self._tick_job = None
        self._refresh_job = None

        self._hwnd = None
        self._layered = False

        self._build_window()
        self._bind_events()
        self._paint_static()
        self._paint_data()

        self.root.after(120, self._init_alpha)
        self.refresh()
        self._schedule_refresh()
        self._tick()

    # -------------------------------------------------- 窗口骨架

    def _build_window(self):
        r = self.root
        r.title("DeepSeek Gauge")
        r.overrideredirect(True)
        r.attributes("-topmost", bool(self.cfg.get("always_on_top", True)))
        r.configure(bg=KEY_COLOR_HEX if self.cfg.get("rounded") else C_BG)
        r.geometry("%dx%d+%d+%d" % (WIN_W, WIN_H, *self._initial_pos()))
        r.resizable(False, False)
        # 项目自带的图标（对话框、任务栏都跟着它）；取不到就安静跳过
        try:
            icon = Path(__file__).resolve().parent.parent / "icon.ico"
            if icon.is_file():
                r.iconbitmap(default=str(icon))
        except Exception:
            pass

        self.canvas = tk.Canvas(
            r,
            width=WIN_W,
            height=WIN_H,
            bg=KEY_COLOR_HEX if self.cfg.get("rounded") else C_BG,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

    def _initial_pos(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        pos = self.cfg.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                x, y = int(pos[0]), int(pos[1])
                # 必须整块落在屏幕内，否则底部信息会被屏幕边缘切掉
                x = max(0, min(x, sw - WIN_W))
                y = max(0, min(y, sh - WIN_H))
                return x, y
            except Exception:
                pass
        return sw - WIN_W - 28, 28

    # -------------------------------------------------- 分层窗口（圆角 + 可变透明）

    def _init_alpha(self):
        try:
            u32 = ctypes.windll.user32
            hwnd = u32.GetAncestor(self.root.winfo_id(), 2)  # GA_ROOT
            if not hwnd:
                hwnd = self.root.winfo_id()
            self._hwnd = hwnd
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            ex = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
            self._layered = True
            self.set_alpha(self.cfg["idle_alpha"])
        except Exception:
            self._layered = False
            try:
                self.root.attributes("-alpha", self.cfg["idle_alpha"])
            except Exception:
                pass

    def set_alpha(self, value: float):
        value = max(0.05, min(1.0, float(value)))
        if self._layered and self._hwnd:
            LWA_ALPHA = 0x2
            LWA_COLORKEY = 0x1
            flags = LWA_ALPHA
            key = 0
            if self.cfg.get("rounded"):
                flags |= LWA_COLORKEY
                key = KEY_COLOR_REF
            try:
                ctypes.windll.user32.SetLayeredWindowAttributes(
                    self._hwnd, key, int(value * 255), flags
                )
                return
            except Exception:
                pass
        try:
            self.root.attributes("-alpha", value)
        except Exception:
            pass

    # -------------------------------------------------- 事件

    def _bind_events(self):
        c = self.canvas
        for w in (self.root, c):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)

        c.bind("<Button-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Double-Button-1>", lambda e: self.refresh())
        c.bind("<Button-3>", self._on_menu)

        self.menu = tk.Menu(self.root, tearoff=0, bg="#161C26", fg="#DDE5F2",
                            activebackground="#2A3446", activeforeground="#FFFFFF",
                            bd=0, font=("Microsoft YaHei UI", 9))
        self.menu.add_command(label="立即刷新", command=self.refresh)
        self.menu.add_command(label="重置位置", command=self._reset_pos)
        self.menu.add_command(label="选择 DSH 目录…", command=self._pick_dsh_home)

        alpha_menu = tk.Menu(self.menu, tearoff=0, bg="#161C26", fg="#DDE5F2",
                             activebackground="#2A3446", activeforeground="#FFFFFF",
                             bd=0, font=("Microsoft YaHei UI", 9))
        for pct in (40, 55, 70, 85):
            alpha_menu.add_command(
                label="%d%%" % pct,
                command=lambda p=pct: self._set_idle_alpha(p / 100.0),
            )
        self.menu.add_cascade(label="淡出透明度", menu=alpha_menu)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self._quit)

    def _on_enter(self, _=None):
        if self._idle_job:
            self.root.after_cancel(self._idle_job)
            self._idle_job = None
        self.set_alpha(self.cfg["hover_alpha"])

    def _on_leave(self, _=None):
        if self._idle_job:
            self.root.after_cancel(self._idle_job)
        self._idle_job = self.root.after(70, self._maybe_idle)

    def _maybe_idle(self):
        self._idle_job = None
        try:
            px, py = self.root.winfo_pointerxy()
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            if x <= px < x + w and y <= py < y + h:
                return
        except Exception:
            pass
        self.set_alpha(self.cfg["idle_alpha"])

    def _on_press(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _on_drag(self, e):
        if not self._drag:
            return
        x = e.x_root - self._drag[0]
        y = e.y_root - self._drag[1]
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, min(x, sw - WIN_W))
        y = max(0, min(y, sh - WIN_H))
        self.root.geometry("+%d+%d" % (x, y))

    def _on_release(self, _=None):
        if self._drag:
            self.cfg["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
            save_config(self.cfg)
        self._drag = None

    def _on_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _reset_pos(self):
        self.cfg["pos"] = None
        save_config(self.cfg)
        self.root.geometry("+%d+%d" % self._initial_pos())

    def _set_idle_alpha(self, v):
        self.cfg["idle_alpha"] = v
        save_config(self.cfg)
        self._maybe_idle()

    def _pick_dsh_home(self):
        """手动指定 dsh 目录（自动探测失败时用）。"""
        try:
            chosen = filedialog.askdirectory(
                title="选择 dsh-home 目录（里面有 .credentials.yaml）"
            )
        except Exception:
            chosen = None
        if not chosen:
            return
        path = Path(chosen)
        if not (path / CREDENTIAL_FILE).is_file():
            messagebox.showwarning(
                "没找到登录信息",
                "这个目录里没有 %s。\n\n请选 dsh 的 home 目录，"
                "通常叫 dsh-home。" % CREDENTIAL_FILE,
            )
            return
        self.cfg["dsh_home"] = str(path)
        save_config(self.cfg)
        self.dsh_home = path
        self.error = None
        self.refresh()

    def _quit(self):
        self.cfg["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
        save_config(self.cfg)
        self.root.destroy()

    # -------------------------------------------------- 绘制

    def _paint_static(self):
        cv = self.canvas
        cv.delete("all")
        d = self.data
        # 余额见底时用暗红描边做全局警示——半透明状态下也一眼能看出来
        low = bool(d) and (d.get("total") or 0) < LOW_BALANCE_DANGER
        border = C_EDGE_ALERT if low else C_BORDER
        rounded = self.cfg.get("rounded", True)
        if rounded:
            round_rect(cv, *CARD, CARD_R, fill=C_BG, outline=border, width=1)
        else:
            cv.create_rectangle(*CARD, fill=C_BG, outline=border, width=1)

        self.dot = cv.create_oval(16, 18, 23, 25, fill=C_OFFLINE, outline="")
        cv.create_text(32, 22, text="DEEPSEEK 余额", anchor="w", fill=C_TITLE,
                       font=("Segoe UI Semibold", 9))
        self.foot = cv.create_text(WIN_W / 2, 106, text="", fill=C_FOOT,
                                   font=("Microsoft YaHei UI", 8))

    def _paint_data(self):
        cv = self.canvas
        cv.delete("dyn")
        d = self.data
        currency = (d or {}).get("currency") or "CNY"

        if d:
            cv.itemconfigure(self.dot, fill=balance_color(d.get("total", 0)))
        else:
            cv.itemconfigure(self.dot, fill=C_OFFLINE)

        # 币种徽章
        tw = max(28, len(currency) * 6 + 12)
        round_rect(cv, 240 - tw, 15, 240, 29, 7, fill=C_BRAND, outline="", tags="dyn")
        cv.create_text(240 - tw / 2, 22, text=currency, fill="#FFFFFF",
                       font=("Segoe UI Semibold", 7), tags="dyn")

        if not d:
            cv.create_text(20, 52, text="--", anchor="w", fill=C_DIM,
                           font=("Segoe UI Semibold", 21), tags="dyn")
            cv.create_text(20, 80, text="正在读取余额…", anchor="w", fill=C_DIM,
                           font=("Microsoft YaHei UI", 8), tags="dyn")
            self._paint_foot()
            return

        total = d.get("total", 0.0)
        cv.create_text(20, 52, text=fmt_money(total, currency), anchor="w",
                       fill=balance_color(total),
                       font=("Segoe UI Semibold", 21), tags="dyn")
        cv.create_text(
            20, 80,
            text="充值 %s · 赠金 %s" % (
                fmt_money(d.get("normal", 0), currency),
                fmt_money(d.get("bonus", 0), currency),
            ),
            anchor="w", fill=C_DIM, font=("Microsoft YaHei UI", 8), tags="dyn",
        )
        self._paint_foot()

    def _paint_foot(self):
        if not getattr(self, "foot", None):
            return
        d = self.data
        color = C_FOOT
        currency = (d or {}).get("currency") or "CNY"

        if self.error and not d:
            text = self.error
        elif self.error:
            text = "%s · 上次数据" % self.error
        elif not d:
            text = "正在读取余额…"
        else:
            text = "累计消费 %s · 更新 %s" % (
                fmt_money(d.get("spent", 0), currency),
                fmt_clock(d.get("fetched_at", 0)),
            )

        # 数据陈旧时明确标出来：宁可说「我看到的还是旧数据」，
        # 也不能把几小时前的余额当成实时余额摆在用户面前。
        if d:
            age = time.time() - (d.get("fetched_at") or 0)
            secs = max(15, int(self.cfg.get("refresh_seconds", 60)))
            if age > max(180, secs * 3):
                text = "⚠ 数据 %s未更新" % fmt_ago(age)
                color = C_WARN

        self.canvas.itemconfigure(self.foot, text=text, fill=color)

    # -------------------------------------------------- 刷新

    def refresh(self):
        if self.fetching:
            # 防卡死保护：上一次 fetching 挂了超过 120 秒还没复位就强制放行
            if time.time() - self._fetch_started > 120:
                self.fetching = False
            else:
                return
        self.fetching = True
        self._fetch_started = time.time()
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        """子线程：只做网络和解析，结果丢进队列，绝不碰 tkinter。"""
        try:
            data = fetch_balance(self.dsh_home)
            self._q.put(("data", data))
        except BalanceError as e:
            self._q.put(("error", str(e)))
        except Exception:
            self._q.put(("error", "读取失败"))

    def _drain(self):
        """主线程：把子线程攒下的结果一次性处理掉。由 _tick 每秒调用。"""
        while True:
            try:
                kind, payload = self._q.get_nowait()
            except queue.Empty:
                return
            try:
                if kind == "data":
                    self._on_data(payload)
                elif kind == "error":
                    self._on_error(payload)
                elif kind == "show":
                    self._force_show()
            except Exception:
                pass

    def _force_show(self):
        """被第二次启动唤起时：把已经开着的窗口提到最前，别让用户以为没反应。"""
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass
        self.refresh()

    def _on_data(self, data):
        self.fetching = False
        self.error = None
        self.data = data
        save_cache(data)
        self._paint_static()
        self._paint_data()

    def _on_error(self, msg):
        self.fetching = False
        self.error = msg
        if self.data:
            self._paint_foot()
        else:
            self._paint_static()
            self._paint_data()

    def _schedule_refresh(self):
        secs = max(15, int(self.cfg.get("refresh_seconds", 60)))
        self._refresh_job = self.root.after(secs * 1000, self._auto_refresh)

    def _auto_refresh(self):
        self._last_auto = time.time()
        try:
            self.refresh()
        finally:
            # 无论如何都要把下一次排上，否则一次异常就让刷新永久停摆
            self._schedule_refresh()

    def _tick(self):
        """每秒一次：先收子线程结果，再刷倒计时文字，最后兜底自愈。"""
        try:
            self._drain()
        except Exception:
            pass
        try:
            if self.data:
                self._paint_foot()
        except Exception:
            pass
        self._watchdog()
        self._tick_job = self.root.after(1000, self._tick)

    def _watchdog(self):
        """兜底自愈：定时器链断了就重新接上，fetching 卡死了就放掉。"""
        try:
            secs = max(15, int(self.cfg.get("refresh_seconds", 60)))
            now = time.time()
            if now - self._last_auto > secs * 3 + 30:
                try:
                    if self._refresh_job:
                        self.root.after_cancel(self._refresh_job)
                except Exception:
                    pass
                self._last_auto = now
                self.refresh()
                self._schedule_refresh()
            if self.fetching and now - self._fetch_started > 120:
                self.fetching = False
        except Exception:
            pass


# ---------------------------------------------------------------- 入口


def _single_instance() -> bool:
    """True = 本进程是主实例；False = 已有实例（已顺手通知它显示出来）。"""
    global _LOCK_SOCKET
    try:
        _LOCK_SOCKET = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _LOCK_SOCKET.bind(("127.0.0.1", 49732))
        _LOCK_SOCKET.listen(1)
        return True
    except OSError:
        try:
            conn = socket.create_connection(("127.0.0.1", 49732), timeout=2)
            conn.sendall(b"show")
            conn.close()
        except Exception:
            pass
        return False


def _serve_show_requests(gauge: "Gauge") -> None:
    """主实例监听「把我叫出来」的请求。"""

    def loop():
        while True:
            try:
                conn, _ = _LOCK_SOCKET.accept()
                data = conn.recv(32)
                conn.close()
                if data.strip() == b"show":
                    gauge._q.put(("show", None))
            except Exception:
                break

    threading.Thread(target=loop, daemon=True).start()


def main():
    if not _single_instance():
        return
    cfg = load_config()
    root = tk.Tk()
    gauge = Gauge(root, cfg)
    _serve_show_requests(gauge)
    root.mainloop()


if __name__ == "__main__":
    main()
