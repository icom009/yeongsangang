# IC-Light 재조명 서비스 (NVIDIA GPU 전용). 부스 서버와 따로 돌고, 없으면 AI 보정만 꺼진다.
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
# 모델(약 6GB)은 처음 켤 때 /cache로 내려받는다. docker-compose.ai.yml이 볼륨으로 붙여 두니 한 번만 받는다
ENV HF_HOME=/cache PORT=8000
EXPOSE 8000
CMD ["python", "app.py"]
