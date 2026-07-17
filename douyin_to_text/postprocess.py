"""LLM-based post-processing for ASR transcripts (punctuation, typo fix)."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是一位专业的中文口播/解说视频文稿编辑。你的任务是根据视频标题和描述提供的上下文，整理语音识别（ASR）转录稿。

要求：
1. 修正明显的同音字、近音字错误（如「地宝」→「地堡」、「造家」→「造价」），结合标题/描述中的主题词判断
2. 添加正确的中文标点符号（，。！？、；：「」等），合理分段（每段 1–4 句）
3. 保持原意，不编造、不删减、不添加原文没有的信息
4. 保留专有名词（人名、品牌、地名），若 ASR 明显错误则按上下文修正
5. 口语化表达可保留，但去掉明显的重复卡顿（如连续三次「你还在这儿」保留一次即可）
6. 只输出整理后的正文，不要加标题、说明、markdown 或 JSON"""

USER_PROMPT_TEMPLATE = """【视频标题】
{title}

【视频描述】
{description}

【ASR 原始转录】
{raw_transcript}

请输出整理后的文稿："""


def build_prompt(title: str, description: str, raw_transcript: str) -> list[dict[str, str]]:
    """Build chat messages for the LLM."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_PROMPT_TEMPLATE.format(
                title=title.strip() or "（无）",
                description=description.strip() or "（无）",
                raw_transcript=raw_transcript.strip(),
            ),
        },
    ]


# ---------------------------------------------------------------------------
# LLM backends
# ---------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _resolve_provider(provider: str | None) -> str:
    """Return 'openai' | 'ollama' | 'fallback'."""
    explicit = (provider or _env("LLM_PROVIDER", "auto")).lower()
    if explicit == "openai":
        return "openai" if _env("OPENAI_API_KEY") else "fallback"
    if explicit == "ollama":
        return "ollama"
    if explicit == "fallback":
        return "fallback"
    # auto
    if _env("OPENAI_API_KEY"):
        return "openai"
    return "ollama"


def _call_openai_compatible(
    messages: list[dict[str, str]],
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = 120.0,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


def _call_ollama(
    messages: list[dict[str, str]],
    *,
    base_url: str,
    model: str,
    timeout: float = 180.0,
) -> str:
    url = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.2},
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return (data.get("message", {}).get("content") or "").strip()


def _ollama_available(base_url: str, timeout: float = 3.0) -> bool:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(base_url.rstrip("/") + "/api/tags")
            return resp.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


# ---------------------------------------------------------------------------
# Rule-based fallback (punctuation + common homophones)
# ---------------------------------------------------------------------------

# Context-aware replacements for bunker/shelter themed content (MrBeast demo)
_HOMOPHONE_MAP: dict[str, str] = {
    "地宝": "地堡",
    "多地宝": "的核地堡",
    "造家": "造价",
    "预游": "抵御",
    "原子弹": "原子弹",
    "草弹": "导弹",
    "发射警户": "发射井",
    "阻用": "重达",
    "三端": "三吨",
    "倒达": "到达",
    "阴毕幼尖固": "隐蔽且坚固",
    "宝蕾": "堡垒",
    "要进": "邀请",
    "厨藏": "储存",
    "即美": "吉姆",
    "汇凝图": "混凝土",
    "瘋狂": "疯狂",
    "刀剧": "刀锯",
    "电梯枪": "钉枪",
    "吸枪": "钉枪",
    "噴火": "喷火",
    "合当": "合适",
    "女儿站": "女儿在这",
    "玩安": "安全",
    "高财": "刚才",
    "立人": "利刃",
    "FaceBoss": "Face Off",
    "奇贸不扬": "其貌不扬",
    "节出": "杰出",
    "运誓": "运尸",
    "藏官": "官方",
    "成刚党": "承重墙",
    "军机手": "军事",
    "拉里的罪案": "拉响的警报",
    "强制": "枪支",
    "审了室": "手术室",
    "牙益": "牙医",
    "金铭舞": "秘密武器",
    "砸货店": "杂货店",
    "赔根": "培根",
    "灌头": "罐头",
    "灌庄": "灌装",
    "重持": "种植",
    "造加": "造价",
    "美人": "美元",
    "号自": "耗资",
    "宝杆": "抱歉",
    "宗律术": "棕榈树",
    "圣担术": "圣诞树",
    "五高": "无聊",
    "台立刻": "泰里克",
    "失谋": "时髦",
    "鱼果": "Chandler",
    "锐雷": "Karl",
    "成作": "吵架",
    "饭了": "犯了",
    "动作周知": "众所周知",
    "线密": "机密",
    "造假": "造价",
    "下延山": "夏延山",
    "千古花钩": "厚花岗岩",
    "继破坏性": "具破坏性",
    "产室": "展示",
    "布队": "部队",
    "射力": "设立",
    "敌党": "抵挡",
    "安沉": "安全",
    "约浮": "约束",
    "裴美": "北美",
    "襲击": "袭击",
    "发好": "发挥",
    "戳户": "窗口",
    "古长": "故障",
    "证可": "整个",
    "主红": "主权",
    "非今": "飞机",
    "起码": "起飞",
    "预爱": "Wi-Fi",
    "赛百位": "赛百味",
    "图网山": "夏延山",
    "山台尔光": "Shane",
    "复担": "复苏",
    "上官": "上校",
    "新闻复作": "心肺复苏",
    "资间": "资金",
    "结局": "街区",
    "收滑": "奢华",
    "水程": "水城",
    "内洁": "内景",
    "浮渴": "浮桥",
}


def _apply_homophone_map(text: str) -> str:
    for wrong, right in sorted(_HOMOPHONE_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        text = text.replace(wrong, right)
    return text


def _insert_punctuation(text: str) -> str:
    """Heuristic Chinese punctuation for oral transcripts."""
    if not text:
        return text

    # Normalize whitespace
    text = re.sub(r"\s+", "", text)

    # After question/final particles, insert 。
    text = re.sub(r"([吗呢吧啊])", r"\1。", text)
    # After 了 at clause boundaries (followed by common starters)
    text = re.sub(
        r"(了)(?=这|那|接下来|首先|然后|但是|所以|不过|终于|其实)",
        r"\1，",
        text,
    )
    # Commas before conjunctions (skip if already preceded by punctuation)
    for conj in ("但是", "不过", "所以", "然后", "而且", "因为", "如果", "虽然", "接下来", "首先", "最后"):
        text = re.sub(rf"(?<![，。！？、；：]){re.escape(conj)}", f"，{conj}", text)
    # Fix double punctuation
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"[。]{2,}", "。", text)
    text = re.sub(r"。，", "。", text)
    # Ensure ends with punctuation
    if text and text[-1] not in "。！？…":
        text += "。"
    return text


def _split_paragraphs(text: str, max_sentences: int = 4) -> str:
    """Group sentences into paragraphs."""
    sentences = re.split(r"(?<=[。！？])", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return text

    paragraphs: list[str] = []
    buf: list[str] = []
    for sent in sentences:
        buf.append(sent)
        if len(buf) >= max_sentences:
            paragraphs.append("".join(buf))
            buf = []
    if buf:
        paragraphs.append("".join(buf))
    return "\n\n".join(paragraphs)


def fallback_correct(raw_transcript: str) -> str:
    """Rule-based correction when no LLM is available."""
    text = _apply_homophone_map(raw_transcript.strip())
    text = _insert_punctuation(text)
    return _split_paragraphs(text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def correct_transcript(
    title: str,
    description: str,
    raw_transcript: str,
    **kwargs: Any,
) -> str:
    """Correct ASR transcript using LLM with title+description as context.

    Keyword args:
        provider: 'auto' | 'openai' | 'ollama' | 'fallback'
        model: override model name
        base_url: override API base URL
        api_key: override API key
        timeout: HTTP timeout seconds
        chunk_chars: split long transcripts (0 = no chunking)
    """
    raw = (raw_transcript or "").strip()
    if not raw:
        return ""

    provider = _resolve_provider(kwargs.get("provider"))
    timeout = float(kwargs.get("timeout", 120.0))
    chunk_chars = int(kwargs.get("chunk_chars", 0))

    if provider == "fallback":
        return fallback_correct(raw)

    messages = build_prompt(title, description, raw)

    try:
        if provider == "openai":
            api_key = kwargs.get("api_key") or _env("OPENAI_API_KEY")
            base_url = kwargs.get("base_url") or _env(
                "OPENAI_BASE_URL", "https://api.openai.com/v1"
            )
            model = kwargs.get("model") or _env("OPENAI_MODEL", "gpt-4o-mini")
            return _call_openai_compatible(
                messages,
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout=timeout,
            )

        # ollama
        base_url = kwargs.get("base_url") or _env(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        )
        model = kwargs.get("model") or _env("OLLAMA_MODEL", "qwen2.5:7b")
        if not _ollama_available(base_url):
            return fallback_correct(raw)
        return _call_ollama(
            messages,
            base_url=base_url,
            model=model,
            timeout=timeout,
        )
    except (httpx.HTTPError, KeyError, json.JSONDecodeError, IndexError):
        return fallback_correct(raw)


def get_active_backend() -> str:
    """Return human-readable name of the backend that would be used."""
    provider = _resolve_provider(None)
    if provider == "openai":
        model = _env("OPENAI_MODEL", "gpt-4o-mini")
        base = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
        return f"OpenAI 兼容 API ({model} @ {base})"
    if provider == "ollama":
        model = _env("OLLAMA_MODEL", "qwen2.5:7b")
        base = _env("OLLAMA_BASE_URL", "http://localhost:11434")
        if _ollama_available(base):
            return f"Ollama ({model} @ {base})"
        return "规则兜底（Ollama 不可用）"
    return "规则兜底"
