import sqlite3
import threading
import os

# Lock to prevent concurrent DB writes from different threads
_db_lock = threading.Lock()

# Path to SQLite DB file (can be overridden via environment variable
DB_PATH = os.getenv("DB_PATH", "processed_images.db")

def init_db():
    """Create DB file and processed table if not exists."""
    with _db_lock:
        # This will create the DB file if it doesn't exist
        conn = sqlite3.connect(DB_PATH)
        try:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS processed (
                    filename TEXT PRIMARY KEY,
                    result TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        finally:
            conn.close()

def mark_as_processed(filename, result):
    with _db_lock, sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO processed(filename, result) VALUES (?, ?)
            ON CONFLICT(filename) DO UPDATE SET result=excluded.result, timestamp=CURRENT_TIMESTAMP
        """, (filename, result))
        conn.commit()

def is_processed(filename):
    with _db_lock, sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM processed WHERE filename = ?", (filename,))
        return c.fetchone() is not None

def load_processed_images():
    with _db_lock, sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT filename FROM processed")
        return set(row[0] for row in c.fetchall())

def load_all_results():
    with _db_lock, sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT filename, result FROM processed")
        return {row[0]: row[1] for row in c.fetchall()}

def remove_processed_entries(filenames):
    """Remove multiple entries from the processed table."""
    if not filenames:
        return
    
    with _db_lock, sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        placeholders = ','.join(['?' for _ in filenames])
        c.execute(f"DELETE FROM processed WHERE filename IN ({placeholders})", filenames)
        conn.commit()
        print(f"🗑️ Removed {len(filenames)} entries from database.")

def remove_processed_entry(filename):
    """Remove a single entry from the processed table."""
    with _db_lock, sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM processed WHERE filename = ?", (filename,))
        conn.commit()