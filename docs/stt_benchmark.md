# STT 引擎对比测试报告

> 生成时间：2026-07-17  
> 测试环境：Windows 11 / **Python 3.13** / CPU / int8 量化（faster-whisper）

## 1. 背景与目标

项目原先使用 **openai-whisper base** 做口播兜底，用户反馈正确率很低。本报告在同一测试音频上对比 **faster-whisper small**（当前默认）与 **SenseVoice Small**（阿里 FunASR，中文专优）。

## 2. 测试配置

| 项目 | 值 |
|------|-----|
| 测试音频 | `debug/benchmark/zh_sample.wav`（46s，16kHz mono） |
| 参考文本 | `debug/benchmark/reference.txt`（edge-tts 合成简体中文，用于字符级准确率） |
| 指标 | 字符准确率（去空格 SequenceMatcher）、耗时、峰值内存、模型磁盘占用 |
| 语言参数 | `zh` |

## 3. 实测结果（Python 3.13）

| 引擎 | 模型 | 耗时(s) | 峰值内存(MB) | 模型大小(MB) | 字符准确率 | 状态 |
|------|------|---------|--------------|--------------|------------|------|
| faster-whisper | small | 12.7 | 51.4 | 461 | **87.4%** | ✅ 备选（自带标点） |
| sensevoice | iic/SenseVoiceSmall-onnx | 13.7 | 98.1 | 230 | **93.8%** | ✅ **当前默认** |

### 转录抽样对比

**faster-whisper / small（87.4%）** — 有标点，但同音错字「嘲杂」应为「嘈杂」，数字为阿拉伯数字：

> 大家好,欢迎收看今天的科技评测。…处理器采用8核架构,内存为16GB,存储空间512GB。…在嘲杂环境下的表现。…

**sensevoice / iic/SenseVoiceSmall-onnx（93.8%）** — 语义与同音字更准确（「嘈杂」「八核」），ITN 将数字转为中文读法；无标点、个别英文单位有空格（「十六 g b」）：

> 大家好欢迎收看今天的科技评测…处理器采用八核架构内存为十六 g b 存储空间五百一十二 g b…在嘈杂环境下的表现…

### 关键差异

| 维度 | faster-whisper small | SenseVoice ONNX |
|------|---------------------|-----------------|
| 字符准确率 | 87.4% | **93.8%（+6.4%）** |
| 耗时（46s 音频） | 12.7s | 13.7s（略慢 ~8%） |
| 峰值内存 | **51 MB** | 98 MB |
| 模型体积 | 461 MB | **230 MB** |
| 标点 | ✅ 较好 | ❌ 归一化后丢失 |
| 中文同音字 | 「嘲杂」 | **「嘈杂」** ✅ |
| 数字读法 | 16GB / 512GB | 十六GB / 五百一十二GB（更贴近参考 TTS） |
| 依赖 | `requirements.txt` 已含 | 需额外 `funasr-onnx modelscope jieba` |

## 4. Python 3.13 安装验证

### 成功路径（本次采用）

```powershell
pip install funasr-onnx modelscope jieba
# 注意：勿让 funasr-onnx 将 numpy 降级到 1.26.x（会导致 Windows 3.13 崩溃）
pip install "numpy>=2.0,<2.3" --force-reinstall --only-binary=:all:
```

- 使用 **预导出 ONNX 模型** `iic/SenseVoiceSmall-onnx`（`quantize=True`），无需完整 `funasr` 包。
- ONNX 包缺少 `chn_jpn_yue_eng_ko_spectok.bpe.model`，引擎会自动从 `iic/SenseVoiceSmall` 补齐（见 `stt_engine.py`）。

### 3.13 失败项（已知限制）

| 方案 | 错误 | 说明 |
|------|------|------|
| funasr-onnx + numpy 1.26.4 | 进程崩溃（MINGW numpy） | funasr-onnx 依赖 pin `numpy<=1.26.4`，与 Py3.13 不兼容 |
| 完整 funasr | `editdistance` 编译失败 | Py3.13 无预编译 wheel |
| iic/SenseVoiceSmall（非 ONNX） | 需 funasr 导出 ONNX | 同上，无法在 3.13 自助导出 |

### Fallback（仅当 3.13 完全不可用时）

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-stt-optional.txt
```

## 5. 推荐结论

### 中文准确率优先 → 推荐 SenseVoice

```bash
pip install -r requirements-stt-optional.txt
python -m douyin_to_text.cli "URL" --stt-engine sensevoice
```

**理由：** 字符准确率 **93.8%**，比 faster-whisper small 高 **+6.4%**；模型仅 **230 MB**（约为 Whisper small 一半）；同音字与中文数字读法更符合口播场景。

### 保持 faster-whisper 为备选

```bash
python -m douyin_to_text.cli "URL" --stt-engine faster-whisper --whisper-model small
```

**理由：** 内存更低（51 MB vs 98 MB），输出自带标点；无需 ModelScope 首次下载。

> **总结：代码默认引擎已切换为 `sensevoice`**（`iic/SenseVoiceSmall-onnx`）。中文口播准确率 93.8%，配合 LLM 后处理补标点。需要自带标点时可回退 faster-whisper。

## 6. 如何切换引擎

### CLI

```bash
# 默认（sensevoice）
python -m douyin_to_text.cli "https://v.douyin.com/xxx" -o output.txt

# 回退 faster-whisper（自带标点）
python -m douyin_to_text.cli "URL" --stt-engine faster-whisper --whisper-model small

# 回退 whisper base
python -m douyin_to_text.cli "URL" --stt-engine whisper --whisper-model base
```

### Python API

```python
from pathlib import Path
from douyin_to_text.stt_engine import transcribe, transcribe_with_stats

# 中文推荐
text = transcribe(Path("audio.wav"), engine="sensevoice")

result = transcribe_with_stats(Path("audio.wav"), engine="faster-whisper", model="small")
print(result.text, result.elapsed_sec, result.char_accuracy)
```

### 重新跑 benchmark

```bash
python scripts/benchmark_stt.py --engines faster-whisper:small sensevoice:iic/SenseVoiceSmall-onnx
```

## 7. 依赖安装

```bash
# 核心（已默认）
pip install faster-whisper

# SenseVoice（Python 3.13 实测可行）
pip install -r requirements-stt-optional.txt
# 或：pip install funasr-onnx modelscope jieba
```

---

*原始 JSON 结果：`debug/benchmark/results.json`*
