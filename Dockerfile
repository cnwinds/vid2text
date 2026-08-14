FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

WORKDIR /app

# 可选国内 apt 镜像（默认阿里云公网；海外构建可 --build-arg APT_MIRROR=）
ARG APT_MIRROR=http://mirrors.aliyun.com
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|http://archive.ubuntu.com|${APT_MIRROR}|g; s|http://security.ubuntu.com|${APT_MIRROR}|g" /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null || true; \
      sed -i "s|http://archive.ubuntu.com|${APT_MIRROR}|g; s|http://security.ubuntu.com|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# pip 索引可覆盖（默认阿里云公网；勿用 ECS 内网 mirrors.cloud.aliyuncs.com，CI/本地会失败）
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    PIP_DEFAULT_TIMEOUT=300

COPY requirements-docker.txt .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements-docker.txt

# 构建阶段下载 SenseVoice 模型并打入镜像（运行时不再访问 ModelScope）
COPY scripts/download_sensevoice_models.py scripts/download_sensevoice_models.py
ENV MODELSCOPE_DOMAIN=www.modelscope.cn \
    MODELSCOPE_CACHE=/tmp/modelscope-cache
RUN python scripts/download_sensevoice_models.py \
    && rm -rf /tmp/modelscope-cache

COPY douyin_to_text/ douyin_to_text/
COPY web/ web/
COPY run_web.py .

RUN mkdir -p /app/data/work

ENV PYTHONUNBUFFERED=1 \
    SENSEVOICE_OFFLINE=1 \
    SENSEVOICE_MODEL_DIR=/app/models/SenseVoiceSmall-onnx

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
