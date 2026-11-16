import io, os, sys, tempfile
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
with open(os.path.join(tempfile.gettempdir(), "sp4.pyz"), "wb") as fp:
    fp.write(ca.extract("PYZ.pyz"))
pyz = A.ZlibArchiveReader(os.path.join(tempfile.gettempdir(), "sp4.pyz"))
mods = sorted(m for m in pyz.toc)
def names_of(suffix):
    code = pyz.extract([m for m in pyz.toc if m.endswith(suffix)][0])
    names = set()
    def walk(c, d=0):
        if d > 4: return
        names.update(c.co_names)
        for k in c.co_consts:
            if hasattr(k, "co_consts"): walk(k, d + 1)
    walk(code); return names
n = names_of("ui.main_window")
for probe in ("export_watchlist", "_auto_watch", "_select_nearest_nearby",
              "_place_tool_window", "_route_combos_wired"):
    print("  main_window 含 %-22s : %s" % (probe, probe in n))
print("  watchlist_export 已打包 :", any(m.endswith("watchlist_export") for m in mods))
print("  planner_panel 含 setWordWrap :", "setWordWrap" in names_of("ui.planner_panel"))
