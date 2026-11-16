# -*- coding: utf-8 -*-
"""探测：打包后的 EXE 音频子进程写进共享环的 PCM **内容**是不是非零。

★ 这是补上一个判据漏洞：原来的 verify_exe_audio.py 只量了"水位"（写入多少
  字节），而**水位满 ≠ 声音非零**——子进程完全可以写满一段全零。
  用户报"完全没声"时，只看水位是发现不了的。
"""
import io
import os
import struct
import subprocess
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")

import numpy as np                                   # noqa: E402
from multiprocessing import shared_memory            # noqa: E402
from 核心 import sonify as SN                        # noqa: E402

EXE = sys.argv[1] if len(sys.argv) > 1 else r"D:\deepseek\release\StellarNexus.exe"
print("被测 EXE：%s（%.1f MB）" % (EXE, os.path.getsize(EXE) / 1048576.0))

shm = shared_memory.SharedMemory(create=True, size=SN.SHM_WORDS * 8)
shm.buf[:] = b"\x00" * len(shm.buf)
sh = np.ndarray((SN.SHM_WORDS,), dtype=np.float64, buffer=shm.buf)
# ★ magic 现在有独立槽位。原来写在字节 8~15，而那正好是标量下标 1 = SH_MASTER，
#   父进程一设音量就把魔数砸成 float(0.55) 的位模式 → 子进程读不到音符
#   （notes=0）→ 合成全零 PCM。这就是"完全没声"的第一个根因。
sh[SN.SH_MAGIC_WORD] = float(SN.SHM_MAGIC)
sh[SN.SH_MASTER] = 0.55
sh[SN.SH_DRONE] = 0.0
sh[SN.SH_STATUS_WORD] = SN.STATUS_STARTING
sh[SN.SH_MELODY] = 0.42
# 给几条音符（不填的话乐器没内容，但和声垫应该还是有声）
for i in range(6):
    struct.pack_into("<fffH", shm.buf, SN.NOTES_OFFSET + i * SN.NOTE_BYTES,
                     SN.pitch_hz(1, i), 0.5, 0.0, 0)
rfd, wfd = os.pipe()
log = open(r"D:\deepseek\.videotmp\child_probe.log", "wb")
env = dict(os.environ)
env.update({"_SNX_AUDIO_CHILD": "1", "_SNX_SHM": shm.name,
            "_SNX_PIPE_FD": str(rfd), "_SNX_STATUS_FD": "-1",
            "_SNX_DEBUG_LOG": r"D:\deepseek\.videotmp\child_probe.log.txt",
            "QT_QPA_PLATFORM": "offscreen", "QT_LOGGING_RULES": "qt.multimedia.*=false",
            "TMP": r"D:\deepseek\.videotmp", "TEMP": r"D:\deepseek\.videotmp"})
p = subprocess.Popen([EXE], env=env, stdin=subprocess.DEVNULL,
                     stdout=log, stderr=subprocess.STDOUT,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
t0 = time.time()
while time.time() - t0 < 25 and sh[SN.SH_STATUS_WORD] == SN.STATUS_STARTING:
    time.sleep(0.2)
print("子进程状态字:", sh[SN.SH_STATUS_WORD], " (1=就绪)")

time.sleep(2.5)
r = int(sh[SN.SH_PCM_R]); w = int(sh[SN.SH_PCM_W])
n = (w - r) % SN.PCM_BYTES
print("环形缓冲水位 %d 字节（%.0f ms）" % (n, n / 4.0 / 44100 * 1000))
if n > 0:
    raw = bytes(shm.buf[SN.PCM_OFFSET + r:SN.PCM_OFFSET + r + min(n, 200000)])
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    print("★ PCM 内容：样本 %d　峰值 %.5f　RMS %.5f" % (len(a), float(np.abs(a).max()),
                                                 float(np.sqrt((a ** 2).mean()))))
    if float(np.abs(a).max()) < 1e-4:
        print("★★★ 全零！子进程在写静音 —— 这就是'完全没声'的原因")
    # 多取几个时刻，看是不是一直零
    zeros = 0
    for k in range(8):
        time.sleep(0.15)
        r2 = int(sh[SN.SH_PCM_R]); w2 = int(sh[SN.SH_PCM_W])
        n2 = (w2 - r2) % SN.PCM_BYTES
        if n2 > 4000:
            seg = np.frombuffer(bytes(shm.buf[SN.PCM_OFFSET + r2:SN.PCM_OFFSET + r2 + 4000]),
                                dtype="<i2").astype(np.float32) / 32768.0
            if float(np.abs(seg).max()) < 1e-4:
                zeros += 1
    print("   后续 8 次采样里全零的次数:", zeros)
p.kill()
try:
    p.wait(timeout=10)
except Exception:
    pass
print()
print("--- 子进程日志 ---")
for f in (r"D:\deepseek\.videotmp\child_probe.log.txt",
          r"D:\deepseek\.videotmp\child_probe.log"):
    if os.path.isfile(f):
        t = open(f, encoding="utf-8", errors="replace").read()
        print("[%s] %s" % (os.path.basename(f), t[:600] or "(空)"))
