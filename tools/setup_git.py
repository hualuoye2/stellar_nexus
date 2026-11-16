# -*- coding: utf-8 -*-
"""开发期工具：初始化 git 仓库并完成首次提交（不推向任何远程）。

用法：
    python tools/setup_git.py            # 初始化 + 暂存 + 首次提交
    python tools/setup_git.py --status   # 只看状态

设计要点：
    · 只提交源码与文档（见 .gitignore）；EXE / 缓存 / 第三方原始星表全部忽略
    · 用户身份只写进**本仓库**的 .git/config，不动全局配置
    · 不添加任何 remote（推到 GitHub 需要你自己建仓库后再 git remote add）
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
GIT_CANDIDATES = (
    "git",
    # 便携版 MinGit（本项目自带，不需要安装、不动系统 PATH）
    os.path.join(ROOT, ".tools", "MinGit", "cmd", "git.exe"),
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "cmd", "git.exe"),
    os.path.join(os.environ.get("ProgramFiles", ""), "Git", "bin", "git.exe"),
)


def find_git() -> str:
    """找到可用的 git（PATH 里没有就去常见安装位置找）。"""
    found = shutil.which("git")
    if found:
        return found
    for path in GIT_CANDIDATES:
        if path and os.path.isfile(path):
            return path
    return ""


def run(git: str, args, check: bool = True, quiet: bool = False):
    """执行 git 命令。"""
    cmd = [git] + list(args)
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if not quiet:
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if out:
            print("   " + out.replace("\n", "\n   "))
        if err and result.returncode != 0:
            print("   [stderr] " + err.replace("\n", "\n   "))
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败（退出码 {result.returncode}）")
    return result


def main() -> int:
    status_only = "--status" in sys.argv
    git = find_git()
    if not git:
        print("未找到 git。请先安装：winget install Git.Git")
        print("（安装后需要新开一个终端，或让我用完整路径调用）")
        return 1
    print(f"git: {git}")

    # ---- 初始化 ----
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        if status_only:
            print("尚未初始化仓库（.git 不存在）")
            return 0
        print("\n① 初始化仓库")
        run(git, ["init", "-b", "main"], check=False)
        # 若旧版 git 不支持 -b，退回默认分支名再改名
        if not os.path.isdir(os.path.join(ROOT, ".git")):
            run(git, ["init"])
            run(git, ["branch", "-M", "main"], check=False)

    # ---- 仓库级身份（不动全局配置） ----
    if not status_only:
        print("\n② 设置本仓库提交身份（仅写入 .git/config）")
        run(git, ["config", "user.name", "Stellar Nexus"], check=False)
        run(git, ["config", "user.email", "stellar@localhost"], check=False)
        run(git, ["config", "core.quotepath", "false"], check=False)   # 中文文件名正常显示
        run(git, ["config", "core.autocrlf", "false"], check=False)

    # ---- 查看状态 ----
    print("\n③ 当前状态")
    run(git, ["status", "--short", "--branch"], check=False)
    if status_only:
        return 0

    # ---- 暂存（.gitignore 已排除产物） ----
    print("\n④ 暂存文件")
    run(git, ["add", "-A"])
    staged = run(git, ["diff", "--cached", "--name-only"], quiet=True)
    files = [f for f in (staged.stdout or "").splitlines() if f.strip()]
    print(f"   暂存 {len(files)} 个文件")
    for name in files[:60]:
        print("     " + name)
    if len(files) > 60:
        print(f"     … 另有 {len(files) - 60} 个")

    # ---- 检查是否有不该入库的大文件 ----
    big: list = []
    for name in files:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path) and os.path.getsize(path) > 2 * 1024 * 1024:
            big.append((name, os.path.getsize(path) / 1024 / 1024))
    if big:
        print("\n   ⚠ 以下文件超过 2 MB，确认是否真要入库：")
        for name, mb in big:
            print(f"     {mb:.1f} MB  {name}")

    # ---- 提交 ----
    print("\n⑤ 首次提交")
    message = (
        "首次提交：STELLAR NEXUS 星图终端\n\n"
        "功能范围：\n"
        "· 现实星图（781 颗恒星 / 88 星座 / 129 深空天体 / 行星日月银河）\n"
        "· 太阳邻域（50 光年内 860 个已知天体，真实三维分布）\n"
        "· 架空星图（程序生成宇宙 + 航线 + 飞船 + 历史推演）\n"
        "· 天象日历与时间旅行、星空导览、观测计划表\n"
        "· 地平障碍与可见性分析、暗空光害模拟（波特尔 1~9）\n"
        "· 天体精细建模、导出 PNG / GIF / MP4\n"
        "· DeepSeek 历史小说工作台\n\n"
        "技术：Python 3.10+ / PySide6，纯 Python 天文算法（Meeus / VSOP87D / ELP2000）"
    )
    run(git, ["commit", "-m", message], check=False)

    print("\n⑥ 提交结果")
    run(git, ["log", "--oneline", "--stat", "-3"], check=False)
    print("\n完成。以后每次改动后执行：")
    print("   git add -A && git commit -m \"说明这次改了什么\"")
    print("回滚某个文件到上次提交：")
    print("   git checkout -- 路径")
    print("查看所有历史版本：")
    print("   git log --oneline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
