"""焦点自检：启动浮窗前后，前台窗口必须不变。

这是主人最在意的一条（玩游戏时被抢焦点会很糟），所以用客观测量而不是"看起来没事"。
运行： python test_focus.py
"""
import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PY = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Python", "Python312", "pythonw.exe")
# 必须规范化成绝对路径：带 ".." 的路径不会出现在进程命令行里，匹配会永远失败
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SRC = os.path.join(ROOT, "src")
u = ctypes.windll.user32


def kill_bullet():
    """只清弹幕姬自己的进程（按命令行里含本项目路径认）。"""
    import subprocess as sp
    root = ROOT
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{root}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    sp.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)


def fg():
    return u.GetForegroundWindow()


def pid_of(hwnd):
    if not hwnd:
        return 0
    pid = ctypes.wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def title(hwnd):
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value[:40]


def probe(name, script, extra_env=None):
    env = dict(os.environ)
    env.update({"BULLET_ALWAYS_SHOW": "1", "BULLET_PORT": "9", "BULLET_STATE": ""})
    env.pop("BULLET_STATE", None)
    if extra_env:
        env.update(extra_env)

    before = fg()
    p = subprocess.Popen([PY, os.path.join(SRC, script)], env=env)
    time.sleep(2.5)
    after = fg()
    alive = p.poll() is None
    p.terminate()

    if not alive:
        # 进程秒退（通常是单实例锁被占）⇒ 窗口根本没显示，这一轮不算数
        print(f"[{name}] 进程存活=False ⇒ 本次无效（被互斥体挡掉了？）")
        return False

    # 判据：前台是不是**变成了我们自己的窗口**。
    # 只比"before != after"会把主人自己切窗口算成我们的锅，测不准。
    stolen = pid_of(after) == p.pid
    print(f"[{name}] 启动前={before} ({title(before)!r})")
    print(f"[{name}] 启动后={after} ({title(after)!r})  前台进程={pid_of(after)} 我们={p.pid}")
    print(f"[{name}] 是否被我们抢走前台={'是 ❌' if stolen else '否 ✅'}")
    return not stolen


def wait_clear(timeout=5.0):
    """等旧实例真的退干净：否则新实例会被单实例互斥体挡掉，测出来是假通过。"""
    end = time.time() + timeout
    while time.time() < end:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*{ROOT}*' }} | "
             "Measure-Object | Select-Object -ExpandProperty Count"],
            capture_output=True, text=True,
        )
        if out.stdout.strip() in ("0", ""):
            return True
        time.sleep(0.3)
    return False


kill_bullet()
wait_clear()

ok_overlay = probe("overlay ", "overlay.py")
kill_bullet(); wait_clear()
ok_input = probe("inputbar", "inputbar.py")
kill_bullet()

print()
if ok_overlay and ok_input:
    print("test_focus: OK — 两层都不会抢焦点")
else:
    print("test_focus: FAIL — 抢焦点，或本次测量无效")
    sys.exit(1)
