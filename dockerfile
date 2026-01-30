FROM python:3.11-alpine

WORKDIR /app

# Install system dependencies for OpenCV and other packages
RUN apk add --no-cache \
    glib \
    libsm \
    libxext \
    libxrender \
    ffmpeg \
    gcc \
    musl-dev \
    linux-headers \
    jpeg-dev \
    zlib-dev \
    libffi-dev

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY db.py .

COPY cert.pem /app/cert.pem
COPY key.pem /app/key.pem

RUN mkdir -p /app/images

CMD ["python", "main.py"]
