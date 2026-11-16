@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
:: ============================================================================
::  build.bat —— STELLAR NEXUS 星图终端 Windows 一键打包脚本
:: ----------------------------------------------------------------------------
::  功能：
::    1. 检查 Python 环境
::    2. 安装 / 校验依赖（PySide6、numpy、pyinstaller）
::    3. 生成应用图标与贴图（资产\make_assets.py）
::    4. 文档审计（tools\audit_readme.py）—— 失败可选手动继续
::    5. 用 PyInstaller 打包成单文件 EXE：dist\StellarNexus.exe
::    6. 打印打包结果与常见问题提示
::
::  用法：双击运行，或在命令行执行 build.bat
::
::  关于第 4 步：这个项目已经三次出现「文档承诺了某行为、代码其实没接上」
::  （信息面板按钮不存在、选中近星后观察点传不进去、星表计数三处互相矛盾），
::  这类问题不抛异常也不写日志，只会让人排查时从错误的前提出发。
::  所以打包前先跑一遍 README 断言审计；失败时给出选择，不硬拦。
:: ============================================================================

cd /d "%~dp0"
echo ============================================================================
echo   STELLAR NEXUS 星图终端 - Windows 打包脚本
echo ============================================================================
echo.

:: ---------------------------------------------------------------- 1. Python
set PYTHON=
where python >nul 2>nul && set PYTHON=python
if "%PYTHON%"=="" (
    where py >nul 2>nul && set PYTHON=py -3
)
if "%PYTHON%"=="" (
    echo [错误] 未检测到 Python，请先安装 Python 3.10 或更高版本，
    echo        并在安装时勾选 "Add Python to PATH"。
    echo        下载地址：https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

echo [1/6] 检查 Python 版本...
%PYTHON% -c "import sys; assert sys.version_info >= (3,10), 'need Python 3.10+'; print('    Python', sys.version.split()[0], 'OK')"
if errorlevel 1 (
    echo [错误] Python 版本过低，请升级到 3.10 以上。
    pause
    exit /b 1
)
echo.

:: ---------------------------------------------------------------- 2. 依赖
echo [2/6] 安装 / 校验依赖...
%PYTHON% -m pip install --upgrade pip --quiet
%PYTHON% -m pip install --disable-pip-version-check --quiet "PySide6>=6.6,<6.10" "numpy>=1.24" "pyinstaller>=6.3"
if errorlevel 1 (
    echo [警告] 依赖安装出现问题（可能是网络原因）。若本机已安装这些库可继续打包。
)
%PYTHON% -c "import PySide6; print('    PySide6', PySide6.__version__, 'OK')" || (
    echo [错误] PySide6 不可用，无法继续。请手动执行：
    echo        %PYTHON% -m pip install -r requirements.txt
    pause
    exit /b 1
)
echo.

:: ---------------------------------------------------------------- 3. 资源
echo [3/6] 生成应用图标与贴图...
if exist "资产\make_assets.py" (
    %PYTHON% "资产\make_assets.py"
) else if exist "assets\make_assets.py" (
    %PYTHON% "assets\make_assets.py"
) else (
    echo [警告] 未找到 make_assets.py，跳过图标生成。
)
echo.

:: 资源目录（兼容中文目录名与 assets 别名）
set ASSET_DIR=资产
if not exist "%ASSET_DIR%" (
    if exist "assets" set ASSET_DIR=assets
)
set ICON_ARG=
if exist "%ASSET_DIR%\icon.ico" set ICON_ARG=--icon="%ASSET_DIR%\icon.ico"
if not defined ICON_ARG if exist "%ASSET_DIR%\icon.png" set ICON_ARG=--icon="%ASSET_DIR%\icon.png"
echo     资源目录：%ASSET_DIR%
echo.

:: ------------------------------------------------- 4. 文档审计（可跳过）
echo [4/6] 文档审计：核对 README 里的数字 / 快捷键 / 菜单 / 模块清单...
if exist "..\tools\audit_readme.py" (
    %PYTHON% "..\tools\audit_readme.py"
    if errorlevel 1 (
        echo.
        echo [警告] README 与代码不一致（详见上面的 FAIL 列表）。
        echo        这不会影响打包结果，但文档错会让你下次排查时从错误的前提出发。
        choice /c YN /n /m "        仍要继续打包吗？(Y/N) "
        if errorlevel 2 (
            echo         已取消打包。请先修正 README，或直接运行：
            echo             %PYTHON% ..\tools\audit_readme.py
            pause
            exit /b 1
        )
    )
) else (
    echo [警告] 未找到 ..\tools\audit_readme.py，跳过文档审计。
)
echo.

:: ---------------------------------------------------------------- 5. 打包
echo [5/6] 清理旧的构建产物...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
echo.

echo [6/6] 开始打包（单文件 EXE，首次打包约需 1-3 分钟）...
%PYTHON% -m PyInstaller --noconfirm --clean ^
    --onefile ^
    --windowed ^
    %ICON_ARG% ^
    --name "StellarNexus" ^
    --add-data "%ASSET_DIR%;%ASSET_DIR%" ^
    --hidden-import "核心" ^
    --hidden-import "核心.star_catalog_data" ^
    --hidden-import "ui.main_window" ^
    --hidden-import "ui.real_sky_view" ^
    --hidden-import "ui.fiction_view" ^
    --hidden-import "ui.body_renderer" ^
    --hidden-import "ui.deepseek_panel" ^
    --hidden-import "ui.object_browser" ^
    --hidden-import "核心.deepseek_client" ^
    --collect-submodules "核心" ^
    --collect-submodules "ui" ^
    --exclude-module tkinter ^
    --exclude-module matplotlib ^
    --exclude-module scipy ^
    --exclude-module pandas ^
    main.py

if errorlevel 1 (
    echo.
    echo [错误] 打包失败。请检查上面的错误信息，常见原因见下方提示。
    goto :tips
)

echo.
echo ============================================================================
echo   打包完成！
echo   可执行文件： %cd%\dist\StellarNexus.exe
echo   双击即可运行，无需安装 Python。
echo ============================================================================
echo.
if exist "dist\StellarNexus.exe" (
    for %%F in ("dist\StellarNexus.exe") do echo   文件大小： %%~zF 字节
    echo.
    choice /c YN /n /m "是否立即运行程序？(Y/N) "
    if errorlevel 2 goto :end
    start "" "dist\StellarNexus.exe"
)
goto :end

:tips
echo.
echo ---------------------- 常见打包问题与解决方案 ----------------------
echo 1) 缺少 Qt 插件（报错 "could not find or load the Qt platform plugin windows"）
echo    解决：确认使用的是官方 PySide6（pip install PySide6），
echo          不要混用 conda 与 pip 安装的 Qt；必要时加入参数
echo          --collect-all PySide6
echo.
echo 2) 报错 "No module named 核心" / "No module named ui"
echo    解决：确认在项目根目录执行本脚本，且 核心\__init__.py、ui\__init__.py 存在；
echo          命令中已包含 --collect-submodules 核心 与 --collect-submodules ui。
echo.
echo 3) 双击 EXE 一闪而过
echo    解决：把 build.spec 里的 SHOW_CONSOLE 改为 True 重新打包，
echo          或用命令行运行 EXE 查看具体错误，再改回 False。
echo.
echo 4) 中文路径/中文文件名导致打包或运行异常
echo    解决：把项目复制到纯英文路径（例如 D:\projects\stellar_nexus）后重新打包。
echo.
echo 5) 杀毒软件误报（PyInstaller 单文件 EXE 常见）
echo    解决：加入白名单，或改用目录版打包（build.spec 中 ONE_FILE = False）。
echo.
echo 6) 启动慢
echo    解决：单文件模式每次运行都要解压到临时目录，可改用目录版
echo          （pyinstaller --noconfirm build.spec 且 ONE_FILE = False）。
echo --------------------------------------------------------------------
pause

:end
endlocal
