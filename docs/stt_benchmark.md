# STT 引擎对比测试报告

> 生成时间：2026-07-17  
> 测试环境：Windows 11 / Python 3.13 / CPU / int8 量化（faster-whisper）

## 1. 背景与目标

项目原先使用 **openai-whisper base** 做口播兜底，用户反馈正确率很低。本报告调研 2025–2026 年可用的免费/开源中文 STT 方案，并在同一测试音频上实测对比。

## 2. 测试配置

| 项目 | 值 |
|------|-----|
| 测试音频 | `debug/benchmark/zh_sample.wav`（46s，16kHz mono） |
| 参考文本 | `debug/benchmark/reference.txt`（edge-tts 合成简体中文，用于字符级准确率） |
| 指标 | 字符准确率（去空格 SequenceMatcher）、耗时、峰值内存、模型磁盘占用 |
| 语言参数 | `zh` |

> 另备真实口播样本 `debug/benchmark/zh_speech.wav`（B站 90s 剪辑），可用于人工听感对比，未纳入自动评分。

## 3. 实测结果

| 引擎 | 模型 | 耗时(s) | 峰值内存(MB) | 模型大小(MB) | 字符准确率 | 状态 |
|------|------|---------|--------------|--------------|------------|------|
| whisper | base | 5.8 | 138.5 | 145 | **57.4%** | ✅ |
| faster-whisper | base | 3.8 | ~50 | 142 | **58.4%** | ✅ |
| faster-whisper | small | 10.5 | 51.4 | 461 | **87.4%** | ✅ 推荐 |
| faster-whisper | distil-large-v3 | 21.1 | 50.7 | 1443 | 0.9% | ⚠️ 中文输出异常 |
| sensevoice | iic/SenseVoiceSmall | — | — | ~230 | — | ⏭ 未安装（见下文） |

### 转录抽样对比

**whisper / base（57.4%）** — 繁体、错字多（「與音識別」「8盒」「曹砸環境」）：

> 大家好 歡迎收看今天的科技評測…處理器採用8盒架構…在曹砸環境下的表現…

**faster-whisper / small（87.4%）** — 简体、语义正确，仅少量标点/用词差异（「嘲杂」应为「嘈杂」）：

> 大家好,欢迎收看今天的科技评测。我们今天要测试的是语音识别系统的准确率。处理器采用8核架构,内存为16GB…

**faster-whisper / distil-large-v3** — 检测为中文但输出英文乱码，**不推荐用于中文**：

> Hello, welcome today's technology review. We're today to testes the UyN.S.P.E.N…

## 4. 方案调研摘要（2025–2026）

| 方案 | 类型 | 模型大小 | 中文表现 | CPU 速度 | 备注 |
|------|------|----------|----------|----------|------|
| **faster-whisper small** | Whisper CTranslate2 | ~460 MB | ⭐⭐⭐⭐ 实测 87% | 中等 | **本次推荐默认** |
| faster-whisper large-v3-turbo | Whisper 蒸馏加速 | ~1.6 GB | ⭐⭐⭐⭐⭐ 文献优 | 较慢 | 精度高，体积大 |
| faster-whisper distil-large-v3 | Whisper 蒸馏 | ~1.4 GB | ⭐ 中文实测差 | 慢 | 本次中文输出异常 |
| openai-whisper base/small | PyTorch 原版 | 145–460 MB | ⭐⭐ base 实测 57% | 慢 / 内存高 | 繁体倾向、错字多 |
| **SenseVoice Small** | 阿里非自回归 | ~230 MB | ⭐⭐⭐⭐⭐ 业界中文 SOTA 级 | 快（15× Whisper） | 需 funasr-onnx |
| Paraformer / Zipformer | FunASR 流式 | 100–300 MB | ⭐⭐⭐⭐ 中文专优 | 快 | 通过 FunASR 使用 |
| whisper.cpp / GGUF | C++ 本地推理 | 可变 | ⭐⭐⭐ | 快 | 中文弱于 SenseVoice |
| FunASR llama.cpp 运行时 | 单二进制 | q8 量化减半 | ⭐⭐⭐⭐⭐ | 极快 | 无需 Python，待集成 |

### 关键结论

1. **Whisper base 对中文口播不够**：易出繁体、同音错字（「地堡→地宝」类问题在口播场景常见）。
2. **faster-whisper small 是性价比最优解**：准确率提升 30 个百分点，内存仅为 whisper base 的 37%，模型 461 MB 可接受。
3. **distil-large-v3 不能假设中文友好**：体积大 3 倍且本次实测失败，需单独验证后再用。
4. **SenseVoice 值得作为下一步**：中文专优、模型更小，但 `funasr-onnx` 在 Python 3.13 / Windows 上安装失败（numpy 构建链问题），建议 Python 3.10–3.12 环境或 FunASR llama.cpp 二进制。

## 5. 推荐

### 默认引擎

```
引擎: faster-whisper
模型: small
```

**理由：**

- 字符准确率 **87.4%**，比 whisper base 提升 **+30%**
- 峰值内存 **~51 MB**（whisper base 为 138 MB）
- CPU 46s 音频约 **10.5s** 完成，可接受
- 免费、纯本地、依赖已纳入 `requirements.txt`

### 首次使用：下载模型

Windows 上 HuggingFace 缓存 symlink 可能损坏，建议下载到项目本地目录：

```powershell
$env:PYTHONIOENCODING='utf-8'
python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-small', local_dir='models/faster-whisper-small')"
```

引擎会自动优先使用 `models/faster-whisper-{model}/model.bin`。

## 6. 如何切换引擎

### CLI

```bash
# 默认（faster-whisper + small）
python -m douyin_to_text.cli "https://v.douyin.com/xxx" -o output.txt

# 回退到 whisper base
python -m douyin_to_text.cli "URL" --stt-engine whisper --whisper-model base

# 高精度（更慢、更大）
python -m douyin_to_text.cli "URL" --stt-engine faster-whisper --whisper-model large-v3-turbo

# SenseVoice（需先 pip install funasr-onnx）
python -m douyin_to_text.cli "URL" --stt-engine sensevoice
```

### Python API

```python
from pathlib import Path
from douyin_to_text.stt_engine import transcribe, transcribe_with_stats

text = transcribe(Path("audio.wav"), engine="faster-whisper", model="small")

result = transcribe_with_stats(Path("audio.wav"))
print(result.text, result.elapsed_sec, result.peak_memory_mb)
```

### 重新跑 benchmark

```bash
python scripts/benchmark_stt.py
python scripts/benchmark_stt.py --engines faster-whisper:small sensevoice:iic/SenseVoiceSmall
```

## 7. 依赖安装

```bash
# 核心（已默认）
pip install faster-whisper

# 可选：OpenAI Whisper 原版
pip install openai-whisper torch

# 可选：SenseVoice（建议 Python 3.10–3.12）
pip install funasr-onnx
# 或完整 FunASR
pip install funasr modelscope torch
```

## 8. 后续优化建议

1. **集成 SenseVoice**：在 Python 3.12 虚拟环境或 Docker 中验证后作为 `--stt-engine sensevoice` 选项。
2. **口播场景微调**：对抖音解说类视频，可加 `--language zh` + 热词提示（faster-whisper `initial_prompt`）。
3. **GPU 加速**：有 CUDA 时将 `device=cuda`, `compute_type=float16`，small 模型可降至 2–3s。
4. **平台字幕优先**：有 CC/自动字幕时仍走字幕接口，避免 STT（已在 CLI 实现）。

---

*原始 JSON 结果：`debug/benchmark/results.json`*
