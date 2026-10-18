"""DSH 弹幕姬 —— 浮窗进程（阶段 1：只读可视版）

透明 / 置顶 / 鼠标穿透 / 不进截图，读 ~/.dsh/bullet/state.json 渲染 agent 状态。
阶段 2 会把下半部分做成可交互的输入条（WPF），此文件只负责"看得见"。

单独测试：  python overlay.py            # 读 ~/.dsh/bullet/state.json
           BULLET_STATE=xxx.json python overlay.py
"""
import ctypes
import json
import os
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass  # pythonw 下 sys.stdout 是 None，别在这里崩


def log(msg):
    """pythonw 无控制台，日志一律落文件。"""
    try:
        os.makedirs(DIR, exist_ok=True)
        with open(os.path.join(DIR, "overlay.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass

HOME = os.path.expanduser("~")
DIR = os.path.join(HOME, ".dsh", "bullet")
STATE_PATH = os.environ.get("BULLET_STATE") or os.path.join(DIR, "state.json")
POS_PATH = os.path.join(DIR, "overlay-pos.json")
GEOM_PATH = os.path.join(DIR, "overlay-geom.json")   # 让输入条贴着弹幕区下方

TRANSPARENT = "#010203"          # 这个颜色 → 完全透明 + 穿透
FG = "#ffffff"
DIM = "#aab4c4"
ACCENT = "#5aa9ff"
DOT = {"thinking": "#5aa9ff", "tool": "#ffb02d", "done": "#3ddc84", "idle": "#5a6270"}

W, PAD = 460, 12
HEAD_H = 34                      # 首行行高：要给"思考中"和下面一行留出明显空档
LINE_H = 24                      # 正文行高
ICON_X = PAD + 4                 # 图标列左边缘：所有图标一律左对齐到这条线
TEXT_X = PAD + 40                # 正文列左边缘（首行状态词也从这里起，两列各自对齐）
# ⚒ 是 emoji，字形本身在宽度里偏右，单给一点负偏移才跟「思/答」对得上
ICON_NUDGE = {"⚒": -4}


def _alpha():
    """透明度优先级：环境变量 > config.json > 1.0。

    默认 1.0 = 整窗不透明，背景靠色键抠成全透明，只有文字可见（弹幕字幕层）。
    调小会让文字一起变淡，一般不需要动。
    """
    env = os.environ.get("BULLET_ALPHA")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    cfg = read_json(os.path.join(DIR, "config.json"), {}) or {}
    try:
        return float(cfg.get("alpha", 1.0))
    except (TypeError, ValueError):
        return 1.0

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def set_dpi_aware():
    u = ctypes.windll.user32
    try:
        if u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


_MUTEX = None   # 必须保留引用，否则句柄被回收、锁就没了


def single_instance(name):
    """拿一个具名互斥体；已有实例在跑就返回 False。

    插件那边任何"以为没起来"的判断失误，都不会再堆出一排重复窗口。
    """
    global _MUTEX
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True
    if kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
        return False
    _MUTEX = handle
    return True


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def one_line(s, n=54):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def fit(text, font, max_px):
    """按像素宽度截断并加省略号 —— 中英文混排时按字符数截断会溢出。"""
    s = " ".join(str(text or "").split())
    if not s or font.measure(s) <= max_px:
        return s
    ell = "…"
    ew = font.measure(ell)
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(s[:mid]) + ew <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo] + ell


class Overlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.configure(bg=TRANSPARENT)
        # 半透明：面板能透出底下的内容，但底色够深所以文字仍清晰。
        # （tkinter 没有 per-pixel alpha，只能整窗调 -alpha；实测 0.86 是清晰度与通透度的平衡点）
        try:
            self.root.attributes("-alpha", _alpha())
        except Exception:
            pass

        self.f_status = tkfont.Font(family="Microsoft YaHei UI", size=11, weight="bold")
        # 全透明背景上字要够"实"，粗一点比描边更管用
        self.f_body = tkfont.Font(family="Microsoft YaHei UI", size=10, weight="bold")
        self.f_mono = tkfont.Font(family="Consolas", size=10, weight="bold")

        self.canvas = tk.Canvas(self.root, bg=TRANSPARENT, highlightthickness=0,
                                width=W, height=110)
        self.canvas.pack(fill="both", expand=True)

        pos = read_json(POS_PATH, {}) or {}
        self.pos_x = int(pos.get("x", 60))
        self.pos_y = int(pos.get("y", 60))
        self.root.geometry(f"{W}x110+{self.pos_x}+{self.pos_y}")

        self.canvas.update_idletasks()
        self._make_click_through()

        self.last_sig = None
        self.visible = False
        self.dragging = False
        self.frames = 0
        self.pulse = 0
        self.cur_h = 110
        self.cur_geom = None
        self._bind_drag()
        self.root.after(120, self.tick)

    # ── 窗口属性 ────────────────────────────────────────────────
    def _hwnd(self):
        # GA_ROOT：真正的顶层窗口；对 canvas 取父窗口会让属性静默失效
        return ctypes.windll.user32.GetAncestor(self.canvas.winfo_id(), 2)

    def _icon(self, y, glyph, color):
        """图标列：统一左对齐，个别字形自带偏移的按 ICON_NUDGE 微调。"""
        self._text(ICON_X + ICON_NUDGE.get(glyph, 0), y, glyph, color, self.f_body)

    def _text(self, x, y, text, fill, font, anchor="nw"):
        """一层淡阴影就够。

        色键透明下，多方向描边会和抗锯齿边缘叠加成"重影"，反而更糊；
        单层右下浅阴影既能压在浅底上看清，又不会发虚。
        """
        c = self.canvas
        c.create_text(x + 1, y + 1, text=text, fill="#000000", font=font, anchor=anchor)
        c.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor)

    def _make_click_through(self):
        u = ctypes.windll.user32
        hwnd = self._hwnd()
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        if os.environ.get("BULLET_CAPTURABLE") != "1":
            # 让 agent 自己的截图拍不到本窗口，避免污染它的视觉输入
            if not u.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
                log("warn: display affinity failed; overlay may appear in captures")

    def _bind_drag(self):
        # 穿透窗口默认不能拖；按住 Ctrl 时由 Alt+拖动 移动（穿透无法收鼠标事件，
        # 所以改用命令行/方向键移动，见 key handler）
        self.root.bind_all("<Control-Alt-Left>", lambda e: self.nudge(-10, 0))
        self.root.bind_all("<Control-Alt-Right>", lambda e: self.nudge(10, 0))
        self.root.bind_all("<Control-Alt-Up>", lambda e: self.nudge(0, -10))
        self.root.bind_all("<Control-Alt-Down>", lambda e: self.nudge(0, 10))

    def nudge(self, dx, dy):
        self.pos_x += dx
        self.pos_y += dy
        self.root.geometry(f"+{self.pos_x}+{self.pos_y}")
        write_json(POS_PATH, {"x": self.pos_x, "y": self.pos_y})
        self.cur_geom = None      # 让下一帧把新位置写给输入条

    # ── 渲染 ────────────────────────────────────────────────────
    def tick(self):
        st = read_json(STATE_PATH, {}) or {}
        status = st.get("status") or "idle"
        active = status in ("thinking", "tool")
        if active:
            self.frames += 1
            # 每 ~360ms 推一格，呼吸点才有"在动"的感觉
            if self.frames % 3 == 0:
                self.pulse = (self.pulse + 1) % 4
        # since 是毫秒时间戳；脏数据/0 时按"不计时"处理，别显示天文数字
        since = float(st.get("since") or 0)
        elapsed = 0
        if active and since > 1_600_000_000_000:
            elapsed = max(0, int((time.time() * 1000 - since) / 1000))
        notice_on = (time.time() * 1000 - float(st.get("noticeAt") or 0)) < 2500 and st.get("notice")
        sig = (status, st.get("thought"), st.get("said"), st.get("tool"),
               st.get("toolDetail"), st.get("step"), st.get("title"), st.get("seq"),
               elapsed if active else 0, self.pulse if active else 0,
               st.get("notice") if notice_on else "")
        if sig != self.last_sig:
            self.last_sig = sig
            self.render(st, elapsed, st.get("notice") if notice_on else "")
        self.root.after(120, self.tick)

    def render(self, st, elapsed=0, notice=""):
        c = self.canvas
        status = st.get("status") or "idle"
        if status == "idle":
            if self.visible:
                ctypes.windll.user32.ShowWindow(self._hwnd(), 0)   # SW_HIDE
                self.visible = False
            return
        if not self.visible:
            # SW_SHOWNOACTIVATE：显示但不激活，避免把焦点从 DSH 输入框抢走
            ctypes.windll.user32.ShowWindow(self._hwnd(), 4)
            self.visible = True

        c.delete("all")

        has_thought = bool(st.get("thought"))
        has_said = bool(st.get("said"))
        has_tool = bool(st.get("tool"))
        # 高度固定成"最多四行"：内容一行行出现时面板不跟着长高，
        # 否则下面的输入条会被顶着上下跳（这就是"抽搐"的另一个来源）。
        h = PAD * 2 + HEAD_H + 3 * LINE_H

        # 只有高度真的变了才重设几何；每帧都设会让下面的输入条跟着抖
        if h != self.cur_h:
            self.cur_h = h
            self.root.geometry(f"{W}x{h}")

        # 几何文件也只在变化时写（呼吸点每秒重绘几次，不能每次都落盘）
        geom = (self.pos_x, self.pos_y, W, h)
        if geom != self.cur_geom:
            self.cur_geom = geom
            write_json(GEOM_PATH, {"x": self.pos_x, "y": self.pos_y, "w": W, "h": h})

        # 背景完全透明：只留文字飘在屏幕上，不遮挡主人要点的东西
        # （窗口本身是点击穿透的，所以这里要解决的只是"看见"）
        inner = W - TEXT_X - PAD          # 可用文字宽度
        y = PAD

        # 第一行：状态词（与图标列左对齐）+ 呼吸点 + 计时 + 右侧（对话名 · 步数）
        label = {"thinking": "思考中", "tool": "执行工具", "done": "完成"}.get(status, status)
        head = label
        if elapsed > 0:
            head += f"  {elapsed}s"
        if notice:
            head += f"   ({notice})"

        # 状态词跟「思/⚒/答」同列起始 —— 主人要的就是这条左边缘对齐
        self._text(ICON_X, y + 3, head, FG, self.f_status)
        head_w = self.f_status.measure(head)

        # 呼吸点跟在状态词后面：不占图标的位，又能看出"它在动"
        color = DOT.get(status, ACCENT)
        cx = ICON_X + head_w + 14
        cy = y + 5 + self.f_status.metrics("ascent") // 2
        if status in ("thinking", "tool"):
            r = [3, 4, 5, 4][self.pulse]
            c.create_oval(cx - r, cy - r, cx + r, cy + r,
                          fill=color, outline="#000000", width=1)
        else:
            c.create_oval(cx - 4, cy - 4, cx + 4, cy + 4,
                          fill=color, outline="#000000", width=1)

        right = []
        if st.get("title"):
            right.append(str(st["title"]))
        if st.get("step"):
            right.append(f"step {st['step']}")
        if right:
            avail = inner - head_w - 40
            rt = fit("  ·  ".join(right), self.f_body, max(60, avail))
            self._text(W - PAD, y + 3, rt, DIM, self.f_body, anchor="ne")

        y += HEAD_H

        if has_thought:
            self._icon(y, "思", ACCENT)
            self._text(TEXT_X, y, fit(st["thought"], self.f_body, inner), FG, self.f_body)
            y += LINE_H
        if has_tool:
            self._icon(y, "⚒", DOT["tool"])
            txt = str(st["tool"] or "")
            if st.get("toolDetail"):
                txt += "  " + str(st["toolDetail"])
            self._text(TEXT_X, y, fit(txt, self.f_mono, inner), "#e2e8f2", self.f_mono)
            y += LINE_H
        if has_said:
            self._icon(y, "答", DIM)
            self._text(TEXT_X, y, fit(st["said"], self.f_body, inner), "#e8edf6", self.f_body)


if __name__ == "__main__":
    if not single_instance("Local\\DSH_Bullet_Overlay"):
        sys.exit(0)          # 已经有一个在上半区了
    set_dpi_aware()
    log(f"start state={STATE_PATH} alpha={_alpha()}")
    try:
        Overlay().root.mainloop()
    except Exception as exc:
        log(f"fatal: {type(exc).__name__}: {exc}")
        raise
