import io, os, sys, tempfile, marshal
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
try:
    from PyInstaller.archive.readers import CArchiveReader
    from PyInstaller.loader import pyimod01_archive as A
except ImportError:  # PyInstaller 只装在 python312；用错解释器时给清晰指引（不要裸 ModuleNotFoundError）
    print("ERROR: 本脚本需要 PyInstaller（只装在 D:\\deepseek\\python312\\python.exe）", file=sys.stderr)
    print("      请改用: D:\\deepseek\\python312\\python.exe tools\\" + os.path.basename(__file__), file=sys.stderr)
    raise SystemExit(2)


def find_exe():
    """定位打包好的 StellarNexus.exe：命令行参数优先 → release\\ → 旧 dist\\ 回退。

    ⚠️ 2026-10 布局迁移：EXE 已从 stellar_nexus\\dist\\ 移到 D:\\deepseek\\release\\，
    本文件曾把旧路径写死 ⇒ 静态验收直接 FileNotFoundError。现在找不到时明确
    报错并 exit 2（不静默通过，也不假装验证过）。
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


exe = find_exe()
print("EXE:", exe)
ca = CArchiveReader(exe)
blob = ca.extract("PYZ.pyz")
tmp = os.path.join(tempfile.gettempdir(), "stellar_pyz.pyz")
with open(tmp, "wb") as fp:
    fp.write(blob)
pyz = A.ZlibArchiveReader(tmp)
mods = [m for m in pyz.toc if "real_sky_view" in m]
print("模块:", mods)
code = pyz.extract(mods[0])
consts, names = set(), set()
def walk(c, depth=0):
    if depth > 3: return
    names.update(c.co_names)
    for k in c.co_consts:
        if isinstance(k, (int, float, str)) and not isinstance(k, bool):
            consts.add(k)
        if hasattr(k, "co_consts"):
            walk(k, depth + 1)
walk(code)
for probe in (20, 0.72, 1.05, 4, 0.02, 0.20, "MILKY_OUTER_TAPER", "MILKY_RIFT_FILL_K",
              "MILKY_CONTOUR_LEVELS", "MILKY_RIFT_BANDS", "MILKY_RIFT_FADE_LO", "MILKY_RIFT_FADE_HI", "_draw_milky_rift",
              "MILKY_LAYERS", "C_MILKY"):
    print("  含 %-22s : %s" % (probe, probe in consts or probe in names))
tuples5 = [c for c in consts if isinstance(c, tuple) and len(c) == 5]
print("  五元组常量（尘埃斑表会被拆成 40 个）:", len(tuples5))
