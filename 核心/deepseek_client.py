# -*- coding: utf-8 -*-
"""
deepseek_client.py —— DeepSeek API 客户端
================================================================================
用途：为「架空星图」的**历史小说生成**提供大模型能力。整个模块只用标准库
（urllib）实现，不引入 openai / requests 等依赖，方便 PyInstaller 打包。

功能：
    * DeepSeekClient        同步与流式（SSE）对话，兼容 OpenAI chat/completions 协议
    * DeepSeekConfig        API Key / 模型 / 参数配置，支持环境变量与本地文件持久化
    * build_story_prompt()  把架空宇宙的世界设定转成高质量的中文提示词
    * generate_story()      便捷函数：一次性生成整篇小说
    * generate_story_stream() 流式生成，逐段回调（界面可实时显示）

配置优先级（从高到低）：
    1. 程序内设置（设置对话框）
    2. 环境变量 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
    3. 本地配置文件  <应用目录>/deepseek_config.json
    4. 内置默认值（api.deepseek.com、deepseek-chat）

安全提示：API Key 仅保存在本机配置文件中，不会随世界设定 JSON 导出，
也不会被写入日志。
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from 核心.utils import LOG, app_dir, ensure_dir, clamp, load_json, save_json

# ==============================================================================
# 常量
# ==============================================================================

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
REASONER_MODEL = "deepseek-reasoner"

AVAILABLE_MODELS: Tuple[Tuple[str, str], ...] = (
    ("deepseek-chat", "DeepSeek-V3 通用对话（推荐写小说）"),
    ("deepseek-reasoner", "DeepSeek-R1 深度推理（适合推演历史逻辑）"),
)

#: 小说风格预设
STORY_STYLES: Tuple[Tuple[str, str, str], ...] = (
    ("chronicle", "编年史",
     "以银河史官的冷峻笔调，按时间顺序记录重大事件，夹叙夹议，强调历史必然性"),
    ("epic", "史诗叙事",
     "宏大叙事，多线并行，语言厚重，注重战争、外交与文明命运的转折"),
    ("documentary", "纪实档案",
     "以解密档案、舰队日志、外交电报、学者批注等文献拼贴出历史真相"),
    ("biography", "人物传记",
     "聚焦一位舰长/外交官/考古学家的视角，用个人命运折射时代变迁"),
    ("mystery", "悬疑考据",
     "围绕一段被抹除的历史展开调查，层层揭开遗迹觉醒背后的秘密"),
    ("romance", "文明言情",
     "以两个敌对文明间的个体情感为主线，书写跨越星海的相遇与告别"),
)

STORY_LENGTHS: Tuple[Tuple[str, int, str], ...] = (
    ("short", 1200, "短篇（约 1200 字）"),
    ("medium", 2600, "中篇（约 2600 字）"),
    ("long", 5000, "长篇（约 5000 字）"),
    ("epic", 9000, "史诗（约 9000 字，分章节）"),
)


# ==============================================================================
# 配置
# ==============================================================================

@dataclass
class DeepSeekConfig:
    """DeepSeek 调用配置。"""

    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = 1.1          # 写小说需要更高的创造性
    max_tokens: int = 4096
    timeout: float = 120.0
    stream: bool = True
    system_prompt: str = (
        "你是一位精通科幻世界观构建与中文叙事的历史作家，"
        "擅长把星际文明的政治、战争、贸易与考古发现写成有质感的编年史。"
        "你的文字凝练、有画面感，善用具体细节（坐标、舰队编成、年份、条约名称）"
        "而不是空泛的形容；尊重用户给出的世界设定，不修改既定的数据。")

    # ---- 持久化 ----
    @staticmethod
    def config_path() -> str:
        """配置文件路径（放在应用目录，便于随程序携带）。"""
        return os.path.join(app_dir(), "deepseek_config.json")

    def load(self) -> "DeepSeekConfig":
        """按优先级载入配置：环境变量 > 文件 > 默认值。"""
        # 1) 文件
        data = load_json(self.config_path(), default={}) or {}
        if isinstance(data, dict):
            self.api_key = str(data.get("api_key", self.api_key) or "")
            self.base_url = str(data.get("base_url", self.base_url) or DEFAULT_BASE_URL)
            self.model = str(data.get("model", self.model) or DEFAULT_MODEL)
            self.temperature = float(data.get("temperature", self.temperature))
            self.max_tokens = int(data.get("max_tokens", self.max_tokens))
            self.timeout = float(data.get("timeout", self.timeout))
            self.stream = bool(data.get("stream", self.stream))
            if data.get("system_prompt"):
                self.system_prompt = str(data["system_prompt"])
        # 2) 环境变量（优先级更高）
        env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if env_key:
            self.api_key = env_key
        env_url = os.environ.get("DEEPSEEK_BASE_URL", "").strip()
        if env_url:
            self.base_url = env_url
        env_model = os.environ.get("DEEPSEEK_MODEL", "").strip()
        if env_model:
            self.model = env_model
        return self

    def save(self) -> bool:
        """保存配置到本地文件（不含任何敏感日志输出）。"""
        data = {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
            "stream": self.stream,
            "system_prompt": self.system_prompt,
        }
        ok = save_json(self.config_path(), data)
        if ok:
            LOG.info("DeepSeek 配置已保存")
        return ok

    def ready(self) -> bool:
        """是否已具备调用条件。"""
        return bool(self.api_key.strip())

    def masked_key(self) -> str:
        """用于界面显示的脱敏 Key。"""
        k = self.api_key.strip()
        if not k:
            return "（未配置）"
        if len(k) <= 10:
            return k[:2] + "…" + k[-2:]
        return f"{k[:6]}…{k[-4:]}（共 {len(k)} 位）"


# ==============================================================================
# 客户端
# ==============================================================================

class DeepSeekError(RuntimeError):
    """DeepSeek 调用异常（带可读的中文说明）。"""


class DeepSeekClient:
    """DeepSeek Chat Completions 客户端（标准库实现，支持流式）。"""

    def __init__(self, config: Optional[DeepSeekConfig] = None) -> None:
        self.config = config or DeepSeekConfig().load()

    # ---- 基础请求 ----

    def _endpoint(self) -> str:
        base = self.config.base_url.rstrip("/")
        if not base.endswith("/v1") and "api.deepseek.com" in base:
            base = f"{base}/v1"
        return f"{base}/chat/completions"

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key.strip()}",
            "Accept": "text/event-stream" if self.config.stream else "application/json",
            "User-Agent": "StellarNexus/1.1 (+desktop)",
        }

    def _payload(self, messages: Sequence[Dict[str, str]], stream: bool,
                 max_tokens: Optional[int] = None) -> bytes:
        body = {
            "model": self.config.model,
            "messages": list(messages),
            "temperature": clamp(self.config.temperature, 0.0, 2.0),
            "max_tokens": int(max_tokens or self.config.max_tokens),
            "stream": bool(stream),
        }
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    def _context(self):
        try:
            return ssl.create_default_context()
        except Exception:                                  # pragma: no cover
            return None

    # ---- 校验 ----

    def check(self) -> Tuple[bool, str]:
        """检查配置与网络连通性（发送一个极小的请求）。"""
        if not self.config.ready():
            return (False, "尚未配置 API Key。请在「工具 → DeepSeek 设置」中填入，"
                           "或设置环境变量 DEEPSEEK_API_KEY。")
        try:
            text = self.chat([{"role": "user", "content": "回复两个字：就绪"}],
                             stream=False, max_tokens=16)
            return (True, f"连接正常（模型 {self.config.model}）：{text.strip()[:20]}")
        except DeepSeekError as exc:
            return (False, str(exc))
        except Exception as exc:                            # pragma: no cover
            return (False, f"未预期的错误：{type(exc).__name__}: {exc}")

    # ---- 非流式 ----

    def chat(self, messages: Sequence[Dict[str, str]], stream: bool = False,
             max_tokens: Optional[int] = None) -> str:
        """一次性获取完整回复。"""
        if not self.config.ready():
            raise DeepSeekError("尚未配置 DeepSeek API Key")
        data = self._payload(messages, stream=False, max_tokens=max_tokens)
        req = urllib.request.Request(self._endpoint(), data=data,
                                     headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout,
                                        context=self._context()) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise DeepSeekError(self._http_error_text(exc)) from exc
        except urllib.error.URLError as exc:
            raise DeepSeekError(f"网络连接失败：{exc.reason}（请检查网络或代理设置）") from exc
        except Exception as exc:
            raise DeepSeekError(f"请求失败：{type(exc).__name__}: {exc}") from exc
        try:
            payload = json.loads(raw)
            return str(payload["choices"][0]["message"]["content"])
        except Exception as exc:
            raise DeepSeekError(f"响应解析失败：{raw[:200]}") from exc

    # ---- 流式 ----

    def chat_stream(self, messages: Sequence[Dict[str, str]],
                    max_tokens: Optional[int] = None) -> Iterator[str]:
        """流式获取回复，逐段 yield 文本增量（SSE 协议）。"""
        if not self.config.ready():
            raise DeepSeekError("尚未配置 DeepSeek API Key")
        data = self._payload(messages, stream=True, max_tokens=max_tokens)
        req = urllib.request.Request(self._endpoint(), data=data,
                                     headers=self._headers(), method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=self.config.timeout,
                                          context=self._context())
        except urllib.error.HTTPError as exc:
            raise DeepSeekError(self._http_error_text(exc)) from exc
        except urllib.error.URLError as exc:
            raise DeepSeekError(f"网络连接失败：{exc.reason}（请检查网络或代理设置）") from exc
        except Exception as exc:
            raise DeepSeekError(f"请求失败：{type(exc).__name__}: {exc}") from exc

        with resp:
            for raw_line in resp:
                try:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                except Exception:
                    continue
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    payload = json.loads(chunk)
                except Exception:
                    continue
                try:
                    delta = payload["choices"][0].get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        yield str(piece)
                except Exception:
                    continue

    # ---- 错误文本 ----

    @staticmethod
    def _http_error_text(exc: "urllib.error.HTTPError") -> str:
        """把 HTTP 错误翻译成用户能看懂的中文提示。"""
        code = getattr(exc, "code", 0)
        detail = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
            payload = json.loads(body)
            detail = str(payload.get("error", {}).get("message") or body)[:200]
        except Exception:
            detail = ""
        table = {
            400: "请求格式有误（可能是模型名或参数不正确）",
            401: "API Key 无效或已过期，请在「DeepSeek 设置」中重新填写",
            402: "账户余额不足，请前往 DeepSeek 平台充值",
            403: "没有访问该模型的权限",
            404: "接口地址不存在，请检查 Base URL",
            422: "参数校验失败（提示词过长或 max_tokens 超限）",
            429: "请求过于频繁，请稍后重试",
            500: "服务端内部错误，请稍后重试",
            503: "服务暂时不可用（可能是负载过高），请稍后重试",
        }
        hint = table.get(code, f"HTTP {code} 错误")
        return f"{hint}。{('详情：' + detail) if detail else ''}"


# ==============================================================================
# 提示词构建
# ==============================================================================

def build_story_system_prompt(style_key: str = "chronicle",
                              extra_requirement: str = "") -> str:
    """根据风格构建 system 提示词。"""
    style_name, style_desc = "编年史", STORY_STYLES[0][2]
    for key, name, desc in STORY_STYLES:
        if key == style_key:
            style_name, style_desc = name, desc
            break
    text = (
        "你是一位顶尖的中文科幻历史作家，正在为一部名为《星际纪元编年史》的"
        "架空宇宙正史撰写章节。写作要求：\n"
        f"1. 文体：{style_name}——{style_desc}。\n"
        "2. 严格使用用户提供的世界设定（人名、地名、年份、势力、技术等级、航线），"
        "不得杜撰与之冲突的设定；可以合理补充细节。\n"
        "3. 用具体细节支撑叙事：年份、坐标、舰队编成、条约条款、伤亡数字、"
        "条约签署地、考古发现编号等。\n"
        "4. 有起承转合：开头设置悬念或场景，中间推进冲突，结尾留下历史余响或伏笔。\n"
        "5. 语言凝练有质感，避免空洞的排比与口号；不要使用 Markdown 标题符号，"
        "用自然段落（需要分章时用「第一章」这样的行首文字）。\n"
        "6. 全文使用简体中文。"
    )
    if extra_requirement.strip():
        text += f"\n7. 额外要求：{extra_requirement.strip()}"
    return text


def _faction_brief(galaxy: Any, limit: int = 6) -> str:
    """势力简介文本。"""
    lines: List[str] = []
    for f in list(getattr(galaxy, "factions", []))[:limit]:
        relations = "、".join(f"{k}：{v}" for k, v in list(getattr(f, "relations", {}).items())[:4])
        lines.append(
            f"- {f.name}（{getattr(f, 'ideology', '')}）首都 {getattr(f, 'capital', '')}，"
            f"领土 {len(getattr(f, 'member_systems', []))} 个星系，"
            f"军事 {getattr(f, 'military', 0):.2f} / 经济 {getattr(f, 'economy', 0):.2f} / "
            f"科技 {getattr(f, 'tech', 0):.2f}。外交：{relations or '暂无'}")
    return "\n".join(lines) or "- （暂无势力）"


def _civilization_brief(galaxy: Any, limit: int = 6) -> str:
    """文明简介文本。"""
    lines: List[str] = []
    for c in list(getattr(galaxy, "civilizations", []))[:limit]:
        traits = "、".join(getattr(c, "traits", [])[:4])
        lines.append(
            f"- {c.name}（种族：{c.species}）科技 Lv.{c.tech_level} {c.tech_name}，"
            f"政体 {c.government}，意识形态 {c.ideology}，母星 {c.home_system}，"
            f"殖民地 {len(c.colonies)} 个，人口 {c.population_millions:.1f} 百万"
            f"{('，特征：' + traits) if traits else ''}")
    return "\n".join(lines) or "- （暂无文明）"


def _event_brief(galaxy: Any, limit: int = 24) -> str:
    """历史事件时间线文本。"""
    events = sorted(getattr(galaxy, "history", []), key=lambda e: e.year)
    if not events:
        return "- （暂无历史事件）"
    step = max(1, len(events) // limit)
    picked = events[::step][:limit]
    return "\n".join(f"- 银河历 {e.year:,.1f} 年【{e.category}】{e.description}"
                     f"（{e.system}，主导方 {e.faction}）" for e in picked)


def _system_brief(galaxy: Any, system_name: str = "", limit: int = 10) -> str:
    """恒星系简介（若指定星系则优先展示它）。"""
    systems = list(getattr(galaxy, "systems", []))
    if not systems:
        return "- （暂无恒星系）"
    picked = []
    if system_name:
        target = next((s for s in systems if s.name == system_name), None)
        if target is not None:
            picked.append(target)
    # 再补上有文明 / 高资源 / 高危险度的星系
    others = sorted(systems, key=lambda s: (-(s.population_millions), -s.resource_richness))
    for s in others:
        if s in picked:
            continue
        picked.append(s)
        if len(picked) >= limit:
            break
    lines: List[str] = []
    for s in picked:
        planets = "、".join(f"{p.name}({p.ptype_cn}，{p.temperature_c:+.0f}℃，"
                            f"生命迹象{p.life_signs})" for p in s.planets[:4]) or "无行星"
        lines.append(
            f"- {s.name}：光谱型 {s.spectral}，行星 {s.planet_count} 颗（{planets}），"
            f"资源丰富度 {s.resource_richness * 100:.0f}%，危险度 {s.danger * 100:.0f}%，"
            f"势力 {s.faction or '无主之地'}，人口 {s.population_millions:.1f} 百万"
            f"{('，异常：' + s.anomaly) if s.anomaly else ''}")
    return "\n".join(lines)


def build_story_prompt(galaxy: Any, style_key: str = "chronicle",
                       length_key: str = "medium", focus: str = "",
                       custom_prompt: str = "") -> Tuple[str, str, int]:
    """把架空宇宙的世界设定构建成提示词。

    返回 (system_prompt, user_prompt, max_tokens)
    """
    # 长度
    target_words = 2600
    for key, words, _label in STORY_LENGTHS:
        if key == length_key:
            target_words = words
            break
    # 中文大约 1.5 token/字，再留出思考余量
    max_tokens = int(clamp(target_words * 1.8 + 800, 1200, 16000))

    stats = galaxy.stats() if hasattr(galaxy, "stats") else {}
    system_prompt = build_story_system_prompt(style_key, custom_prompt)

    lines: List[str] = [
        "请依据下面的世界设定撰写一篇架空宇宙的历史小说。",
        "",
        "=== 宇宙基本信息 ===",
        f"名称：{getattr(galaxy, 'name', '未命名')}　种子：{getattr(galaxy, 'seed', 0)}",
        f"规模：{stats.get('systems', 0)} 个恒星系，{stats.get('planets', 0)} 颗行星，"
        f"{stats.get('routes', 0)} 条航线",
        f"当前时间：银河历 {getattr(galaxy, 'year', 0):,.2f} 年",
        f"总人口：{stats.get('population_millions', 0):,.0f} 百万",
        "",
        "=== 势力 ===",
        _faction_brief(galaxy),
        "",
        "=== 文明 ===",
        _civilization_brief(galaxy),
        "",
        "=== 重要恒星系 ===",
        _system_brief(galaxy, focus),
        "",
        "=== 历史事件时间线 ===",
        _event_brief(galaxy),
        "",
        "=== 写作任务 ===",
        f"- 篇幅：约 {target_words} 字。",
    ]
    if focus:
        lines.append(f"- 舞台中心：以恒星系「{focus}」为主要场景。")
    lines.append("- 请至少引用 5 个上面出现的具体名称（星系 / 势力 / 文明 / 事件），"
                 "并给出明确的年份。")
    lines.append("- 结尾写一段 1~2 行的「史官注」，对这段历史作出评价或留下悬念。")
    user_prompt = "\n".join(lines)
    return system_prompt, user_prompt, max_tokens


# ==============================================================================
# 便捷调用
# ==============================================================================

def generate_story(galaxy: Any, config: Optional[DeepSeekConfig] = None,
                   style_key: str = "chronicle", length_key: str = "medium",
                   focus: str = "", custom_prompt: str = "") -> str:
    """一次性生成完整小说（同步阻塞）。"""
    cfg = config or DeepSeekConfig().load()
    client = DeepSeekClient(cfg)
    system_prompt, user_prompt, max_tokens = build_story_prompt(
        galaxy, style_key, length_key, focus, custom_prompt)
    return client.chat([{"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}],
                       stream=False, max_tokens=max_tokens)


def generate_story_stream(galaxy: Any, config: Optional[DeepSeekConfig] = None,
                          style_key: str = "chronicle", length_key: str = "medium",
                          focus: str = "", custom_prompt: str = "",
                          on_chunk: Optional[Callable[[str], None]] = None) -> str:
    """流式生成小说，逐段回调 on_chunk（用于界面实时显示）。返回完整文本。"""
    cfg = config or DeepSeekConfig().load()
    client = DeepSeekClient(cfg)
    system_prompt, user_prompt, max_tokens = build_story_prompt(
        galaxy, style_key, length_key, focus, custom_prompt)
    parts: List[str] = []
    for piece in client.chat_stream([{"role": "system", "content": system_prompt},
                                     {"role": "user", "content": user_prompt}],
                                    max_tokens=max_tokens):
        parts.append(piece)
        if on_chunk is not None:
            try:
                on_chunk(piece)
            except Exception:
                pass
    return "".join(parts)


# ==============================================================================
# 本地示例（离线可用）
# ==============================================================================

def offline_sample_story(galaxy: Any, style_key: str = "chronicle") -> str:
    """在没有 API Key 或断网时生成一段基于真实世界设定的示例文本。

    这样做的好处：即使没有配置 DeepSeek，用户也能立刻看到「历史小说」这一功能的
    形态与内容组织方式，并据此判断提示词是否合适。
    """
    name = getattr(galaxy, "name", "未命名星系群")
    seed = getattr(galaxy, "seed", 0)
    year = getattr(galaxy, "year", 0.0)
    stats = galaxy.stats() if hasattr(galaxy, "stats") else {}
    factions = list(getattr(galaxy, "factions", []))
    civs = list(getattr(galaxy, "civilizations", []))
    events = sorted(getattr(galaxy, "history", []), key=lambda e: e.year)
    systems = list(getattr(galaxy, "systems", []))
    f1 = factions[0].name if factions else "无名势力"
    f2 = factions[1].name if len(factions) > 1 else "边境诸邦"
    c1 = civs[0] if civs else None
    s1 = systems[0].name if systems else "原点星"
    ev = events[:6]

    lines: List[str] = []
    lines.append(f"【{name}·编年史（本地示例）】")
    lines.append("")
    lines.append(f"银河历 {year:,.2f} 年，{name} 的星图上共标记 {stats.get('systems', 0)} 个恒星系、"
                 f"{stats.get('routes', 0)} 条航线。这份文本是在未配置 DeepSeek API Key 的情况下"
                 f"由本地模板生成的示例（种子 {seed}），用来说明「历史小说」界面将如何组织内容。")
    lines.append("")
    if c1 is not None:
        lines.append(f"第一章　{c1.name}的黎明")
        lines.append("")
        lines.append(f"{c1.name}（种族：{c1.species}）在{c1.home_system}点燃了第一座聚变塔。"
                     f"那一年，他们的科技等级被史官记为 Lv.{c1.tech_level}——{c1.tech_name}。"
                     f"{c1.government}在条约上按下印章时，星系外的黑暗里还没有任何一支舰队。")
        lines.append("")
    lines.append("第二章　裂痕")
    lines.append("")
    lines.append(f"{f1} 与 {f2} 的边境线沿着 {s1} 外的第二条航线划定。"
                 f"第一批货运船队在那里完成了三次往返，随后是巡逻舰，再随后是封锁。"
                 f"史官们在事后反复争论：真正让局势不可逆的，究竟是某一次演习的越界，"
                 f"还是谈判桌上被删掉的那一行条款。")
    lines.append("")
    lines.append("第三章　余响")
    lines.append("")
    for e in ev[:4]:
        lines.append(f"　　· 银河历 {e.year:,.1f} 年【{e.category}】{e.description}")
    lines.append("")
    lines.append("史官注：以上文字由本地模板拼接，仅用于演示排版与信息密度。"
                 "在「工具 → DeepSeek 设置」中填入 API Key 后，同样的世界设定会交给"
                 "大模型改写成真正连贯、有文学质感的历史小说。")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_BASE_URL", "DEFAULT_MODEL", "REASONER_MODEL", "AVAILABLE_MODELS",
    "STORY_STYLES", "STORY_LENGTHS",
    "DeepSeekConfig", "DeepSeekClient", "DeepSeekError",
    "build_story_prompt", "build_story_system_prompt",
    "generate_story", "generate_story_stream", "offline_sample_story",
]
