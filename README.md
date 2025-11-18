
RTSP Blackmist Detector 🚨💨
A Dockerized system that watches an RTSP video stream for fume / dark smoke using a vision‑language model, stores results in InfluxDB, and exposes them through a lightweight web dashboard.

✨ Features

📡 RTSP live‑stream frame extraction  
🤖 AI analysis using an Ollama VLM (recommended: Qwen2.5-VL)  
📈 Data storage in InfluxDB v1  
🌐 Web interface to view detections and history  
🔒 HTTPS support via your own cert.pem and key.pem


🧩 Requirements
Before using this project, you must install/configure the following:
1. Docker
Install Docker for your OS:https://docs.docker.com/get-docker/
2. InfluxDB v1
Install InfluxDB v1.x and note your credentials (host, port, database, username, password).
3. Ollama (Required for AI analysis)

Must be installed on the host machine
Must be accessible from inside Docker → listen on 0.0.0.0
Install: https://ollama.ai  

4. Download the model
Recommended model:
ollama pull qwen2.5vl
5. Clone the repository
gh repo clone Coldness00/Fumes-Detector
cd rtsp-blackmist-detector
6. Provide certificates
In the root of the repo, create:
cert.pem
key.pem
These are your SSL certificate and key for HTTPS access.

⚙️ Configuration (docker-compose.yml)
You must edit docker-compose.yml before running the stack.
Below are fields you MUST understand and adapt 👇
Do NOT change
FOLDER_PATH=/app/images
DB_PATH=/app/data/processed_images.db
Should be customized

BASE_URL → your machine’s local IP + web portExample: http://192.168.1.15:9822

EXTERNAL_URL → only if you route through a reverse proxyOtherwise, keep it as comment

REFRESH_TIME → Delay between image captures (seconds). Lower = faster = heavier on CPU/GPU. Adjust based on your hardware.

PROMPT → Default is recommended. You can tweak, but results may vary, but keep the formating format.

OLLAMA settings → Tweak as needed, but defaults are safe.

CAMERA_NAMEA →  friendly name for your camera.

RTSP_URL → Your camera's RTSP address with id and passworkd.

InfluxDB connection block  


TZ → Your timezone (example: Europe/Luxembourg)


Volumes
You MUST allocate local folders for:

/app/images
/app/data

Example:
./images:/app/images
./data:/app/data
Ports
Modify if running multiple instances.


🚀 Start the project
Once everything is configured:
docker compose up -d
Check logs if needed:
docker compose logs -f

🖥️ Access the Web Dashboard
Open:
https://<BASE_URL>:<PORT>
(Your cert.pem and key.pem must be valid or your browser will show a warning)

🗂 Project Structure
.
├── data/                     <- local DB and persistent data
├── images/                   <- extracted frames
├── main.py                   <- main application
├── db.py                     <- database handler
├── docker-compose.yml
├── Dockerfile
├── cert.pem / key.pem
└── requirements.txt

❓ Questions / Issues
Feel free to open an Issue or ask for enhancements.
