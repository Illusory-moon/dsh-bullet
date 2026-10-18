"""从会话日志里抠一条 user/message 的完整结构，作为插件造消息的样板。"""
import glob
import json
import os
import sys
import zstandard

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HOME = os.path.expanduser("~")
cands = glob.glob(os.path.join(HOME, ".dsh", "sessions", "**", "session.v4.jsonl.zstd"), recursive=True)
cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
raw = open(cands[0], "rb").read()
out = bytearray()
try:
    with zstandard.ZstdDecompressor().stream_reader(raw, read_across_frames=True) as r:
        while True:
            b = r.read(1 << 16)
            if not b:
                break
            out.extend(b)
except Exception:
    pass

for line in bytes(out).decode("utf-8", "replace").splitlines():
    try:
        o = json.loads(line)
    except Exception:
        continue
    if o.get("type") == "user/message":
        print(json.dumps(o, ensure_ascii=False, indent=2)[:1800])
        break

# 顺带看一个 step/start 和 turn/start 的形状
for want in ("turn/start", "step/start"):
    for line in bytes(out).decode("utf-8", "replace").splitlines():
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") == want:
            print(f"\n--- {want} ---")
            print(json.dumps(o, ensure_ascii=False)[:400])
            break
