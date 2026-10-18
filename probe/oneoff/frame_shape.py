"""测：session.v4.jsonl.zstd 是「一帧到底」还是「每次 flush 一帧」。

这决定外置轮询能不能只解压尾部增量（便宜），还是每次都从头解压（贵）。
"""
import glob
import os
import sys
import zstandard

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOME = os.path.expanduser("~")
cands = glob.glob(os.path.join(HOME, ".dsh", "sessions", "**", "session.v4.jsonl.zstd"), recursive=True)
cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
data = open(cands[0], "rb").read()
print(f"file: {os.path.basename(os.path.dirname(cands[0]))}")
print(f"bytes: {len(data)}")

# 逐帧解析：每帧读 magic + 头部长度 + 内容，记录边界
off = 0
frames = []
MAGIC = 0xFD2FB528
while off + 8 <= len(data):
    magic = int.from_bytes(data[off:off + 4], "little")
    if magic != MAGIC:
        # 可能是 skippable frame 或损坏，停下
        print(f"non-zstd-magic at offset {off}: {data[off:off+4].hex()}")
        break
    fhd = data[off + 4]
    single_segment = (fhd >> 5) & 1
    has_checksum = (fhd >> 2) & 1
    dict_id_flag = fhd & 3
    fcs_flag = (fhd >> 6) & 3
    pos = off + 5
    if not single_segment:
        pos += 1  # window descriptor
    pos += [0, 1, 2, 4][dict_id_flag]
    pos += [1 if single_segment else 0, 2, 4, 8][fcs_flag]
    # 找到该帧结束：用 decompressobj 一次性解，拿到 unused_data 推断消耗量
    d = zstandard.ZstdDecompressor().decompressobj()
    out = d.decompress(data[off:])
    consumed = len(data) - off - len(d.unused_data)
    frames.append((off, consumed, len(out)))
    off += consumed
    if consumed <= 0:
        break

print(f"frames: {len(frames)}")
for i, (o, c, n) in enumerate(frames[:6]):
    print(f"  frame {i}: offset={o:>8} compressed={c:>7} decompressed={n:>7}")
if len(frames) > 6:
    print(f"  ... last: offset={frames[-1][0]:>8} compressed={frames[-1][1]:>7} decompressed={frames[-1][2]:>7}")
