"""输入条 → 插件的命令通道自检（不需要 DSH）。

监听一个 UDP 端口，把输入条实例化后直接调 send()，断言对端收到正确的 JSON。
运行： python test_inputbar.py
"""
import json
import os
import socket
import sys
import threading

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

got = []
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", 0))
PORT = sock.getsockname()[1]


def listen():
    sock.settimeout(5)
    try:
        while len(got) < 3:
            data, _ = sock.recvfrom(65535)
            got.append(json.loads(data.decode("utf-8")))
    except Exception:
        pass


threading.Thread(target=listen, daemon=True).start()
os.environ["BULLET_PORT"] = str(PORT)

import inputbar  # noqa: E402  （必须在设置 env 之后导入）

bar = inputbar.InputBar()
bar.root.withdraw()

bar.entry.insert(0, "停，你点错页面了")
bar.send("steer")
bar.send("cancel")

bar.root.update()
import time  # noqa: E402
time.sleep(0.6)

assert len(got) == 2, f"应收到 2 条命令，实际 {len(got)}"
assert got[0] == {"cmd": "steer", "text": "停，你点错页面了"}, got[0]
assert got[1]["cmd"] == "cancel", got[1]
assert bar.entry.get() == "", "发送后输入框应清空"
bar.root.destroy()

print("test_inputbar: OK — 命令通道与输入框行为正确")
print("received:", json.dumps(got, ensure_ascii=False))
