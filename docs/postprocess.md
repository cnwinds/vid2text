# 转录后处理（LLM 纠错）

Whisper 等 ASR 引擎转录中文口播时，常见问题包括：同音字错误、无标点、断句混乱。本模块在 STT 完成后，结合**视频标题**和**描述**作为上下文，用 LLM 整理转录稿。

## 工作流程

```
STT 原始转录
    ↓
correct_transcript(title, description, raw_transcript)
    ↓
LLM 后端（OpenAI 兼容 / Ollama）或规则兜底
    ↓
加标点、分段、修正明显 ASR 错误
```

## 模块用法

```python
from douyin_to_text.postprocess import correct_transcript, get_active_backend

title = "MrBeast 末日地堡"
description = "世界最最神秘最坚固的末日核地堡 价值超过10亿美元..."
raw = "这座大山里面用藏地一座造家十亿美元的多地宝..."

print(get_active_backend())  # 查看当前会使用的后端
corrected = correct_transcript(title, description, raw)
print(corrected)
```

### 函数签名

```python
def correct_transcript(
    title: str,
    description: str,
    raw_transcript: str,
    **kwargs,
) -> str
```

可选 `kwargs`：

| 参数 | 说明 |
|------|------|
| `provider` | `auto`（默认）、`openai`、`ollama`、`fallback` |
| `model` | 覆盖模型名 |
| `base_url` | 覆盖 API 地址 |
| `api_key` | 覆盖 API Key |
| `timeout` | HTTP 超时（秒，默认 120） |

## CLI 集成

转录完成后**默认开启**后处理；用 `--no-correct` 跳过：

```bash
# 默认：STT + LLM 后处理
python -m douyin_to_text.cli "https://..." -o output.txt

# 仅原始 Whisper 输出
python -m douyin_to_text.cli "https://..." --no-correct -o output.txt
```

平台字幕不会被后处理（已足够准确）；仅 **口播转录** 路径会触发。

## 环境变量配置

### 优先级（auto 模式）

1. 设置了 `OPENAI_API_KEY` → OpenAI 兼容 API
2. 否则尝试 Ollama 本地服务
3. 都不可用 → **规则兜底**（同音词表 + 启发式标点）

### OpenAI / DeepSeek / 硅基流动等（OpenAI 兼容）

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.openai.com/v1"   # 可选
export OPENAI_MODEL="gpt-4o-mini"                     # 可选
```

DeepSeek 示例：

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.deepseek.com/v1"
export OPENAI_MODEL="deepseek-chat"
```

硅基流动示例：

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.siliconflow.cn/v1"
export OPENAI_MODEL="Qwen/Qwen2.5-7B-Instruct"
```

### Ollama（本地免费）

```bash
# 先安装并拉模型
ollama pull qwen2.5:7b

export OLLAMA_BASE_URL="http://localhost:11434"  # 默认
export OLLAMA_MODEL="qwen2.5:7b"                   # 默认
```

### 强制指定后端

```bash
export LLM_PROVIDER="openai"    # 或 ollama / fallback
```

## Prompt 设计要点

针对中文口播/解说类视频优化：

- **系统提示**：角色为文稿编辑，强调「修正 ASR 错误、加标点、分段、保持原意不编造」
- **用户提示**：注入标题、描述、原始转录三段上下文
- **温度**：0.2，减少创造性发挥
- **输出约束**：仅正文，无 markdown / JSON

## 规则兜底

无 API Key 且 Ollama 不可用时自动启用：

1. **同音词替换表**：针对地堡/MrBeast 等常见误识别（如「地宝→地堡」）
2. **启发式标点**：在语气词（吗、呢、吧）后加句号，连词前加逗号
3. **分段**：每 4 句合并为一段

兜底效果有限，建议配置 OpenAI 兼容 API 或 Ollama 以获得更好质量。

## 示例对比（MrBeast 地堡，前 500 字）

**描述：**
> 世界最最神秘最坚固的末日核地堡 价值超过10亿美元的世界最昂贵核地堡。从1美元的地堡到最贵地堡应有尽有

**原始 ASR（节选）：**
> 这座大山里面用藏地一座造家十亿美元的多地宝它能够给预游是以来最大原子弹的冲击在学下来的视频中会向你们展示造架五千万美元的地宝以及其他各种地宝首先要介绍这个一美元的地宝安全 进里面看看

**规则兜底输出（节选）：**
> 这座大山里面用藏地一座造价十亿美元的核地堡，它能够给抵御是以来最大原子弹的冲击，在学下来的视频中会向你们展示，造价五千万美元的核地堡，以及其他各种核地堡，首先要介绍这个一美元的地堡安全，进里面看看。

**LLM 输出（预期）：**
> 这座大山里面隐藏着一座造价十亿美元的核地堡，它能够抵御有史以来最大原子弹的冲击。在接下来的视频中，我会向你们展示造价五千万美元的地堡，以及其他各种地堡。首先要介绍的是这个一美元的地堡——安全吗？进里面看看。

## 依赖

- `httpx`（已在 `requirements.txt` 中）
- 无需额外安装 OpenAI SDK（直接调用 REST API）
