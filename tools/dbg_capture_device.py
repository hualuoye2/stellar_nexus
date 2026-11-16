# -*- coding: utf-8 -*-
"""端到端取证：把**音频设备实际读到的字节**录成 WAV。

为什么要这么做：之前所有检查都是"共享内存里有没有数据"，
而那证明不了"设备收到了什么"。这里模拟 QAudioSink 的消费者行为
（按实时速率读共享环），把读到的字节落成 WAV——
这个文件就是声卡真正会播的东西，可以直接听。
"""
import io
import os
import struct
import subprocess
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")

import numpy as np                                    # noqa: E402
from multiprocessing import shared_memory             # noqa: E402
from 核心 import sonify as SN                         # noqa: E402

EXE = sys.argv[1] if len(sys.argv) > 1 else r"D:\deepseek\release\StellarNexus.exe"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
OUT = r"D:\deepseek\.videotmp\device_capture.wav"
print("被测 EXE：%s" % EXE)

shm = shared_memory.SharedMemory(create=True, size=SN.SHM_WORDS * 8)
shm.buf[:] = b"\x00" * len(shm.buf)
sh = memoryview(shm.buf).cast("d")
sh[SN.SH_MAGIC_WORD] = float(SN.SHM_MAGIC)
sh[SN.SH_MASTER] = 0.55
sh[SN.SH_MELODY] = 0.42
sh[SN.SH_DRONE] = 0.25
sh[SN.SH_STATUS_WORD] = SN.STATUS_STARTING
# 给一批音符（和真实 App 的"星海"音景同量级：9 条）
for i in range(9):
    struct.pack_into("<fffH", shm.buf, SN.NOTES_OFFSET + i * SN.NOTE_BYTES,
                     SN.pitch_hz(1, i % 5), 0.5, -0.4 + 0.1 * i, i % 7)

rfd, wfd = os.pipe()
env = dict(os.environ)
env["_SNX_AUDIO_CHILD"] = "1"
env["_SNX_SHM"] = shm.name
env["_SNX_PIPE_FD"] = str(rfd)
env["_SNX_STATUS_FD"] = "-1"
env["QT_QPA_PLATFORM"] = "offscreen"
env["QT_LOGGING_RULES"] = "qt.multimedia.*=false"
_tmp = r"D:\deepseek\snxtmp"
os.makedirs(_tmp, exist_ok=True)
env["TMP"] = env["TEMP"] = _tmp

p = subprocess.Popen([EXE], env=env, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
t0 = time.time()
while time.time() - t0 < 30 and sh[SN.SH_STATUS_WORD] == SN.STATUS_STARTING:
    time.sleep(0.2)
print("子进程状态字: %s" % sh[SN.SH_STATUS_WORD])
if sh[SN.SH_STATUS_WORD] != SN.STATUS_READY:
    p.kill()
    sys.exit(1)

time.sleep(1.0)          # 让子进程先把环填满
# 按实时速率消费（44100 帧/秒 × 4 字节/帧 = 176400 B/s），录 SECONDS 秒
BYTES_PER_SEC = 44100 * 4
CHUNK = 4096
captured = bytearray()
deadline = time.time() + SECONDS
next_at = time.perf_counter()
starved = 0
while time.time() < deadline:
    r = int(sh[SN.SH_PCM_R]); w = int(sh[SN.SH_PCM_W])
    rem = (w - r) % SN.PCM_BYTES
    take = min(CHUNK, rem)
    if take <= 0:
        starved += 1
        time.sleep(0.005)
        continue
    end = r + take
    base = SN.PCM_OFFSET
    if end <= SN.PCM_BYTES:
        captured += bytes(shm.buf[base + r:base + end])
    else:
        captured += (bytes(shm.buf[base + r:base + SN.PCM_BYTES])
                     + bytes(shm.buf[base:base + end - SN.PCM_BYTES]))
    sh[SN.SH_PCM_R] = float(end % SN.PCM_BYTES)
    # 按实时速率节流
    next_at += take / BYTES_PER_SEC
    sleep = next_at - time.perf_counter()
    if sleep > 0:
        time.sleep(sleep)
    else:
        next_at = time.perf_counter()
p.kill()

with wave.open(OUT, "wb") as f:
    f.setnchannels(2)
    f.setsampwidth(2)
    f.setframerate(44100)
    f.writeframes(bytes(captured))
x = np.frombuffer(bytes(captured), dtype="<i2").astype(np.float32) / 32768.0
print("录到 %d 字节 = %.1f 秒　饥饿次数 %d" % (len(captured), len(captured) / 4.0 / 44100, starved))
if len(x):
    pk = float(np.abs(x).max()); rms = float(np.sqrt((x ** 2).mean()))
    print("★ 设备实际收到的音频：峰值 %.4f (%5.1f dBFS)　RMS %.4f (%5.1f dBFS)"
          % (pk, 20 * np.log10(max(1e-9, pk)), rms, 20 * np.log10(max(1e-9, rms))))
print("WAV 已写出：%s" % OUT)
