# -*- coding: utf-8 -*-
"""
utils.py —— 通用工具函数
================================================================================
本模块只依赖 Python 标准库，可以被界面层和计算层共同引用。

内容：
    * 应用级常量（名称、版本、默认坐标）
    * 资源路径解析（兼容 PyInstaller 单文件模式的 sys._MEIPASS）
    * 数值与角度工具（clamp / lerp / 角度归一化 / 格式化）
    * 天文常用格式化（赤经 → 时分秒、赤纬 → 度分秒）
    * 星等 → 显示亮度的映射
    * 日志与错误捕获
    * JSON 读写（本地存档、导入导出）
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# ==============================================================================
# 一、应用级常量
# ==============================================================================

APP_NAME = "STELLAR NEXUS 星图终端"
APP_SHORT = "StellarNexus"
APP_VERSION = "1.1.3"
APP_ENCODING = "UTF-8"

#: 定位失败时的默认观测点（北京 · 天安门附近）
DEFAULT_LAT = 39.9042
DEFAULT_LON = 116.4074
DEFAULT_LOCATION_NAME = "北京 (定位回退)"

#: 默认观测时间偏移（小时）。None 表示使用系统当前时间。
DEFAULT_UTC_OFFSET_HOURS = 8.0

#: 恒星/星系渲染时使用的极限星等（越小越暗）
LIMITING_MAGNITUDE = 6.6


# ==============================================================================
# 二、资源路径解析（PyInstaller 友好）
# ==============================================================================

def is_frozen() -> bool:
    """判断当前是否运行在 PyInstaller 打包后的单文件环境中。"""
    return bool(getattr(sys, "frozen", False))


def base_dir() -> str:
    """返回「只读资源」所在目录。

    打包后 PyInstaller 会把 --add-data 的内容解压到临时目录 sys._MEIPASS，
    因此这里优先使用它；否则使用本文件所在项目的根目录。
    """
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return str(getattr(sys, "_MEIPASS"))
    # 核心/utils.py -> 上一级即项目根目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_dir() -> str:
    """返回「可写数据」目录（存档、导出文件放在这里）。

    * 打包后：EXE 所在目录
    * 源码运行：项目根目录
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


#: 资产目录名的候选列表：不同平台/不同解压工具对中文目录名的处理可能不同，
#: 因此同时支持「资产」与「assets」两种命名。
_ASSET_DIR_NAMES = ("资产", "assets", "Assets", "資産")


def asset_dir() -> str:
    """返回资产目录的绝对路径（自动兼容中文目录名与 assets 别名）。"""
    root = base_dir()
    for name in _ASSET_DIR_NAMES:
        path = os.path.join(root, name)
        if os.path.isdir(path):
            return path
    # 都没有时，返回首选路径（后续写入图标时会自动创建）
    return os.path.join(root, _ASSET_DIR_NAMES[0])


def asset_path(*parts: str) -> str:
    """拼接资产文件的绝对路径，例如 asset_path("sprites", "star.png")。"""
    return os.path.join(asset_dir(), *parts)


def ensure_dir(path: str) -> str:
    """确保目录存在（失败时静默返回原路径，交由上层处理）。"""
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def saves_dir() -> str:
    """本地存档目录。"""
    return ensure_dir(os.path.join(app_dir(), "saves"))


# ==============================================================================
# 三、数值与角度工具
# ==============================================================================

def clamp(value: float, low: float, high: float) -> float:
    """把 value 限制在 [low, high] 区间内。"""
    if value < low:
        return low
    if value > high:
        return high
    return value


def lerp(a: float, b: float, t: float) -> float:
    """线性插值：t=0 返回 a，t=1 返回 b。"""
    return a + (b - a) * t


def norm360(deg: float) -> float:
    """把角度归一化到 [0, 360)。"""
    return math.fmod(math.fmod(deg, 360.0) + 360.0, 360.0)


def norm180(deg: float) -> float:
    """把角度归一化到 (-180, 180]，便于计算角度差。"""
    d = norm360(deg)
    return d - 360.0 if d > 180.0 else d


def angle_delta(a: float, b: float) -> float:
    """返回 a→b 的最短角度差（带符号，单位度）。"""
    return norm180(b - a)


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    """标准 smoothstep，用于淡入淡出。"""
    if edge1 == edge0:
        return 0.0
    t = clamp((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def fmt_float(value: Optional[float], digits: int = 2, unit: str = "") -> str:
    """安全地格式化浮点数（None/NaN 返回 '--'）。"""
    if value is None:
        return "--"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "--"
    if math.isnan(f) or math.isinf(f):
        return "--"
    return f"{f:.{digits}f}{unit}"


def fmt_ra(ra_deg: float) -> str:
    """赤经（度）→ '12h 34.5m' 形式的字符串。"""
    ra = norm360(ra_deg) / 15.0  # 度 → 小时
    hh = int(ra)
    mm = (ra - hh) * 60.0
    return f"{hh:02d}h {mm:04.1f}m"


def fmt_dec(dec_deg: float) -> str:
    """赤纬（度）→ '+41° 16′' 形式的字符串。"""
    sign = "+" if dec_deg >= 0 else "-"
    d = abs(dec_deg)
    deg = int(d)
    minute = (d - deg) * 60.0
    return f"{sign}{deg:02d}° {minute:04.1f}′"


def fmt_alt_az(alt_deg: float, az_deg: float) -> str:
    """高度角/方位角的中文描述。"""
    return f"高度 {alt_deg:+.2f}° / 方位 {norm360(az_deg):.2f}°"


_COMPASS_16 = ("北", "北东北", "东北", "东东北", "东", "东东南", "东南", "南东南",
               "南", "南西南", "西南", "西西南", "西", "西西北", "西北", "北西北")


def azimuth_to_compass(az_deg: float) -> str:
    """方位角（度，从正北顺时针）→ 中文十六方位。"""
    idx = int((norm360(az_deg) + 11.25) // 22.5) % 16
    return _COMPASS_16[idx]


def mag_to_radius(mag: float, base: float = 1.6) -> float:
    """星等 → 光点半径（像素）。

    采用通行的亮度-星等关系：半径 ∝ 10^(-0.2·mag)，并做最小/最大裁剪，
    保证亮星（-1.46 等）足够醒目，6 等星仍能看到 1px 的点。
    base 为 0 等星对应的半径，默认 1.6 px 左右，肉眼观感接近真实星图。
    """
    r = base * (10.0 ** (-0.2 * (mag + 1.46)))
    return clamp(r, 0.75, 11.0)


def mag_to_alpha(mag: float, limit: float = LIMITING_MAGNITUDE) -> float:
    """星等 → 透明度（越暗越透明），用于营造星空层次。"""
    if mag >= limit:
        return 0.08
    span = max(1.0, limit + 1.5)
    t = clamp((mag + 1.5) / span, 0.0, 1.0)      # 0 = 最亮, 1 = 最暗
    return clamp(1.0 - 0.80 * t, 0.18, 1.0)


def bv_to_rgb(bv: float) -> Tuple[int, int, int]:
    """B-V 色指数 → RGB（0-255）。

    使用经典的色指数-色温近似，再做黑体色温 → sRGB 转换，
    这样从蓝色的 O 型星到红色的 M 型星都能得到自然的颜色。
    """
    bv = clamp(bv, -0.4, 2.0)
    # 1) B-V → 色温（Ballesteros 公式）
    try:
        temp = 4600.0 * (1.0 / (0.92 * bv + 1.7) + 1.0 / (0.92 * bv + 0.62))
    except ZeroDivisionError:
        temp = 5800.0
    # 2) 色温 → RGB（Tanner Helland 近似）
    t = clamp(temp, 1000.0, 40000.0) / 100.0
    if t <= 66.0:
        r = 255.0
        g = clamp(99.4708025861 * math.log(t) - 161.1195681661, 0.0, 255.0)
        b = 0.0 if t <= 19.0 else clamp(138.5177312231 * math.log(t - 10.0) - 305.0447927307, 0.0, 255.0)
    else:
        r = clamp(329.698727446 * ((t - 60.0) ** -0.1332047592), 0.0, 255.0)
        g = clamp(288.1221695283 * ((t - 60.0) ** -0.0755148492), 0.0, 255.0)
        b = 255.0
    # 稍微提高整体亮度，避免深空背景上过暗
    r, g, b = r * 1.05, g * 1.05, b * 1.12
    return int(clamp(r, 0, 255)), int(clamp(g, 0, 255)), int(clamp(b, 0, 255))


_SPECTRAL_RGB: Dict[str, Tuple[int, int, int]] = {
    "O": (155, 176, 255), "B": (170, 191, 255), "A": (202, 215, 255),
    "F": (248, 247, 255), "G": (255, 244, 232), "K": (255, 210, 161),
    "M": (255, 180, 130), "L": (255, 130, 100), "T": (200, 90, 120),
    "W": (230, 240, 255), "D": (220, 230, 255), "N": (120, 160, 255),
    "S": (255, 160, 120), "C": (255, 140, 110), "X": (150, 150, 255),
}


def spectral_to_rgb(spectral: str) -> Tuple[int, int, int]:
    """光谱型（如 'G2V'、'K5III'、'M'）→ RGB 颜色。"""
    if not spectral:
        return (255, 255, 255)
    key = spectral.strip().upper()[0]
    return _SPECTRAL_RGB.get(key, (255, 255, 255))


def hex_to_rgb(color: str) -> Tuple[int, int, int]:
    """'#RRGGBB' → (r, g, b)。解析失败返回白色。"""
    try:
        c = color.lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except Exception:
        return (255, 255, 255)


def rgb_to_hex(rgb: Sequence[int]) -> str:
    """(r, g, b) → '#rrggbb'。"""
    r, g, b = (int(clamp(v, 0, 255)) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def mix_color(c1: Sequence[int], c2: Sequence[int], t: float) -> Tuple[int, int, int]:
    """两个 RGB 颜色按比例混合。"""
    t = clamp(t, 0.0, 1.0)
    return (int(lerp(c1[0], c2[0], t)), int(lerp(c1[1], c2[1], t)), int(lerp(c1[2], c2[2], t)))


def rgb_str(rgb: Sequence[int], alpha: float = 1.0) -> str:
    """(r,g,b) + alpha → 'rgba(r,g,b,a)' 字符串，方便直接塞进 Qt 样式。"""
    a = clamp(alpha, 0.0, 1.0)
    return f"rgba({int(rgb[0])},{int(rgb[1])},{int(rgb[2])},{a:.3f})"


# ==============================================================================
# 四、深色调色板（根治「文字看不见」）
# ==============================================================================
# 重要说明：
#   QSS 只能改变「被选择器命中的控件」的外观。对于没有被样式表覆盖的普通
#   QWidget 容器（例如图层复选框所在的面板、信息面板的内层容器），Qt 会退回
#   到**系统调色板**——Windows 默认浅色主题下是 #efefef 浅灰底，
#   于是我们设置的浅色文字（#e6f6fd）落在浅灰底上，几乎不可见。
#   解决办法：创建 QApplication 后立刻把**应用调色板**整体改成深色，
#   这样任何落到默认分支的控件也是深底浅字，与 QSS 形成双保险。

#: 界面配色（与 资产/styles.qss 保持一致）
DARK_BG = "#061019"
DARK_BG_ALT = "#0a1a28"
DARK_BG_DEEP = "#03080f"
DARK_TEXT = "#e6f6fd"
DARK_TEXT_DIM = "#a9c9da"
DARK_TEXT_LABEL = "#8fd4ea"
DARK_ACCENT = "#00e5ff"


def build_dark_palette():
    """构建深色 QPalette（延迟导入 Qt，避免核心包强依赖 GUI）。无 Qt 时返回 None。"""
    try:
        from PySide6.QtGui import QColor, QPalette
    except Exception:                                   # pragma: no cover
        return None
    pal = QPalette()
    bg = QColor(DARK_BG)
    bg_alt = QColor(DARK_BG_ALT)
    deep = QColor(DARK_BG_DEEP)
    text = QColor(DARK_TEXT)
    dim = QColor(DARK_TEXT_DIM)
    accent = QColor(DARK_ACCENT)

    pal.setColor(QPalette.Window, bg)
    pal.setColor(QPalette.WindowText, text)
    pal.setColor(QPalette.Base, deep)
    pal.setColor(QPalette.AlternateBase, bg_alt)
    pal.setColor(QPalette.ToolTipBase, bg_alt)
    pal.setColor(QPalette.ToolTipText, text)
    pal.setColor(QPalette.Text, text)
    pal.setColor(QPalette.Button, bg_alt)
    pal.setColor(QPalette.ButtonText, text)
    pal.setColor(QPalette.BrightText, QColor("#ffffff"))
    pal.setColor(QPalette.Link, accent)
    pal.setColor(QPalette.LinkVisited, QColor("#a06eff"))
    pal.setColor(QPalette.Highlight, QColor("#00b4d8"))
    pal.setColor(QPalette.HighlightedText, QColor("#02121a"))
    try:
        pal.setColor(QPalette.PlaceholderText, dim)
    except Exception:
        pass
    # 禁用态：给出低对比但可读的颜色，避免系统配色导致完全不可见
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#7f9fb0"))
    pal.setColor(QPalette.Disabled, QPalette.Base, QColor("#0a141d"))
    pal.setColor(QPalette.Disabled, QPalette.Button, QColor("#0d1c28"))
    pal.setColor(QPalette.Disabled, QPalette.Highlight, QColor("#0a5f78"))
    pal.setColor(QPalette.Disabled, QPalette.HighlightedText, QColor("#8fb8cc"))
    return pal


def apply_dark_theme(app) -> None:
    """给 QApplication 应用深色调色板（应在加载 QSS 之前调用）。"""
    try:
        pal = build_dark_palette()
        if pal is None:
            return
        app.setPalette(pal)
        LOG.info("已应用深色调色板（杜绝未覆盖控件落到系统浅色底）")
    except Exception as exc:                            # pragma: no cover
        LOG.warn(f"应用深色调色板失败（不影响运行）：{exc}")


# ==============================================================================
# 五、时间工具
# ==============================================================================

def now_local() -> datetime:
    """当前本地时间。"""
    return datetime.now()


def format_datetime(dt: datetime) -> str:
    """统一的界面时间格式：'2025-01-31 22:15:04'。"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_clock(dt: datetime) -> str:
    """仅时间部分，用于工具栏时钟。"""
    return dt.strftime("%H:%M:%S")


def format_gametime(years: float) -> str:
    """架空宇宙时间：把「年（浮点）」格式化为 '银河历 12.34 年'。"""
    years = max(0.0, float(years))
    return f"银河历 {years:,.2f} 年"


def human_duration(seconds: float) -> str:
    """秒 → 人类可读的时长（用于航线耗时显示）。"""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.0f} 秒"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:.1f} 分钟"
    hours = minutes / 60.0
    if hours < 24:
        return f"{hours:.1f} 小时"
    days = hours / 24.0
    if days < 365:
        return f"{days:.1f} 天"
    return f"{days / 365.25:.2f} 年"


def timestamp_tag() -> str:
    """生成文件名用的时间戳标签。"""
    return time.strftime("%Y%m%d_%H%M%S")


# ==============================================================================
# 六、日志 / 异常捕获
# ==============================================================================

@dataclass
class LogEntry:
    """一条日志记录。"""
    level: str
    message: str
    when: float = field(default_factory=time.time)

    def stamp(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.when))


class AppLog:
    """极简日志器：保留最近 N 条记录，并可通过回调推送到界面状态栏。

    设计目标是「不允许控制台报错」——所有异常都先进日志，
    再由界面决定如何提示用户，而不是让 traceback 直接炸掉进程。
    """

    MAX_ENTRIES = 400

    def __init__(self) -> None:
        self.entries: List[LogEntry] = []
        self._sinks: List[Callable[[LogEntry], None]] = []
        self._installed = False

    # ---- 基础写入 ----
    def add(self, level: str, message: str) -> LogEntry:
        entry = LogEntry(level=str(level).upper(), message=str(message))
        self.entries.append(entry)
        if len(self.entries) > self.MAX_ENTRIES:
            del self.entries[: len(self.entries) - self.MAX_ENTRIES]
        for sink in list(self._sinks):
            try:
                sink(entry)
            except Exception:
                pass
        return entry

    def info(self, message: str) -> None:
        self.add("INFO", message)

    def warn(self, message: str) -> None:
        self.add("WARN", message)

    def error(self, message: str, exc: Optional[BaseException] = None) -> None:
        text = message
        if exc is not None:
            text = f"{message} :: {type(exc).__name__}: {exc}"
        self.add("ERROR", text)

    # ---- 订阅 ----
    def subscribe(self, sink: Callable[[LogEntry], None]) -> None:
        """注册一个日志接收器（例如主窗口状态栏）。"""
        if sink not in self._sinks:
            self._sinks.append(sink)

    def unsubscribe(self, sink: Callable[[LogEntry], None]) -> None:
        if sink in self._sinks:
            self._sinks.remove(sink)

    def recent_text(self, count: int = 40) -> str:
        """导出最近的日志文本（供「工具 → 查看日志」使用）。"""
        lines = [f"[{e.stamp()}] {e.level:<5} {e.message}" for e in self.entries[-count:]]
        return "\n".join(lines) if lines else "（暂无日志）"

    # ---- 全局异常钩子 ----
    def install_excepthook(self) -> None:
        """安装全局异常钩子，避免未捕获异常把进程打挂（同时记录 traceback）。"""
        if self._installed:
            return
        self._installed = True

        def _hook(exc_type, exc_value, exc_tb) -> None:  # noqa: ANN001
            if issubclass(exc_type, KeyboardInterrupt):
                sys.__excepthook__(exc_type, exc_value, exc_tb)
                return
            detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            self.add("ERROR", f"未捕获异常：{exc_value}")
            for line in detail.strip().splitlines()[-6:]:
                self.add("TRACE", line.strip())

        sys.excepthook = _hook
        # 线程中的异常（PySide6 的信号槽里抛出的异常不会经过 sys.excepthook）
        try:
            import threading

            def _thread_hook(args) -> None:  # noqa: ANN001
                if issubclass(args.exc_type, SystemExit):
                    return
                self.add("ERROR", f"线程异常：{args.exc_value}")
                detail = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
                for line in detail.strip().splitlines()[-6:]:
                    self.add("TRACE", line.strip())

            threading.excepthook = _thread_hook
        except Exception:
            pass


#: 全局单例日志器，整个项目共享
LOG = AppLog()


def locate_by_ip(timeout: float = 6.0) -> Optional[Tuple[float, float, str, float]]:
    """用公共 IP 定位服务粗略判断所在城市（失败返回 None，调用方回退默认地点）。

    说明：只用于给星图一个合理的初始观测点；定位失败绝不会影响其他功能。
    返回 (纬度, 经度, 地点名, 时区小时数)。
    """
    import json as _json
    import urllib.request

    services = (
        ("http://ip-api.com/json/?fields=status,country,regionName,city,lat,lon,timezone",
         "ipapi"),
        ("https://ipapi.co/json/", "ipapico"),
    )
    for url, kind in services:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "StellarNexus/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = _json.loads(resp.read().decode("utf-8", "replace"))
            if kind == "ipapi" and data.get("status") != "success":
                continue
            lat = float(data.get("lat", data.get("latitude")))
            lon = float(data.get("lon", data.get("longitude")))
            city = str(data.get("city") or data.get("regionName") or "")
            country = str(data.get("country_name") or data.get("country") or "")
            name = city or country or "未知地点"
            tz = data.get("timezone") or data.get("timezone_name") or ""
            offset = 8.0
            if isinstance(tz, str) and tz.startswith("UTC"):
                try:
                    offset = float(tz[3:].replace(":", "."))
                except ValueError:
                    offset = 8.0
            elif isinstance(tz, (int, float)):
                offset = float(tz)
            else:
                offset = _guess_offset(lon)
            LOG.info(f"IP 定位成功：{name} {lat:+.4f}, {lon:+.4f}（{tz}）")
            return (lat, lon, name, offset)
        except Exception as exc:
            LOG.warn(f"IP 定位失败（{kind}）：{exc}")
    return None


def _guess_offset(lon: float) -> float:
    """按经度粗略推算时区（每 15° 一小时）。"""
    return round(clamp(lon / 15.0, -12.0, 14.0))


def safe_call(func: Callable[..., Any], *args, default: Any = None, **kwargs) -> Any:
    """安全调用：捕获所有异常写日志并返回默认值（用于非关键路径）。"""
    try:
        return func(*args, **kwargs)
    except Exception as exc:  # pragma: no cover - 防御性代码
        LOG.error(f"safe_call 执行 {getattr(func, '__name__', func)} 失败", exc)
        return default


# ==============================================================================
# 七、JSON 读写
# ==============================================================================

def save_json(path: str, data: Any, *, indent: int = 2) -> bool:
    """把数据写入 JSON 文件（UTF-8、ensure_ascii=False 以便中文可读）。"""
    try:
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            ensure_dir(directory)
        with open(path, "w", encoding=APP_ENCODING) as fp:
            json.dump(data, fp, ensure_ascii=False, indent=indent)
        LOG.info(f"已保存: {path}")
        return True
    except Exception as exc:
        LOG.error(f"保存 JSON 失败: {path}", exc)
        return False


def load_json(path: str, default: Any = None) -> Any:
    """读取 JSON 文件；失败时返回 default 并记录日志。

    ★ "文件不存在"按**提示级**记录，不算警告。
      理由：本项目所有可选的本地文件（deepseek_config.json、saves/ 下的存档、
      地平剖面…）在首次运行时**本来就不存在**，属于正常路径。以前它走 LOG.warn，
      于是启动 + 每次生成宇宙都会往日志里塞一条"文件不存在"，
      真正的 WARN 反而被淹没——排查时得先学会忽略它。
      现在只有"文件在、但读不出来"（权限 / 损坏 / 编码）才报 ERROR。
    """
    try:
        with open(path, "r", encoding=APP_ENCODING) as fp:
            return json.load(fp)
    except FileNotFoundError:
        LOG.info(f"可选文件不存在（正常路径，不是错误）: {path}")
    except Exception as exc:
        LOG.error(f"读取 JSON 失败: {path}", exc)
    return default


def list_json_files(directory: str) -> List[str]:
    """列出目录下所有 .json 文件（按名称排序）。"""
    try:
        if not os.path.isdir(directory):
            return []
        names = [n for n in os.listdir(directory) if n.lower().endswith(".json")]
        return sorted(names)
    except Exception:
        return []


# ==============================================================================
# 八、几何辅助（架空星图 / 航线计算共用）
# ==============================================================================

def vec3_sub(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vec3_len(v: Sequence[float]) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def vec3_dist(a: Sequence[float], b: Sequence[float]) -> float:
    """三维欧氏距离（架空星图中 1 单位 = 1 光年）。"""
    return vec3_len(vec3_sub(a, b))


def vec3_norm(v: Sequence[float]) -> Tuple[float, float, float]:
    n = vec3_len(v)
    if n < 1e-12:
        return (0.0, 0.0, 1.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def vec3_cross(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def vec3_dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def format_distance_ly(ly: float) -> str:
    """光年距离格式化：小于 1000 光年显示一位小数，否则用科学计数。"""
    if ly < 1000:
        return f"{ly:,.1f} 光年"
    if ly < 1e6:
        return f"{ly / 1000.0:,.2f} 千光年"
    return f"{ly:.3e} 光年"


__all__ = [
    "APP_NAME", "APP_SHORT", "APP_VERSION", "DEFAULT_LAT", "DEFAULT_LON",
    "DEFAULT_LOCATION_NAME", "LIMITING_MAGNITUDE",
    "is_frozen", "base_dir", "app_dir", "asset_dir", "asset_path", "ensure_dir", "saves_dir",
    "clamp", "lerp", "norm360", "norm180", "angle_delta", "smoothstep",
    "fmt_float", "fmt_ra", "fmt_dec", "fmt_alt_az", "azimuth_to_compass",
    "mag_to_radius", "mag_to_alpha", "bv_to_rgb", "spectral_to_rgb",
    "hex_to_rgb", "rgb_to_hex", "mix_color", "rgb_str",
    "now_local", "format_datetime", "format_clock", "format_gametime",
    "human_duration", "timestamp_tag",
    "LogEntry", "AppLog", "LOG", "safe_call",
    "save_json", "load_json", "list_json_files",
    "DARK_BG", "DARK_BG_ALT", "DARK_BG_DEEP", "DARK_TEXT", "DARK_TEXT_DIM",
    "DARK_TEXT_LABEL", "DARK_ACCENT", "build_dark_palette", "apply_dark_theme",
    "vec3_sub", "vec3_len", "vec3_dist", "vec3_norm", "vec3_cross", "vec3_dot",
    "format_distance_ly",
    "locate_by_ip",
]
