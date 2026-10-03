FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# GPU 서버는 ORT=onnxruntime-gpu[cuda,cudnn] 로 빌드한다 (docker-compose.gpu.yml). CUDA·cuDNN은 pip로 함께 받는다
ARG ORT=onnxruntime
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$ORT" != "onnxruntime" ]; then \
         pip uninstall -y onnxruntime && pip install --no-cache-dir "$ORT"; fi

COPY booth booth
COPY scripts scripts
# 매팅 모델도 빌드 때 받아 둔다 (GPU 서버는 resnet50)
ARG MATTING=mobilenetv3
ENV YS_MATTING_MODEL=$MATTING
RUN python scripts/fetch_models.py

COPY . .

ENV PORT=8080
EXPOSE 8080
CMD ["python", "main.py"]
