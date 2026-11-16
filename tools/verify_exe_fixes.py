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
with open(os.path.join(tempfile.gettempdir(), "sp2.pyz"), "wb") as fp:
    fp.write(ca.extract("PYZ.pyz"))
pyz = A.ZlibArchiveReader(os.path.join(tempfile.gettempdir(), "sp2.pyz"))
def names_of(mod):
    code = pyz.extract([m for m in pyz.toc if m.endswith(mod)][0])
    names = set()
    def walk(c, d=0):
        if d > 4: return
        names.update(c.co_names)
        for k in c.co_consts:
            if hasattr(k, "co_consts"): walk(k, d + 1)
    walk(code)
    return names
n1 = names_of("core.export_media") if any(m.endswith("core.export_media") for m in pyz.toc) else names_of("export_media")
for probe in ("NightExportController", "NightExportWorker", "EncodeJob", "push_frame", "render_widget"):
    print("  export_media 含 %-24s : %s" % (probe, probe in n1))
n2 = names_of("inverse_sky_view")
for probe in ("_line_points", "_star_pool", "_line_cache", "QPainterPath"):
    print("  inverse_sky_view 含 %-22s : %s" % (probe, probe in n2))
n3 = names_of("ui.main_window") if any(m.endswith("ui.main_window") for m in pyz.toc) else names_of("main_window")
print("  main_window 含 _place_tool_window :", "_place_tool_window" in n3)  
n3 = names_of("deepseek_panel")
print("  deepseek_panel 含 setMinimumSize :", "setMinimumSize" in n3, " resize :", "resize" in n3)
