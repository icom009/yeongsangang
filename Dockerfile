FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY booth booth
COPY scripts scripts
RUN python scripts/fetch_models.py

COPY . .

ENV PORT=8080
EXPOSE 8080
CMD ["python", "main.py"]
