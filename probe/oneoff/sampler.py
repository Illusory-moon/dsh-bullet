"""后台采样 state.json，看思考文本是逐字增长还是整块落地。"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
D = os.path.join(os.path.expanduser("~"), ".dsh", "bullet")
STATE = os.path.join(D, "state.json")
OUT = os.path.join(D, "sampler.log")

duration = float(sys.argv[1]) if len(sys.argv) > 1 else 150.0
interval = 0.08
end = time.time() + duration
last = None

with open(OUT, "w", encoding="utf-8") as f:
    while time.time() < end:
        try:
            with open(STATE, encoding="utf-8") as fh:
                st = json.load(fh)
        except Exception:
            time.sleep(interval)
            continue
        sig = (st.get("status"), st.get("thought"), st.get("said"), st.get("tool"), st.get("step"))
        if sig != last:
            last = sig
            row = {
                "t": round(time.time() - (end - duration), 2),
                "status": st.get("status"),
                "step": st.get("step"),
                "tool": st.get("tool"),
                "thoughtLen": len(st.get("thought") or ""),
                "thoughtTail": (st.get("thought") or "")[-30:],
                "saidLen": len(st.get("said") or ""),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
        time.sleep(interval)
print("sampler done")
