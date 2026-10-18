"""P0 探针（只读）：解压会话日志副本，看 DSH 事件帧的真实形状。

不装插件、不重启、不写 DSH 任何状态 —— 只 cp 一份日志出来读。
目的：确认 assistant/live-chunk 究竟带不带「思考 vs 回答」的区分，
以及 tool/call 的字段名（MCP 工具名、参数）。
"""
import glob
import json
import os
import shutil
import sys
import tempfile
import zstandard

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOME = os.path.expanduser("~")
ROOT = os.path.join(HOME, ".dsh", "sessions")

# 1) 找最近被写过的会话日志
cands = glob.glob(os.path.join(ROOT, "**", "session.v4.jsonl.zstd"), recursive=True)
if not cands:
    print("no session log found")
    sys.exit(1)
cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
src = cands[0]
print(f"source: {src}")
print(f"mtime : {__import__('datetime').datetime.fromtimestamp(os.path.getmtime(src))}")
print(f"size  : {os.path.getsize(src)} bytes (compressed)")

# 2) 拷一份再读（绝不动原件）
tmp = os.path.join(tempfile.gettempdir(), "dsh_bullet_probe.jsonl")
shutil.copyfile(src, tmp + ".zst")
raw = open(tmp + ".zst", "rb").read()

# 3) 流式解压：尾部可能抓到半个帧（日志在写），丢掉即可
out = bytearray()
dctx = zstandard.ZstdDecompressor()
try:
    with dctx.stream_reader(raw, read_across_frames=True) as r:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            out.extend(chunk)
except Exception as e:  # 尾部半帧很正常
    print(f"(decompress stopped early: {type(e).__name__}: {e})")

lines = [l for l in bytes(out).decode("utf-8", "replace").splitlines() if l.strip()]
print(f"decompressed lines: {len(lines)}")

events = []
for i, line in enumerate(lines):
    try:
        obj = json.loads(line)
    except Exception:
        continue
    events.append(obj)

# 4) 事件类型分布
counts = {}
for e in events:
    t = e.get("type") or e.get("event") or (e.get("payload") or {}).get("type") or "<none>"
    counts[t] = counts.get(t, 0) + 1
print("\n=== event types ===")
for t, n in sorted(counts.items(), key=lambda kv: -kv[1])[:40]:
    print(f"{n:6d}  {t}")


def brief(v, limit=110):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = s.replace("\n", "\\n")
    return s if len(s) <= limit else s[:limit] + "…"


def shape(v, depth=0):
    """只打印结构，不打印正文。"""
    pad = "  " * depth
    if isinstance(v, dict):
        for k, val in list(v.items())[:14]:
            if isinstance(val, (dict, list)):
                print(f"{pad}{k}:")
                shape(val, depth + 1)
            else:
                print(f"{pad}{k}: {brief(val)}")
    elif isinstance(v, list):
        print(f"{pad}[list len={len(v)}]")
        if v:
            shape(v[0], depth + 1)


# 5) 关键事件的样张（每种只打一个）
WANTED = ["assistant/live-chunk", "assistant/chunk", "assistant/message",
          "assistant/attempt", "tool/call", "tool/result", "session/event"]
print("\n=== samples (structure only) ===")
seen = set()
for e in events:
    t = e.get("type") or e.get("event") or (e.get("payload") or {}).get("type")
    if t in seen or t not in WANTED:
        continue
    seen.add(t)
    print(f"\n--- {t} ---")
    shape(e)
    if len(seen) >= len(WANTED):
        break

# 6) 顶层键一览（ помогает понять обёртку）
print("\n=== top-level keys seen ===")
keys = {}
for e in events:
    for k in e.keys():
        keys[k] = keys.get(k, 0) + 1
for k, n in sorted(keys.items(), key=lambda kv: -kv[1])[:20]:
    print(f"{n:6d}  {k}")

try:
    os.remove(tmp + ".zst")
except OSError:
    pass
