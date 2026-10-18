"""看桥插件 dump 下来的事件样张，重点是 live-chunk 的真实形状。"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
p = os.path.join(os.path.expanduser("~"), ".dsh", "bullet", "debug.jsonl")
rows = []
for line in open(p, encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line:
        continue
    try:
        rows.append(json.loads(line))
    except Exception:
        pass

print(f"{len(rows)} samples\n")
for r in rows:
    print(f"[{r.get('kind')}]")

print("\n=== live-chunk 样张（前 3 个，全量）===")
n = 0
for r in rows:
    if "live" in str(r.get("kind", "")):
        print(json.dumps(r, ensure_ascii=False)[:900])
        print("-" * 60)
        n += 1
        if n >= 3:
            break
