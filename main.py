import os
import time
import threading
import base64
import requests
import json
import db
import re
import sys
import logging
import subprocess
from threading import Lock
from flask import Flask, render_template_string, send_from_directory, redirect, request
from PIL import Image
from io import BytesIO
from datetime import datetime, timedelta
from dotenv import load_dotenv
sys.stdout.reconfigure(line_buffering=True)

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

load_dotenv()
BASE_URL = os.getenv("BASE_URL", "http://localhost:9822")  # Base URL for image links
EXTERNAL_URL = os.getenv("EXTERNAL_URL", "")  # Optional external URL for reverse proxy
FOLDER_PATH    = os.getenv("FOLDER_PATH", "./images")
OLLAMA_URL     = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
CAMERA_NAME    = os.getenv("CAMERA_NAME", "Unknown Camera")
REFRESH_TIME   = int(os.getenv("REFRESH_TIME", "55"))
PROMPT         = os.getenv("PROMPT", "Is there black smoke or mist like fume on the top right corner picture, answer by Yes, No, Maybe. Give your confidance level if Yes from 0 to 100.")
OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL", "llava")  # Default to 'llava'
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.8"))
OLLAMA_TOP_P = float(os.getenv("OLLAMA_TOP_P", "0.9"))
OLLAMA_SEED = int(os.getenv("OLLAMA_SEED", "42"))
CLEANUP_INTERVAL = 1 * 60 * 60  # Run cleanup every 24 hours (in seconds)
IMAGE_RETENTION_DAYS = int(os.getenv("IMAGE_RETENTION_DAYS", "15"))
HEALTH_MAX_SNAPSHOT_AGE = int(os.getenv("HEALTH_MAX_SNAPSHOT_AGE", "600"))
SELF_EXIT_AFTER_STALE = int(os.getenv("SELF_EXIT_AFTER_STALE", str(HEALTH_MAX_SNAPSHOT_AGE)))
SELF_EXIT_ENABLED = os.getenv("SELF_EXIT_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
STARTUP_GRACE_PERIOD = 120
SELF_EXIT_CHECK_INTERVAL = 30

app = Flask(__name__)
analysis_results = {}  # Cache of filename -> result string
request_lock = Lock()
results_lock = Lock()

# Monotonic timestamps avoid wall-clock changes affecting health decisions.
process_started_at = time.monotonic()
last_successful_snapshot = None
cleanup_active = threading.Event()

def record_successful_snapshot():
    """Record the monotonic time of the most recent successful snapshot."""
    global last_successful_snapshot
    last_successful_snapshot = time.monotonic()

def snapshot_age_seconds():
    """Return age of the last successful snapshot, or None if there is none."""
    if last_successful_snapshot is None:
        return None
    return time.monotonic() - last_successful_snapshot

def snapshot_health_state(max_age=HEALTH_MAX_SNAPSHOT_AGE):
    """Return the shared snapshot-health state used by /health and self-exit."""
    age = snapshot_age_seconds()
    in_startup_grace = time.monotonic() - process_started_at < STARTUP_GRACE_PERIOD
    stale = age is None or age > max_age
    return age, stale, in_startup_grace

db.init_db()  # Initialize your SQLite DB on startup

print(f"✅ Using model: {OLLAMA_MODEL}")

# Convert image file to base64 for LLaVA API
def encode_image_to_base64(path):
    with Image.open(path) as img:
        buffer = BytesIO()
        img.convert("RGB").save(buffer, format="JPEG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

# Send image and prompt to LLaVA server, stream and collect respons
def ask_llava_stream(image_b64, prompt):
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "images": [image_b64],
        "options": {                        # ← wrap parameters here
            "temperature": OLLAMA_TEMPERATURE,
            "top_p": OLLAMA_TOP_P,
            "seed": OLLAMA_SEED,
            "num_ctx": 8192,
        }
    }
    response = requests.post(
        OLLAMA_URL,
        json=payload,
        stream=True
    )
    response.raise_for_status()
    full_response = ""
    for line in response.iter_lines():
        if line:
            data = json.loads(line.decode("utf-8"))
            full_response += data.get("response", "")
            if data.get("done", False):
                break
    return full_response

# Home page: list all images with results and forms
import re
from datetime import datetime

def extract_answer(text):
    """
    Extract the answer from AI response text using the same logic as parse_response.
    """
    if not text:
        return "unknown"
    # Use the same regex as parse_response to find "answer = confidence" format
    match = re.search(r"\b(yes|no|maybe)\s*=\s*(\d+)", text, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    # If no confidence format found, return unknown
    return "unknown"

def get_filtered_files():
    """Return image filenames matching the current request's filters."""
    all_files = sorted(
        [f for f in os.listdir(FOLDER_PATH) if f.lower().endswith((".jpg", ".jpeg", ".png"))],
        key=lambda f: os.path.getmtime(os.path.join(FOLDER_PATH, f)),
        reverse=True
    )

    with results_lock:
        results_empty = not analysis_results
    if results_empty:
        loaded_results = db.load_all_results()
        with results_lock:
            if not analysis_results:
                analysis_results.update(loaded_results)
    # Always use a copy while outside the lock; callers must not read the
    # mutable cache while analyses may be updating it.
    with results_lock:
        results_snapshot = dict(analysis_results)

    filter_answer = request.args.get("answer", "").lower()
    if filter_answer == "yesmaybe":
        filter_answers = ["yes", "maybe"]
    elif filter_answer:
        filter_answers = [filter_answer]
    else:
        filter_answers = []

    datetime_start = request.args.get("datetime_start")
    datetime_end = request.args.get("datetime_end")
    start_dt = end_dt = None
    try:
        if datetime_start:
            start_dt = datetime.strptime(datetime_start, "%Y-%m-%dT%H:%M")
        if datetime_end:
            end_dt = datetime.strptime(datetime_end, "%Y-%m-%dT%H:%M")
    except ValueError:
        pass

    filtered_files = []
    for f in all_files:
        result = results_snapshot.get(f, "")
        if filter_answers and extract_answer(result) not in filter_answers:
            continue
        file_path = os.path.join(FOLDER_PATH, f)
        file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
        if start_dt and file_mtime < start_dt:
            continue
        if end_dt and file_mtime > end_dt:
            continue
        filtered_files.append(f)
    return filtered_files, results_snapshot, filter_answer, datetime_start, datetime_end


def render_image_batch(files, results_snapshot):
    return render_template_string(ITEM_TEMPLATE, files=files, results=results_snapshot)


@app.route("/")
def index():
    per_page = 24
    filtered_files, results_snapshot, filter_answer, datetime_start, datetime_end = get_filtered_files()
    files = filtered_files[:per_page]
    return render_template_string(
        TEMPLATE,
        files=files,
        items_html=render_image_batch(files, results_snapshot),
        camera_name=CAMERA_NAME,
        results=results_snapshot,
        prompt=PROMPT,
        model=OLLAMA_MODEL,
        current_answer=filter_answer,
        datetime_start=datetime_start,
        datetime_end=datetime_end,
        filtered_files=filtered_files,
        has_more=len(filtered_files) > len(files)
    )


@app.route("/load_more")
def load_more():
    """Return the next filtered image batch for the infinite-scroll client."""
    per_page = 24
    try:
        page = max(1, int(request.args.get("page", 2)))
    except (TypeError, ValueError):
        page = 2
    filtered_files, results_snapshot, _, _, _ = get_filtered_files()
    start = (page - 1) * per_page
    files = filtered_files[start:start + per_page]
    return {
        "html": render_image_batch(files, results_snapshot),
        "has_more": start + len(files) < len(filtered_files),
        "page": page,
    }


# Serve image file for rendering in browser
@app.route("/images/<filename>")
def image_file(filename):
    return send_from_directory(FOLDER_PATH, filename)

# Trigger analysis of selected image when form is submitted
@app.route("/analyze/<filename>", methods=["POST"])
def analyze(filename):
    filepath = os.path.join(FOLDER_PATH, filename)
    try:
        image_b64 = encode_image_to_base64(filepath)
        with request_lock:
            response = ask_llava_stream(image_b64, PROMPT)
            print(f"🤖 AI result for {filename}: {response}")
        with results_lock:
            analysis_results[filename] = response
        db.mark_as_processed(filename, response)
        answer, confidence = parse_response(response)
        send_to_influx(answer, confidence, filename)  # Pass filename here
    except Exception as e:
        with results_lock:
            analysis_results[filename] = f"Error: {e}"
    return redirect("/")

@app.route("/health")
def health():
    """Report camera snapshot freshness for Docker/container health checks."""
    age, stale, in_startup_grace = snapshot_health_state()
    healthy = in_startup_grace or not stale
    response = {
        "status": "healthy" if healthy else "unhealthy",
        "snapshot_age_seconds": age,
        "max_snapshot_age_seconds": HEALTH_MAX_SNAPSHOT_AGE,
        "startup_grace": in_startup_grace,
    }
    return response, 200 if healthy else 503

@app.route("/snapshot/control", methods=["POST"])
def control_snapshot():
    global snapshot_loop_enabled
    action = request.form.get("action")
    if action == "stop":
        snapshot_loop_enabled = False
        print("⏹ Snapshot loop stopped.")
    elif action == "start":
        snapshot_loop_enabled = True
        print("▶️ Snapshot loop started.")
    elif action == "once":
        print("📸 Doing one manual snapshot...")
        do_one_snapshot()
    return redirect("/")

#RTSP snapshot
snapshot_loop_enabled = True
snapshot_thread = None
snapshot_lock = Lock()

def rtsp_snapshotter():
    global snapshot_loop_enabled

    rtsp_url = os.getenv("RTSP_URL")
    print(f"📸 Preparing RTSP snapshots via FFmpeg")

    while True:
        sleep_time = REFRESH_TIME
        try:
            if not snapshot_loop_enabled:
                sleep_time = 1
                continue

            os.makedirs(FOLDER_PATH, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"rtsp_{timestamp}.jpg"
            filepath = os.path.join(FOLDER_PATH, filename)

            cmd = [
                "ffmpeg",
                "-rtsp_transport", "tcp",
                "-y",  # overwrite output
                "-i", rtsp_url,
                "-ss", "00:00:05",      # < wait seconds after stream starts
                "-frames:v", "1",       # < grab one frame
                "-q:v", "2",            # < good JPEG quality
                filepath
            ]

            print(f"📡 Taking snapshot to {filepath}...")
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=True, timeout=60)
            record_successful_snapshot()
            print(f"✅ Snapshot saved via FFmpeg: {filepath}")
        except Exception as e:
            print(f"❌ RTSP snapshot loop error: {e}")
        finally:
            print("🕒 Waiting for next capture cycle...")
            time.sleep(sleep_time)

def do_one_snapshot():
    rtsp_url = os.getenv("RTSP_URL")
    if not rtsp_url:
        print("❌ RTSP_URL is not set.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"rtsp_{timestamp}.jpg"
    filepath = os.path.join(FOLDER_PATH, filename)

    cmd = [
        "ffmpeg",
        "-rtsp_transport", "tcp",
        "-y",  # overwrite output
        "-i", rtsp_url,
        "-ss", "00:00:05",      # < wait seconds after stream starts
        "-frames:v", "1",       # < grab one frame
        "-q:v", "2",            # < good JPEG quality
        filepath
    ]

    print(f"📸 Taking manual snapshot to {filepath}...")
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        record_successful_snapshot()
        print(f"✅ Manual snapshot saved: {filepath}")
    except subprocess.CalledProcessError:
        print("❌ FFmpeg failed to capture snapshot.")

# Add manual cleanup route
@app.route("/cleanup", methods=["POST"])
def manual_cleanup():
    """Manual trigger for cleanup process."""
    try:
        cleanup_old_images()
        return redirect("/?message=cleanup_completed")
    except Exception as e:
        print(f"❌ Manual cleanup failed: {e}")
        return redirect("/?message=cleanup_failed")

# Background thread to watch folder for new unprocessed files
def folder_watcher():
    print(f"👁️ Watching folder: {FOLDER_PATH}")
    processed = set(db.load_processed_images())
    # Check latest file on startup
    try:
        latest = get_latest_image()
        if latest and latest not in processed:
            image_b64 = encode_image_to_base64(os.path.join(FOLDER_PATH, latest))
            with request_lock:
                response = ask_llava_stream(image_b64, PROMPT)
                print(f"🤖 AI result for {latest}: {response}")  # Fixed variable name
            with results_lock:
                analysis_results[latest] = response
            db.mark_as_processed(latest, response)
            answer, confidence = parse_response(response)
            send_to_influx(answer, confidence, latest)  # Pass filename here
            processed.add(latest)
    except Exception as e:
        print(f"❌ Folder watcher startup error: {e}")
        if 'latest' in locals() and latest:
            with results_lock:
                analysis_results[latest] = f"Error: {e}"

    # Monitor folder continuously
    while True:
        try:
            for filename in os.listdir(FOLDER_PATH):
                if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                if filename in processed:
                    continue
                try:
                    image_b64 = encode_image_to_base64(os.path.join(FOLDER_PATH, filename))
                    with request_lock:
                        response = ask_llava_stream(image_b64, PROMPT)
                        print(f"🤖 AI result for {filename}: {response}")
                    with results_lock:
                        analysis_results[filename] = response
                    db.mark_as_processed(filename, response)
                    answer, confidence = parse_response(response)
                    send_to_influx(answer, confidence, filename)  # Pass filename here
                    processed.add(filename)
                except Exception as e:
                    with results_lock:
                        analysis_results[filename] = f"Error: {e}"
            time.sleep(5)
        except Exception as e:
            print(f"❌ Folder watcher loop error: {e}")
            time.sleep(5)

# Get the most recently modified image file
def get_latest_image():
    files = [
        f for f in os.listdir(FOLDER_PATH)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ]

    if not files:
        return None

    def safe_mtime(filename):
        try:
            return os.path.getmtime(os.path.join(FOLDER_PATH, filename))
        except FileNotFoundError:
            return 0  # file vanished

    files = sorted(files, key=safe_mtime, reverse=True)
    return files[0]

#look for the answer from the AI
def parse_response(response):
    """
    Parse responses like 'Yes = 65', 'No=10', 'Maybe = 50'
    Only accepts '=' as separator.
    Returns:
        answer: str ('yes', 'no', 'maybe')
        confidence: float (0.0 to 1.0)
    """
    match = re.search(r"\b(yes|no|maybe)\b\s*=\s*(\d+)", response, re.IGNORECASE)
    if not match:
        return "unknown", 0.0
    answer = match.group(1).lower()
    confidence = int(match.group(2))
    confidence = min(max(confidence, 0), 100) / 100.0
    return answer, confidence

#InfluxDB send information
def _escape_tag(v: str) -> str:
    # Escape for tag values: comma, space, equals
    return str(v).replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")

def _escape_measurement(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,")

def _escape_field_str(v: str) -> str:
    # Field string values are in double quotes; escape internal quotes and backslashes
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'

def _format_field_value(v):
    # Line protocol field type inference
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return f"{v}i"
    if isinstance(v, float):
        # Avoid NaN/inf
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return f"{v}"
    # strings and everything else
    return _escape_field_str(v)

def send_to_influx(answer, confidence, filename=None, ts_ns=None):
    influx_url = os.getenv("INFLUX_URL")
    influx_db = os.getenv("INFLUX_DB")
    influx_user = os.getenv("INFLUX_USER", "")
    influx_pass = os.getenv("INFLUX_PASS", "")
    measurement = os.getenv("MEASUREMENT", "smoke_detection_pipes")
    source = os.getenv("OLLAMA_MODEL", "unknown")

    if not influx_url:
        print("⚠️ INFLUX_URL not configured, skipping InfluxDB write")
        return

    # Determine the base URL to use (from your existing context)
    base_url = EXTERNAL_URL if EXTERNAL_URL else BASE_URL  # noqa: F821 (assumes defined in your module)

    image_link = ""
    if filename:
        image_link = f"{base_url}/images/{filename}"

    # TAGS: keep only low-cardinality tags
    tags = {
        "source": source,  # stable
        # Add more stable tags if useful, e.g. camera_id/site/pipeline/model_version
    }

    # FIELDS: high-cardinality stuff
    fields = {
        "answer": answer,                     # moved from tag -> field
        "confidence": float(confidence),      # numeric field
    }
    if filename:
        fields["image_filename"] = filename   # field
    if image_link:
        fields["image_link"] = image_link     # field

    # Build line protocol safely
    m = _escape_measurement(measurement)
    tag_str = ",".join(f"{_escape_tag(k)}={_escape_tag(v)}" for k, v in tags.items() if v is not None)
    field_parts = []
    for k, v in fields.items():
        fv = _format_field_value(v)
        if fv is None:
            continue
        field_parts.append(f"{_escape_tag(k)}={fv}")
    field_str = ",".join(field_parts)

    if not field_str:
        print("⚠️ No fields to write, skipping")
        return

    # Optional timestamp in nanoseconds; if not provided, server will assign
    if ts_ns is None:
        # Example: current time in ns; alternatively pass your frame_ts_ns
        ts_ns = int(time.time() * 1e9)

    if tag_str:
        line = f"{m},{tag_str} {field_str} {ts_ns}"
    else:
        line = f"{m} {field_str} {ts_ns}"

    params = {"db": influx_db} if influx_db else {}
    auth = (influx_user, influx_pass) if influx_user else None

    try:
        resp = requests.post(influx_url, params=params, data=line, auth=auth, timeout=5)
        if resp.status_code == 204:
            print(f"📤 Sent answer with confidence={confidence:.2f} to InfluxDB.")
            if image_link:
                print(f"🔗 Image link: {image_link}")
        else:
            print(f"⚠️ InfluxDB write failed: {resp.status_code} {resp.text}")
            # Helpful for debugging cardinality/limits:
            # print(f"Line protocol was: {line}")
    except Exception as e:
        print(f"❌ InfluxDB error: {e}")

def _cleanup_old_images():
    """Remove images older than IMAGE_RETENTION_DAYS and update database accordingly."""
    print(f"🧹 Starting cleanup of images older than {IMAGE_RETENTION_DAYS} days...")
    
    if not os.path.exists(FOLDER_PATH):
        print(f"❌ Folder {FOLDER_PATH} does not exist.")
        return
    cutoff_time = time.time() - (IMAGE_RETENTION_DAYS * 24 * 60 * 60)
    removed_count = 0
    # Get all image files in the folder
    image_files = [f for f in os.listdir(FOLDER_PATH) 
                   if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    for filename in image_files:
        filepath = os.path.join(FOLDER_PATH, filename)
        try:
            # Check if file is older than retention period
            if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff_time:
                os.remove(filepath)
                # Remove from in-memory cache as well
                with results_lock:
                    analysis_results.pop(filename, None)
                removed_count += 1
                print(f"🗑️ Removed old image: {filename}")
        except Exception as e:
            print(f"❌ Error removing {filename}: {e}")
    # Clean up database entries for files that no longer exist
    cleanup_database_entries()
    print(f"✅ Cleanup completed. Removed {removed_count} old images.")

def cleanup_old_images():
    """Run cleanup while marking it so snapshot self-exit checks are paused."""
    cleanup_active.set()
    try:
        return _cleanup_old_images()
    finally:
        cleanup_active.clear()

def cleanup_database_entries():
    """Remove database entries for images that no longer exist in the folder."""
    print("🔄 Cleaning up database entries for missing images...")
    
    # Get all filenames from database
    db_results = db.load_all_results()
    
    # Get all existing image files
    existing_files = set()
    if os.path.exists(FOLDER_PATH):
        existing_files = set(f for f in os.listdir(FOLDER_PATH) 
                           if f.lower().endswith((".jpg", ".jpeg", ".png")))
    
    # Find database entries that don't have corresponding files
    orphaned_entries = []
    for filename in db_results.keys():
        if filename not in existing_files:
            orphaned_entries.append(filename)
    
    # Remove orphaned entries from database and memory cache
    if orphaned_entries:
        db.remove_processed_entries(orphaned_entries)
        # Also remove from in-memory cache
        for filename in orphaned_entries:
            with results_lock:
                analysis_results.pop(filename, None)
        print(f"🗑️ Removed {len(orphaned_entries)} orphaned database entries.")
    else:
        print("✅ No orphaned database entries found.")

def snapshot_health_watchdog():
    """Exit on persistent snapshot staleness so Docker can restart the app."""
    print(f"🛡️ Snapshot health watchdog started (checks every {SELF_EXIT_CHECK_INTERVAL}s; "
          f"threshold={SELF_EXIT_AFTER_STALE}s, enabled={SELF_EXIT_ENABLED})")
    while True:
        time.sleep(SELF_EXIT_CHECK_INTERVAL)
        if not SELF_EXIT_ENABLED or cleanup_active.is_set():
            continue
        age, stale, in_startup_grace = snapshot_health_state(SELF_EXIT_AFTER_STALE)
        if in_startup_grace or not stale:
            continue
        print(
            f"❌ Snapshot health watchdog: no successful snapshot for {age:.0f}s "
            f"(threshold {SELF_EXIT_AFTER_STALE}s); exiting so Docker restarts the container.",
            flush=True,
        )
        os._exit(1)

def cleanup_scheduler():
    """Background thread that runs cleanup periodically."""
    print(f"⏰ Starting cleanup scheduler (runs every {CLEANUP_INTERVAL/3600:.1f} hours)")
    
    while True:
        try:
            cleanup_old_images()
        except Exception as e:
            print(f"❌ Error during scheduled cleanup: {e}")
        finally:
            # Wait for next cleanup cycle even after an unexpected error.
            time.sleep(CLEANUP_INTERVAL)

def start_cleanup_scheduler():
    """Start the cleanup scheduler in a background thread."""
    cleanup_thread = threading.Thread(target=cleanup_scheduler, daemon=True)
    cleanup_thread.start()
    print("🚀 Cleanup scheduler started.")
ITEM_TEMPLATE = """
{% for file in files %}
  <div>
    <img src="/images/{{ file }}" alt="{{ file }}" onclick="showModal('/images/{{ file }}')">
    <form method="post" action="/analyze/{{ file }}">
      <button type="submit">Analyze "{{ file }}"</button>
    </form>
    {% if results.get(file) %}
      <div class="result"><strong>Result:</strong><br>{{ results[file] }}</div>
    {% endif %}
  </div>
{% endfor %}
"""

TEMPLATE = """
{% for file in files %}
  <div>
    <img src="/images/{{ file }}" alt="{{ file }}" onclick="showModal('/images/{{ file }}')">
    <form method="post" action="/analyze/{{ file }}">
      <button type="submit">Analyze "{{ file }}"</button>
    </form>
    {% if results.get(file) %}
      <div class="result"><strong>Result:</strong><br>{{ results[file] }}</div>
    {% endif %}
  </div>
{% endfor %}
"""

TEMPLATE = """
{% for file in files %}
  <div>
    <img src="/images/{{ file }}" alt="{{ file }}" onclick="showModal('/images/{{ file }}')">
    <form method="post" action="/analyze/{{ file }}">
      <button type="submit">Analyze "{{ file }}"</button>
    </form>
    {% if results.get(file) %}
      <div class="result"><strong>Result:</strong><br>{{ results[file] }}</div>
    {% endif %}
  </div>
{% endfor %}
"""

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <title>Black Mist Checker - {{ camera_name }}</title>
  <style>
    body { font-family: sans-serif; padding: 2rem; background: #f9f9f9; }
    img { max-width: 300px; margin: 1rem; border: 2px solid #ccc; cursor: pointer; transition: transform 0.2s ease-in-out; }
    img:hover { transform: scale(1.02); }

    form { display: inline-block; margin-bottom: 1rem; }
    .result { background: #eee; padding: 0.5rem; margin-bottom: 2rem; border-radius: 4px; max-width: 320px; white-space: pre-wrap; }

    /* Modal styles */
    .modal {
      display: none;
      position: fixed;
      z-index: 1000;
      left: 0;
      top: 0;
      width: 100%;
      height: 100%;
      overflow: auto;
      background-color: rgba(0, 0, 0, 0.8);
    }

    .modal-content {
      margin: 5% auto;
      display: block;
      max-width: 90%;
      max-height: 80%;
      border: 5px solid white;
      border-radius: 8px;
    }

    .close {
      position: absolute;
      top: 20px;
      right: 35px;
      color: white;
      font-size: 40px;
      font-weight: bold;
      cursor: pointer;
    }

    .close:hover,
    .close:focus {
      color: #bbb;
      text-decoration: none;
    }
  </style>
</head>
<body>
  <h1>Black Mist Checker - {{ camera_name }}</h1>
  <h2>Maintenance</h2>
  <form method="post" action="/cleanup" onsubmit="return confirm('This will remove all images older than 15 days. Continue?');">
    <button type="submit" style="background-color: #ff6b6b; color: white; padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer;">
        🧹 Clean Old Images (15+ days)
    </button>
  </form>
    <script>
    // Show cleanup status message if present
    const urlParams = new URLSearchParams(window.location.search);
    const message = urlParams.get('message');
    if (message === 'cleanup_completed') {
        alert('✅ Cleanup completed successfully!');
    } else if (message === 'cleanup_failed') {
        alert('❌ Cleanup failed. Check server logs for details.');
    }
    </script>
  <!--
  <h2>Snapshot Controls</h2>
  <form method="post" action="/snapshot/control"><button name="action" value="start">🔁 Start Loop</button></form>
  <form method="post" action="/snapshot/control"><button name="action" value="stop">⏹ Stop Loop</button></form>
  <form method="post" action="/snapshot/control"><button name="action" value="once">📸 Do One Snapshot</button></form>
  -->
   <h2>Filter</h2>
   <form method="get" action="/" style="display: inline-block; margin-bottom: 1rem;">
   <label for="answer">Result:</label>
   <select name="answer" id="answer">
     <option value="" {% if current_answer == "" %}selected{% endif %}>All</option>
     <option value="yes" {% if current_answer == "yes" %}selected{% endif %}>Yes</option>
     <option value="no" {% if current_answer == "no" %}selected{% endif %}>No</option>
     <option value="maybe" {% if current_answer == "maybe" %}selected{% endif %}>Maybe</option>
     <option value="yesmaybe" {% if current_answer == "yesmaybe" %}selected{% endif %}>Yes + Maybe</option>
   </select>

     <label for="datetime_start">From:</label>
    <input type="datetime-local" id="datetime_start" name="datetime_start" value="{{ datetime_start or '' }}">

     <label for="datetime_end">To:</label>
     <input type="datetime-local" id="datetime_end" name="datetime_end" value="{{ datetime_end or '' }}">
     <button type="submit">Apply</button>
   </form>

   <form method="get" action="/" style="display: inline-block; margin-left: 1rem;">
     <button type="submit">Clear Filters</button>
   </form>
   <hr>
  <div id="image-list">
    {{ items_html|safe }}
  </div>

  <div id="scroll-status" style="text-align: center; margin: 2rem;">Loading...</div>
  <div id="scroll-sentinel" aria-hidden="true"></div>

    <script>
    const filteredImages = {{ filtered_files|tojson }};
    let nextPage = 2;
    let loading = false;
    let hasMore = {{ has_more|tojson }};
    const imageList = document.getElementById('image-list');
    const status = document.getElementById('scroll-status');
    const sentinel = document.getElementById('scroll-sentinel');

    async function loadMoreImages() {
        if (loading || !hasMore) return;
        loading = true;
        status.textContent = 'Loading...';
        const params = new URLSearchParams(window.location.search);
        params.set('page', nextPage);
        try {
            const response = await fetch(`/load_more?${params.toString()}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            imageList.insertAdjacentHTML('beforeend', data.html);
            hasMore = data.has_more;
            nextPage = data.page + 1;
            if (!hasMore) status.textContent = 'No more images';
        } catch (error) {
            console.error('Unable to load more images:', error);
            status.textContent = 'Unable to load more images. Scroll to retry.';
        } finally {
            loading = false;
        }
    }

    if (!hasMore) status.textContent = 'No more images';
    const observer = new IntersectionObserver(entries => {
        if (entries.some(entry => entry.isIntersecting)) loadMoreImages();
    }, { rootMargin: '400px' });
    observer.observe(sentinel);
    </script>

    <!-- Modal -->
    <div id="imageModal" class="modal">
    <span class="close" onclick="hideModal()">&times;</span>
    <img class="modal-content" id="modalImage" alt="Image Viewer">
    </div>

  <script>
    // Collect all image elements on the page
    let currentIndex = 0;

    function showModal(src) {
        // src is like "/images/filename.jpg"
        // Extract filename from src
        const filename = src.split('/').pop();
        currentIndex = filteredImages.indexOf(filename);
        if (currentIndex === -1) currentIndex = 0;

        const modal = document.getElementById('imageModal');
        const modalImg = document.getElementById('modalImage');
        modal.style.display = 'block';
        modalImg.src = `/images/${filteredImages[currentIndex]}`;
    }

    function hideModal() {
        document.getElementById('imageModal').style.display = 'none';
    }

    function showPrev() {
        currentIndex = (currentIndex - 1 + filteredImages.length) % filteredImages.length;
        document.getElementById('modalImage').src = `/images/${filteredImages[currentIndex]}`;
    }

    function showNext() {
        currentIndex = (currentIndex + 1) % filteredImages.length;
        document.getElementById('modalImage').src = `/images/${filteredImages[currentIndex]}`;
    }

    // Keyboard navigation
    document.addEventListener('keydown', function(event) {
        const modal = document.getElementById('imageModal');
        if (modal.style.display !== 'block') return;

        if (event.key === "Escape") {
        hideModal();
        } else if (event.key === "ArrowLeft") {
        showPrev();
        } else if (event.key === "ArrowRight") {
        showNext();
        }
    });

    // Mouse click navigation inside modal image
    document.getElementById('modalImage').addEventListener('click', function(event) {
        const modalWidth = this.clientWidth;
        const clickX = event.offsetX;

        if (clickX < modalWidth / 2) {
        showPrev();
        } else {
        showNext();
        }
    });

    // Also allow clicking on modal background (except close button) to hide modal
    document.getElementById('imageModal').addEventListener('click', function(event) {
        if (event.target === this) {
        hideModal();
        }
    });;
  </script>
</body>
</html>
"""
def run_http():
    app.run(host="0.0.0.0", port=9822)

def run_https():
    app.run(host="0.0.0.0", port=9823, ssl_context=('cert.pem', 'key.pem'))

if __name__ == "__main__":
    threading.Thread(target=folder_watcher, daemon=True).start()
    snapshot_thread = threading.Thread(target=rtsp_snapshotter, daemon=True)
    snapshot_thread.start()
    threading.Thread(target=run_http, daemon=True).start()
    threading.Thread(target=snapshot_health_watchdog, daemon=True).start()
    start_cleanup_scheduler()
    print("🧹 Running initial cleanup on startup...")
    cleanup_old_images()
    run_https()
