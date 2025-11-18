FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libglib2.0-0 libsm6 libxext6 libxrender-dev ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY db.py .

COPY cert.pem /app/cert.pem
COPY key.pem /app/key.pem

RUN mkdir -p /app/images  # Optional: can also be removed since host bind-mount handles it

CMD ["python", "main.py"]
