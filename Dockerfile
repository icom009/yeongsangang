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

# 한마디 글씨체에 없는 글자(♥ 등)를 찾는 데 쓴다. 위의 무거운 층(CUDA)을 다시 받지 않도록 따로 설치
RUN pip install --no-cache-dir fonttools

COPY booth booth
COPY scripts scripts
# 매팅 모델도 빌드 때 받아 둔다 (GPU 서버는 resnet50 + birefnet)
ARG MATTING=mobilenetv3
ARG SEGMENT=
ENV YS_MATTING_MODEL=$MATTING YS_SEGMENT_MODEL=$SEGMENT
RUN python scripts/fetch_models.py

COPY . .

ENV PORT=8080
EXPOSE 8080
CMD ["python", "main.py"]
