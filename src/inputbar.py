"""DSH 弹幕姬 —— 输入条（弹幕姬式分层交互的下半部分）

上半区（overlay.py）整窗穿透、只读；这一层可点可输入，视觉上跟上半区拼成一块。

三条硬规矩（都是踩坑换来的）：
1. **永不主动抢焦点**：常驻 WS_EX_NOACTIVATE，只有主人真的点在它身上才临时放开
2. **鼠标移过来才出现**：不跟任务状态绑定，玩游戏/全屏时不会跳出来挡事
3. **背景全透明**：只留「输入：」+ 文字 + 两个按钮，跟上半区一个风格

命令走 UDP 到插件（端口见 ~/.dsh/bullet/port.txt），单向 fire-and-forget。
- 回车 / [纠正] → steer：插到下一步（改航，不打断当前步）
- [停]        → cancel：立刻中断当前 phase
"""
import ctypes
import ctypes.wintypes
import json
import os
import socket
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = os.path.expanduser("~")
DIR = os.path.join(HOME, ".dsh", "bullet")
STATE_PATH = os.environ.get("BULLET_STATE") or os.path.join(DIR, "state.json")
GEOM_PATH = os.path.join(DIR, "overlay-geom.json")
PORT_PATH = os.path.join(DIR, "port.txt")

TRANSPARENT = "#010203"          # 色键：这个颜色 → 完全透明
W, H = 460, 36
FG = "#ffffff"
DIM = "#c6cedd"
ACCENT = "#5aa9ff"
DANGER = "#ff6b6b"
PREFIX = "输入："

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SWP_NOACTIVATE = 0x0010


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


def log(msg):
    """pythonw 无控制台，出问题只能靠文件。"""
    try:
        with open(os.path.join(DIR, "inputbar.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


_MUTEX = None


def single_instance(name):
    """具名互斥体：已有输入条在跑就退出。插件误判时也不会堆出重复窗口。"""
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


def read_port():
    env = os.environ.get("BULLET_PORT")
    if env:
        try:
            return int(env)
        except ValueError:
            return 0
    try:
        return int(open(PORT_PATH, encoding="utf-8").read().strip())
    except Exception:
        return 0


class InputBar:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.configure(bg=TRANSPARENT)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.attributes("-topmost", True)

        self.f_text = tkfont.Font(family="Microsoft YaHei UI", size=10, weight="bold")
        self.f_btn = tkfont.Font(family="Microsoft YaHei UI", size=10, weight="bold")

        self.canvas = tk.Canvas(self.root, bg=TRANSPARENT, highlightthickness=0,
                                width=W, height=H)
        self.canvas.pack(fill="both", expand=True)

        self.entry = tk.Entry(
            self.root, bg=TRANSPARENT, fg=FG, insertbackground=FG, relief="flat", bd=0,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.entry.bind("<Return>", lambda e: self.send("steer"))
        self.entry.bind("<Escape>", lambda e: self.release_focus())

        self.canvas.update_idletasks()
        self.hwnd = ctypes.windll.user32.GetAncestor(self.canvas.winfo_id(), 2)
        self._style_toolwindow()
        self._set_noactivate(True)
        ctypes.windll.user32.ShowWindow(self.hwnd, SW_HIDE)

        self.visible = False
        self.cur_geom = None
        self.interactive = False        # 主人点进来过，才允许输入
        self.hint = ""
        self.hint_until = 0.0
        self.last_empty = None
        self.canvas.bind("<Button-1>", self._on_click)
        self.draw()
        self.root.after(120, self.tick)

    # ── 窗口属性 ────────────────────────────────────────────────
    def _style_toolwindow(self):
        u = ctypes.windll.user32
        u.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                         u.GetWindowLongW(self.hwnd, GWL_EXSTYLE) | WS_EX_TOOLWINDOW)

    def _set_noactivate(self, on):
        """NOACTIVATE = 系统层面保证"显示/置顶都不会抢焦点"。"""
        u = ctypes.windll.user32
        style = u.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_NOACTIVATE) if on else (style & ~WS_EX_NOACTIVATE)
        u.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style)

    def release_focus(self):
        """退出输入态：重新上锁，不主动挪焦点（主动设焦点反而会打扰别的窗口）。"""
        self.interactive = False
        self._set_noactivate(True)
        self.draw()

    # ── 位置 / 显隐 ─────────────────────────────────────────────
    def _target_rect(self):
        g = read_json(GEOM_PATH, {}) or {}
        x = int(g.get("x", 60))
        w = int(g.get("w", W))
        top = int(g.get("y", 60))
        y = top + int(g.get("h", 120)) + 2
        # 用 win32 取屏幕高度：窗口还没映射时 Tk 的 winfo_screenheight() 会返回 0，
        # 会把"贴底翻上去"误判成永远成立，窗口就跑到屏幕顶部去了
        screen_h = ctypes.windll.user32.GetSystemMetrics(1) or 1080
        if y + H > screen_h - 40:
            y = top - H - 2
        return (x, max(0, y), w)

    def place(self):
        target = self._target_rect()
        if target == self.cur_geom:
            return
        self.cur_geom = target
        # 窗口已显示时才用 Tk 定位：SW_HIDE 状态下 Tk 的 geometry 请求不会落地，
        # 而 SetWindowPos 在 Tk 的 overrideredirect 窗口上会失败 —— 所以先 show 再 place。
        self.root.geometry(f"{target[2]}x{H}+{target[0]}+{target[1]}")

    def _cursor_inside(self):
        u = ctypes.windll.user32
        pt = ctypes.wintypes.POINT()
        if not u.GetCursorPos(ctypes.byref(pt)):
            return False
        x, y, w = self.cur_geom or self._target_rect()
        # 上半区 + 本条一起算作"热区"，否则鼠标在对勾中间移动就会闪
        g = read_json(GEOM_PATH, {}) or {}
        top = int(g.get("y", y - 120))
        return (x - 6) <= pt.x <= (x + w + 6) and (top - 6) <= pt.y <= (y + H + 6)

    def show(self):
        if self.visible:
            return
        ctypes.windll.user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self.visible = True
        self.cur_geom = None      # 显示后强制重新落位（隐藏时的 geometry 不生效）

    def hide(self):
        if not self.visible:
            return
        ctypes.windll.user32.ShowWindow(self.hwnd, SW_HIDE)
        self.visible = False

    # ── 主循环 ──────────────────────────────────────────────────
    def _entry_text(self):
        try:
            return self.entry.get().strip()
        except Exception:
            return ""

    def tick(self):
        force = os.environ.get("BULLET_ALWAYS_SHOW") == "1"   # 调试/截图用
        if force or self._cursor_inside() or self.interactive:
            self.show()
            self.place()          # 必须在 show 之后：隐藏时的定位不生效
        elif self.visible:
            self.hide()
        # 输入框空/非空切换时重画占位提示
        empty = self._entry_text() == ""
        if empty != self.last_empty:
            self.last_empty = empty
            self.draw()
        if self.hint and time.time() > self.hint_until:
            self.hint = ""
            self.draw()
        self.root.after(120, self.tick)

    def draw(self):
        c = self.canvas
        c.delete("all")

        # 「输入：」前缀 —— 透明背景下不看这句就认不出这是输入框
        self._text(8, H // 2, PREFIX, FG, self.f_text, anchor="w")

        # 输入区左边界跟着前缀宽度走
        self.entry_x = 8 + self.f_text.measure(PREFIX) + 6
        btn_left = W - 12 - (self.f_btn.measure("纠正") + 16) - 10 - 6 - (self.f_btn.measure("停") + 16)
        self.entry.place(x=self.entry_x, y=9, height=H - 18,
                         width=max(60, btn_left - self.entry_x - 12))

        # 一条极细下划线：唯一"不遮挡但能认出是输入框"的手段
        self.canvas.create_line(self.entry_x - 2, H - 7, btn_left - 12, H - 7,
                                fill="#7b8798", width=1)

        # 右侧按钮
        self._button(btn_left, H // 2, "停", DANGER, "cancel")
        self._button(W - 12 - (self.f_btn.measure("纠正") + 16), H // 2,
                     "纠正", ACCENT, "steer")

        if self.hint:
            self._text(W - 4, 4, self.hint, DIM, self.f_btn, anchor="ne")

    def _text(self, x, y, text, fill, font, anchor="w"):
        # 与上半区一致的淡阴影：透明背景上压白字要有个底
        self.canvas.create_text(x + 1, y + 1, text=text, fill="#000000",
                                font=font, anchor=anchor)
        self.canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor)

    def _button(self, x0, y, label, color, cmd):
        wid = self.f_btn.measure(label) + 16
        tag = f"btn-{cmd}"
        self.canvas.create_text(x0 + 1, y + 1, text=label, fill="#000000",
                                font=self.f_btn, anchor="w", tags=tag)
        self.canvas.create_text(x0, y, text=label, fill=color,
                                font=self.f_btn, anchor="w", tags=tag)
        # 加宽命中区，别让人非得点准笔画
        self.canvas.create_rectangle(x0 - 6, y - 12, x0 + wid, y + 12,
                                     fill="", outline="", tags=(tag, "hit"))
        self.canvas.tag_bind(tag, "<Button-1>", lambda e, k=cmd: self.send(k))

    # ── 交互 ────────────────────────────────────────────────────
    def _on_click(self, event):
        """只有主人真的点上来，才放开 NOACTIVATE 并夺取焦点。"""
        if not self.interactive:
            self.interactive = True
            self._set_noactivate(False)
        ctypes.windll.user32.SetForegroundWindow(self.hwnd)
        self.entry.focus_force()

    # ── 发命令 ──────────────────────────────────────────────────
    def send(self, cmd):
        port = read_port()
        text = self.entry.get().strip()
        if cmd != "cancel" and text == "":
            self.flash("说点什么呢")
            return
        if port == 0:
            log("send failed: no port (插件未就绪)")
            self.flash("插件未就绪")
            return
        payload = json.dumps({"cmd": cmd, "text": text}, ensure_ascii=False)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(payload.encode("utf-8"), ("127.0.0.1", port))
            s.close()
        except Exception as exc:
            log(f"send failed: {type(exc).__name__}: {exc}")
            self.flash(f"发送失败 {type(exc).__name__}")
            return
        log(f"sent cmd={cmd} len={len(text)}")
        if cmd != "cancel":
            self.entry.delete(0, "end")
        self.flash({"steer": "已插入下一步", "cancel": "已请求停止"}.get(cmd, "已发送"))
        # 发完等一会儿再放手，方便连续追问；期间不主动挪焦点
        self.root.after(4000, self.release_focus)

    def flash(self, text):
        self.hint = text
        self.hint_until = time.time() + 2.5
        self.draw()


if __name__ == "__main__":
    if not single_instance("Local\\DSH_Bullet_InputBar"):
        sys.exit(0)          # 已经有一个输入条在跑
    set_dpi_aware()
    log(f"start port={read_port()}")
    try:
        InputBar().root.mainloop()
    except Exception as exc:
        log(f"fatal: {type(exc).__name__}: {exc}")
        raise
