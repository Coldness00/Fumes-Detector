Here is a **clean, polished, perfectly formatted README.md**, ready to paste directly into GitHub.

---

```markdown
# RTSP Blackmist Detector 🚨💨

A Dockerized system that watches an **RTSP video stream** for **fume / dark smoke** using a vision‑language model, stores results in **InfluxDB**, and exposes them through a lightweight **web dashboard**.

---

## ✨ Features

- 📡 RTSP live‑stream frame extraction  
- 🤖 AI analysis using an Ollama VLM (recommended: **Qwen2.5-VL**)  
- 📈 Data storage in **InfluxDB v1**  
- 🌐 Web interface to view detections and history  
- 🔒 HTTPS support via your own `cert.pem` and `key.pem`  

---

## 🧩 Requirements

Before using this project, you must install/configure the following:

### 1. Docker  
Install Docker for your OS:  
https://docs.docker.com/get-docker/

### 2. InfluxDB v1  
Install InfluxDB v1.x and note your credentials
Fill the file accordingly

### 3. Ollama (⚠ Required for AI analysis)

- Must be installed **on the host machine**  
- Must be accessible from inside Docker → **listen on 0.0.0.0**  
- Install from: https://ollama.ai  

To expose Ollama:

```bash
ollama serve --host 0.0.0.0
```

### 4. Download the model

Recommended model:

```bash
ollama pull qwen2.5vl
```

### 5. Clone the repository

```bash
gh repo clone Coldness00/Fumes-Detector
cd rtsp-blackmist-detector
```

### 6. Provide certificates

At the root of the repo, create:

```
cert.pem
key.pem
```

These are required for HTTPS access.

---

## ⚙️ Configuration (docker-compose.yml)

You **must edit `docker-compose.yml`** before running the stack.

Below are fields you MUST understand and adapt:

---

### ❌ Do NOT change

```
FOLDER_PATH=/app/images
DB_PATH=/app/data/processed_images.db
```

---

### ✅ Must be customized

#### **BASE_URL**  
Machine’s local IP + port  
Example:  
```
http://192.168.1.15:9822
```

#### **EXTERNAL_URL**  
Use only if routing through a reverse proxy.  
Otherwise: leave it commented.

#### **REFRESH_TIME**  
Time between image captures (seconds).  
- Lower = faster detection  
- Higher load on CPU/GPU  

#### **PROMPT**  
Recommended to keep as provided.  
You can tweak, but **preserve formatting**.

#### **OLLAMA settings**  
Defaults are safe, but can be adjusted if needed.

#### **CAMERA_NAME**  
Friendly camera display name.

#### **RTSP_URL**  
Your RTSP address including **ID and password** if required.

#### **InfluxDB block**  
Adjust host, port, database, username, password.

#### **TZ**  
Timezone (example):

```
TZ=Europe/Paris
```

---

### 📁 Volumes

You must map local folders to the container:

```
./images:/app/images
./data:/app/data
```

These store:
- extracted frames  
- processed database  

---

### 🔌 Ports

Adjust if running multiple instances.


---

## 🚀 Start the project

Run the stack:

```bash
docker compose up -d
```

Check logs:

```bash
docker compose logs -f
```

---

## 🖥️ Access the Web Dashboard

Open:

```
https://<BASE_URL>:<PORT>
```

If using self‑signed certificates, your browser may show a warning—this is expected.

---

## 🗂 Project Structure

```
.
├── data/                     <- local DB and persistent data
├── images/                   <- extracted frames
├── main.py                   <- main application
├── db.py                     <- database handler
├── docker-compose.yml
├── Dockerfile
├── cert.pem / key.pem
└── requirements.txt
```

---

## ❓ Questions / Issues

Feel free to open an Issue or request enhancements.  
Contributions and suggestions are always welcome! 🚀
```

---
