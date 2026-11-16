# -*- coding: utf-8 -*-
"""
tools/audit_readme.py —— 审计 README 里所有"可验证的承诺"
===============================================================================
动机：这个项目已经两次出现「文档承诺了某个行为，代码里其实没接上」——
  1. README 说"信息面板有外星视角按钮"，实际入口在工具菜单；
  2. README 说"选中近星后打开外星视角会以它为观察点"，
     open_inverse_sky 用 getattr 探测 NearbyView.selected_star，
     而那个方法当时根本不存在 → 静默回退，永远用默认值；
  3. 星表计数在三处互相矛盾（148+622=770 / 144+625=769 / 160+621=781），
     而代码实测是 160 / 621 / 781。

这类问题不抛异常、不写日志、界面照常打开，只有"逐条对答案"才能发现。

检查项：
  A 数据计数   README 里的数字必须等于代码里的实际长度
  B 快捷键     快捷键表里的每组键必须在 main_window 里真的注册了
  C 菜单项     README 提到的「菜单 → 条目」必须真的存在于菜单树
  D 模块清单   实际存在的核心/ui 模块必须都写进结构图
  E 自检脚本   声称的数量 vs 实际数量

设计原则（第一版踩过的坑）：
  * 误报比漏报更致命——第一版把「系统设置 → 隐私与安全性」（讲 macOS Gatekeeper
    的操作步骤）、「清标记 → 置标记」（讲 CPU 死循环的机制）也当成菜单断言，
    一次刷出 5 条假 FAIL。
    所以 C 项只认**首段在真实菜单名集合里**的引用，其余一律跳过。
  * 菜单条目允许带 emoji 前缀，比较时先把非中英文数字字符全剥掉。

运行：python tools/audit_readme.py
"""
from __future__ import annotations

import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PKG = os.path.join(ROOT, "stellar_nexus")
sys.path.insert(0, PKG)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")

README = os.path.join(PKG, "README.md")
MAIN_WINDOW = os.path.join(PKG, "ui", "main_window.py")

FAILS: list = []
SKIPS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'  OK  ' if ok else ' FAIL '}] {label}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def skip(label: str, why: str = "") -> None:
    print(f"[ SKIP ] {label}" + (f"　{why}" if why else ""))
    SKIPS.append(label)


def _strip_markup(s: str) -> str:
    """去掉 markdown 强调、emoji、空格与标点，只留下可比较的核心文字。"""
    s = s.replace("**", "").replace("*", "").replace("`", "")
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s)


def main() -> int:
    text = open(README, encoding="utf-8").read()
    src_mw = open(MAIN_WINDOW, encoding="utf-8").read()

    # ======================================================================
    print("=" * 84)
    print("A. 数据计数：README 里的数字 vs 代码里的实际长度")
    print("=" * 84)
    # ======================================================================
    from 核心 import light_pollution as LP
    from 核心 import star_data as SD
    from 核心 import nearby_stars as NS
    from 核心 import city_data as CD
    from 核心 import planet_data as PD

    real = {
        "star_catalog": len(SD.STAR_CATALOG),
        "render_stars": len(SD.ALL_SKY_STARS),
        "extra_in_render": len(SD.EXTRA_STAR_CATALOG),
        "faint_catalog": len(SD.FAINT_STAR_CATALOG),
        "named_cn_in_render": sum(1 for s in SD.ALL_SKY_STARS if s.name),
        "brighter_6": sum(1 for s in SD.ALL_SKY_STARS if s.mag <= 6.0),
        "detail_stars": len(SD.DETAIL_STARS),
        "star_names_cn": len(SD.STAR_NAMES_CN),
        "constellations": len(SD.CONSTELLATIONS),
        "lines": len(SD.constellation_lines_resolved()),
        "deep_sky": len(SD.DEEP_SKY_CATALOG),
        "nearby": len(NS.NEARBY_CATALOG),
        "nearby_gliese": sum(1 for s in NS.NEARBY_CATALOG if s.gliese),
        "nearby_hip": sum(1 for s in NS.NEARBY_CATALOG if s.hip),
        "cities": len(CD.CITIES),
        "countries": len(CD.COUNTRIES),
        "planets": len(PD.PLANET_INFO),
    }
    for k, v in real.items():
        print(f"       代码实测 {k:<20} = {v:,}")
    print()

    claims = [
        (r"主表亮星 \| (\d+) 颗", "star_catalog", "主表亮星"),
        (r"星座连线端点星 \| (\d+) 颗", "extra_in_render", "星座连线端点星"),
        (r"背景暗星层 \| ([\d,]+) 颗", "faint_catalog", "背景暗星层"),
        (r"参与渲染的恒星总数\*\* \| \*\*([\d,]+) 颗", "render_stars", "参与渲染的恒星总数"),
        (r"其中带中文名 \| (\d+) 颗", "named_cn_in_render", "其中带中文名"),
        (r"带精细球体模型的星 \| (\d+) 颗", "detail_stars", "带精细球体模型的星"),
        (r"星座 \| (\d+) 个", "constellations", "星座"),
        (r"连线（HIP 折线，共 (\d+) 条）", "lines", "星座连线折线"),
        (r"深空天体 \| \*\*(\d+) 个", "deep_sky", "深空天体"),
        (r"人类已编目的 (\d+) 个天体", "nearby", "太阳邻域天体"),
        (r"(\d+) 个有 Gliese 编号", "nearby_gliese", "有 Gliese 编号"),
        (r"(\d+) 个有 HIP 编号", "nearby_hip", "有 HIP 编号"),
        (r"内置\*\*离线 GeoNames 城市库（(\d+) 个城市", "cities", "城市库"),
    ]
    for pat, key, name in claims:
        m = re.search(pat, text)
        if m is None:
            skip(f"{name}", f"README 里没有匹配 {pat!r} 的写法")
            continue
        claimed = int(m.group(1).replace(",", ""))
        check(f"{name}：README 写 {claimed:,}，代码 {real[key]:,}",
              claimed == real[key],
              "" if claimed == real[key] else f"差 {claimed - real[key]:+,}")

    # 首次运行的描述行「A 颗主表亮星 + B 颗连线端点星 + C 颗背景暗星 = D 颗」
    m = re.search(r"（(\d+) 颗主表亮星 \+ (\d+) 颗连线端点星 \+ (\d+) 颗背景暗星 = \*\*(\d+) 颗可渲染恒星\*\*）",
                  text)
    if m is None:
        skip("首次运行星表描述行")
    else:
        a, b, c, d = (int(x) for x in m.groups())
        check(f"首次运行描述行 {a}+{b}+{c}={d} 与代码一致",
              a == real["star_catalog"] and b == real["extra_in_render"]
              and c == real["faint_catalog"] and d == real["render_stars"],
              f"代码 {real['star_catalog']}+{real['extra_in_render']}"
              f"+{real['faint_catalog']}={real['render_stars']}")

    # 「实际同时上屏」区间：必须与各波特尔等级的实测绘制量吻合
    m = re.search(r"约 ([\d,]+) ~ ([\d,]+) 颗", text)
    if m is None:
        skip("实际同时上屏区间")
    else:
        lo = int(m.group(1).replace(",", ""))
        hi = int(m.group(2).replace(",", ""))
        counts = []
        for level in range(1, 10):
            lim = LP.limiting_magnitude(level, 0.0, 90.0, 0.0, -90.0)
            counts.append(sum(1 for s in SD.ALL_SKY_STARS if s.mag <= lim))
        check(f"同时上屏区间 {lo:,}~{hi:,} 覆盖全部 9 个波特尔等级",
              lo <= min(counts) and hi >= max(counts),
              f"实测区间 {min(counts):,}~{max(counts):,}")

    # ======================================================================
    print()
    print("=" * 84)
    print("B. 快捷键：README 表里的每组键必须真的注册了")
    print("=" * 84)
    # ======================================================================
    registered = set(re.findall(r'"(Ctrl\+[^"]+)"', src_mw))
    rows = re.findall(r"^\| `([^`]+)`((?: / `[^`]+`)*) \| (.+?) \|$", text, re.M)
    total = 0
    for first, rest, desc in rows:
        keys = [first] + re.findall(r"`([^`]+)`", rest)
        if not keys[0].startswith(("Ctrl", "F1", "F11")):
            continue
        for k in keys:
            total += 1
            if k == "R":
                check(f"快捷键 `{k}`（{desc}）已接上重置视角", "reset_view" in src_mw)
            elif re.fullmatch(r"F\d+", k):
                check(f"快捷键 `{k}`（{desc}）已注册",
                      f'"{k}"' in src_mw or f"Key_{k}" in src_mw)
            else:
                check(f"快捷键 `{k}`（{desc}）已注册", k in registered,
                      "" if k in registered else "main_window 里找不到这个快捷键字符串")
    print(f"       （共检查 {total} 组按键）")

    # ======================================================================
    print()
    print("=" * 84)
    print("C. 菜单项：只审计「首段是真实菜单名」的引用（避免误报）")
    print("=" * 84)
    # ======================================================================
    menu_roots = set(re.findall(r"addMenu\(\"([^\"(]+)", src_mw))
    menu_roots = {r.replace("&", "").strip() for r in menu_roots}
    menu_roots |= {"文件", "视图", "工具", "帮助", "编辑"}
    print(f"       代码里真实存在的菜单：{sorted(menu_roots)}")
    print()
    actions = [_strip_markup(m) for m in re.findall(r'_add_action\([^,]+,\s*"([^"]+)"', src_mw)]
    actions = [a for a in actions if a]
    seen: set = set()
    for menu, item in re.findall(r"「([^」]+) → ([^」]+)」", text):
        root = re.sub(r"\(&\w\)", "", menu).strip()
        if root not in menu_roots:
            skip(f"「{menu} → {item}」", "首段不是菜单名（多为操作步骤或机制说明）")
            continue
        if (menu, item) in seen:
            continue
        seen.add((menu, item))
        core = _strip_markup(item)[:6]
        hit = any(core and core in a for a in actions)
        check(f"菜单「{root} → {item}」存在", hit,
              "" if hit else f"在 {len(actions)} 个菜单条目里没找到含「{core}」的")

    # ======================================================================
    print()
    print("=" * 84)
    print("D. 模块清单：实际模块必须都写进 README 结构图")
    print("=" * 84)
    # ======================================================================
    # 结构图是个树形图，只从「## 8. 项目结构」那一节的代码块里抓文件名。
    # 第一版直接全文抓，把 tools/ 里的脚本名也当成结构图条目，反向校验立刻误报。
    sec = re.search(r"## 8\. 项目结构(.*?)```(.*?)```", text, re.S)
    tree = sec.group(2) if sec else ""
    listed = set(re.findall(r"([a-zA-Z_0-9]+\.py)", tree))
    for sub in ("ui", "核心"):
        actual = {f for f in os.listdir(os.path.join(PKG, sub))
                  if f.endswith(".py") and not f.startswith("__")}
        missed = sorted(actual - listed)
        check(f"{sub}/ 下 {len(actual)} 个模块都已写入结构图", not missed,
              "" if not missed else "缺：" + "、".join(missed))
    # 反向：结构图里列了但文件不存在
    ghost = []
    for f in sorted(listed):
        if f in ("main.py", "make_assets.py"):
            continue
        if not os.path.exists(os.path.join(PKG, "ui", f)) \
                and not os.path.exists(os.path.join(PKG, "核心", f)):
            ghost.append(f)
    check("结构图里没有列出不存在的模块", not ghost,
          "" if not ghost else "多出：" + "、".join(ghost))

    # ======================================================================
    print()
    print("=" * 84)
    print("E. 自检脚本数量")
    print("=" * 84)
    # ======================================================================
    n_check = len([f for f in os.listdir(os.path.join(ROOT, "tools"))
                   if f.startswith("check_") and f.endswith(".py")])
    m = re.search(r"`check_\*\.py`（(\d+) 个", text)
    if m:
        check(f"check_* 数量：README 写 {m.group(1)}，实际 {n_check}",
              int(m.group(1)) == n_check)
    else:
        print(f"       README 未写出具体数量（实际 {n_check} 个），无需比对")

    # ======================================================================
    print()
    if SKIPS:
        print(f"ℹ️  跳过 {len(SKIPS)} 条（非断言，仅提示）")
    if FAILS:
        print(f"❌ {len(FAILS)} 项断言失败：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部断言通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
