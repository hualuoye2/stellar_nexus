# -*- coding: utf-8 -*-
"""验证**打包后的 EXE**里"星图之声"的音频子进程真的能起来。

为什么必须单独验这件事：
  音频合成原在同进程线程里做，但主线程重绘会抢 GIL（实测生产速率只有 0.81×，
  音乐卡顿），于是改成子进程合成。打包成单文件 EXE 之后：
    · `sys.executable` 是那个 EXE，不是 python；
    · `核心` 包在 EXE 内部，不在文件系统上，`subprocess` 起 `python -c` 找不到它。
  所以冰冻模式改成"用同一个 EXE + 环境变量 `_SNX_AUDIO_CHILD`"再起一次进程。
  **这条路只有真的启动打包后的 EXE 才能验证。**

判据（端到端，不看任何内部变量）：
  1) 用 `_SNX_AUDIO_CHILD=1` 起 EXE，它必须把共享内存里的**状态字**置成就绪，
     并开始往 PCM 环里写数据（说明它认出了标志、在 EXE 内部 import 到了
     `核心.sonify`、并且成功打开了 QAudioSink）；
  2) 关掉"退出管道"后它必须自行退出（不留孤儿）。

★ 这里踩过的两个坑，写下来免得再犯：
  · `subprocess.Popen` 默认 `close_fds=True`，**子进程继承不到额外管道 fd**。
    所以"子进程用管道向父进程报就绪"在 Windows 上是坏的（子进程拿到 fd=-1，
    退回 stderr，而 `--windowed` 的 EXE 里 stderr 是空的）→ 现在用共享内存状态字。
  · `--windowed` 打包后 `sys.stdout` / `sys.stderr` 是 None，子进程里任何
    `print` 都等于扔掉。
"""
import io
import os
import subprocess
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")


def find_exe():
    """定位打包好的 StellarNexus.exe：命令行参数优先 → release\\ → 旧 dist\\ 回退。

    ⚠️ 2026-10 布局迁移：EXE 已从 stellar_nexus\\dist\\ 移到 D:\\deepseek\\release\\，
    本文件原来默认就指向 release\\（是对的），这里与其他 verify_exe_*.py 统一成
    同一套查找逻辑，找不到时明确报错并 exit 2。
    """
    ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cands = []
    if len(sys.argv) > 1:
        cands.append(sys.argv[1])
    for base in (os.path.join(ws, "release"),
                 os.path.join(ws, "stellar_nexus", "release"),
                 os.path.join(ws, "stellar_nexus", "dist")):
        cands.append(os.path.join(base, "StellarNexus.exe"))
    for p in cands:
        if os.path.isfile(p):
            return p
    print("ERROR: 找不到 StellarNexus.exe，已尝试以下路径：", file=sys.stderr)
    for p in cands:
        print("  - " + p, file=sys.stderr)
    print("用法: python tools\\" + os.path.basename(__file__) + " [EXE 路径]", file=sys.stderr)
    raise SystemExit(2)


EXE = find_exe()
print("EXE:", EXE)
FAILS = []


def check(label, ok, detail=""):
    print("[%s] %s%s" % ("  OK  " if ok else " FAIL ", label, ("　" + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


from multiprocessing import shared_memory          # noqa: E402
import numpy as np_c                               # noqa: E402
from 核心 import sonify as SN                      # noqa: E402

if not os.path.isfile(EXE):
    print("找不到 EXE：%s" % EXE)
    sys.exit(2)
print("被测 EXE：%s（%.1f MB）" % (EXE, os.path.getsize(EXE) / 1048576.0))

shm = shared_memory.SharedMemory(create=True, size=SN.SHM_WORDS * 8)
shm.buf[:] = b"\x00" * len(shm.buf)
sh = memoryview(shm.buf).cast("d")
# ★ magic 现在有**独立槽位** SH_MAGIC_WORD（原来是 pack_into(buf, 8, MAGIC)，
#   而字节 8~15 正好是标量下标 1 = SH_MASTER：父进程一设音量就把魔数砸掉，
#   子进程读不到音符 → 合成全零 PCM。这个探针也跟着改，否则它会用一个
#   已经不成立的假设去测 EXE）。
sh[SN.SH_MAGIC_WORD] = float(SN.SHM_MAGIC)
sh[SN.SH_MASTER] = 0.55
sh[SN.SH_MELODY] = 0.42
sh[SN.SH_STATUS_WORD] = SN.STATUS_STARTING
# ★ 必须给几条音符。这一版加了"低音线与铃声都受 has_harmony 门控"的规则
#   （没有乐谱就完全安静），所以**不给音符时全零是正确行为**——
#   拿它去判"静音故障"会得到一个假结论（我第一版断言就这么假失败了一次）。
import struct as _st                                  # noqa: E402
for _i in range(6):
    _st.pack_into("<fffH", shm.buf, SN.NOTES_OFFSET + _i * SN.NOTE_BYTES,
                  SN.pitch_hz(1, _i), 0.5, 0.0, 0)
rfd, wfd = os.pipe()
env = dict(os.environ)
env["_SNX_AUDIO_CHILD"] = "1"
env["_SNX_SHM"] = shm.name
env["_SNX_PIPE_FD"] = str(rfd)
env["_SNX_STATUS_FD"] = "-1"
env["QT_QPA_PLATFORM"] = "offscreen"
env["QT_LOGGING_RULES"] = "qt.multimedia.*=false"
env.pop("STELLAR_NEXUS_HEADLESS", None)
# ★ 单文件 EXE 启动时要先把自己解压到 %TEMP%。本沙箱的默认 %TEMP% 不可写，
#   于是 PyInstaller 的引导层直接报 "Could not create temporary directory!"
#   并退出（退出码 4294967295），看起来像"子进程不干活"。
#   这不是代码问题，是环境限制，所以这里显式给一个可写目录并说明原因。
#   ★ 目录不存在时要**自己建**：这个目录会被清理脚本删掉，缺失时 EXE 起不来，
#     而失败表现是"状态字 0 / 退出码 4294967295"，与真实回归无法区分。
_tmp = os.environ.get("SNX_PROBE_TMP") or r"D:\deepseek\snxtmp"
os.makedirs(_tmp, exist_ok=True)
env["TMP"] = env["TEMP"] = _tmp

print()
print("A 用 _SNX_AUDIO_CHILD=1 起 EXE（模拟父进程拉起的音频子进程）……")
p = subprocess.Popen([EXE], env=env, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
t0 = time.time()
status = SN.STATUS_STARTING
while time.time() - t0 < 30:
    status = sh[SN.SH_STATUS_WORD]
    if status != SN.STATUS_STARTING:
        break
    if p.poll() is not None:
        break
    time.sleep(0.2)
check("★ 打包后的 EXE 认出了音频子进程标志并置就绪"
      "（说明它能在 EXE 内部 import 核心.sonify 并打开音频设备）",
      status == SN.STATUS_READY,
      "状态字=%s（1=就绪 2=失败）　退出码=%s" % (status, p.poll()))

if status == SN.STATUS_READY:
    time.sleep(2.0)
    r = int(sh[SN.SH_PCM_R])
    w = int(sh[SN.SH_PCM_W])
    fill = (w - r) % SN.PCM_BYTES
    check("★ 子进程真的在往共享环里写 PCM（水位 > 50ms）",
          fill / 4.0 / 44100 * 1000.0 > 50.0,
          "水位 %.0f ms" % (fill / 4.0 / 44100 * 1000.0))

    # ★★ 判据 1.5：**水位 ≠ 有声**。子进程完全可以写满一段全零。
    #    实测踩过：struct 格式写成 "<ff" 却取 p[2] → 每块抛 IndexError 被 except
    #    吞掉 → 子进程稳定写全零 PCM，而上面那条"水位"判据**照样通过**，
    #    用户听到的是"完全没声"。所以必须采样 PCM 的**内容**。
    _bad = 0
    _peak = 0.0
    for _k in range(8):
        _r = int(sh[SN.SH_PCM_R])
        _w = int(sh[SN.SH_PCM_W])
        _n = (_w - _r) % SN.PCM_BYTES
        if _n > 4000:
            _seg = np_c.frombuffer(
                bytes(shm.buf[SN.PCM_OFFSET + _r:SN.PCM_OFFSET + _r + 4000]),
                dtype="<i2").astype("float32") / 32768.0
            _pk = float(np_c.abs(_seg).max())
            _peak = max(_peak, _pk)
            if _pk < 1e-4:
                _bad += 1
        time.sleep(0.15)
    check("★★ PCM 内容非静音（峰值 > 0.01，且 8 次采样无全零）",
          _peak > 0.01 and _bad == 0,
          "峰值 %.5f　全零采样 %d/8（只看水位是发现不了静音的）" % (_peak, _bad))

# ---- 判据 2：防孤儿 ----
print()
print("B 关掉退出管道，子进程应自行退出……")
alive_before = p.poll() is None
try:
    os.close(wfd)
    wfd = -1
except Exception:
    pass
t0 = time.time()
while time.time() - t0 < 20 and p.poll() is None:
    time.sleep(0.3)
exited = p.poll() is not None
check("关掉管道后子进程自行退出（不留孤儿）", alive_before and exited,
      "之前存活=%s 之后退出=%s 退出码=%s" % (alive_before, exited, p.poll()))
if not exited:
    try:
        p.kill()
        p.wait(timeout=10)
    except Exception:
        pass

if wfd >= 0:
    try:
        os.close(wfd)
    except Exception:
        pass
try:
    os.close(rfd)
except Exception:
    pass
try:
    shm.close()
    shm.unlink()
except Exception:
    pass

print()
print("结果：", "全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS))
sys.exit(0 if not FAILS else 1)
