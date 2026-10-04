FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    docker.io \
    openssh-client \
    sshpass \
    tmate \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY config.json .
COPY core/ ./core/
COPY api.py .

RUN mkdir -p data backups cache

CMD ["python", "-u", "bot.py"]
