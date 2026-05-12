from __future__ import annotations
import sqlite3
import threading
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any
from pathlib import Path

# Database configuration
DB_PATH = Path(__file__).parent.parent.parent / "data" / "live_feed.db"
MAX_EVENTS = 1000
MAX_AGE_DAYS = 30

_db_lock = threading.Lock()

def _init_db():
    """Initialize SQLite database for live feed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS live_feed_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER UNIQUE,
            event_type TEXT NOT NULL,
            user TEXT NOT NULL,
            message TEXT,
            timestamp TEXT NOT NULL,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON live_feed_events(timestamp DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user ON live_feed_events(user)")
    conn.commit()
    conn.close()

def _cleanup_old_events():
    """Remove events older than MAX_AGE_DAYS or keep only MAX_EVENTS."""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Remove by age
        cutoff_date = (datetime.now() - timedelta(days=MAX_AGE_DAYS)).isoformat()
        cursor.execute("DELETE FROM live_feed_events WHERE timestamp < ?", (cutoff_date,))
        
        # Remove excess events (keep only MAX_EVENTS)
        cursor.execute("""
            DELETE FROM live_feed_events 
            WHERE id NOT IN (
                SELECT id FROM live_feed_events 
                ORDER BY timestamp DESC 
                LIMIT ?
            )
        """, (MAX_EVENTS,))
        
        conn.commit()
        conn.close()

def add_event(event_type: str, user: str, message: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
    """Adds a new event to the live feed (stored in SQLite)."""
    event_id = int(datetime.now().timestamp() * 1000)
    timestamp = datetime.now().isoformat()
    metadata_json = json.dumps(metadata or {})
    
    event = {
        "id": event_id,
        "type": event_type,
        "user": user,
        "message": message,
        "timestamp": timestamp,
        "metadata": metadata or {}
    }
    
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO live_feed_events (event_id, event_type, user, message, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (event_id, event_type, user, message, timestamp, metadata_json))
            conn.commit()
        except sqlite3.IntegrityError:
            # Duplicate event_id, skip
            pass
        finally:
            conn.close()
    
    # Cleanup old events periodically
    _cleanup_old_events()
    
    return event

def get_feed(limit: int = 50) -> List[Dict[str, Any]]:
    """Returns the latest live feed events from database."""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT event_id, event_type, user, message, timestamp, metadata
            FROM live_feed_events
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        conn.close()
    
    events = []
    for row in rows:
        events.append({
            "id": row["event_id"],
            "type": row["event_type"],
            "user": row["user"],
            "message": row["message"],
            "timestamp": row["timestamp"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {}
        })
    
    return events

def get_user_activities(user: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Get activities for a specific user."""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT event_id, event_type, user, message, timestamp, metadata
            FROM live_feed_events
            WHERE user = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (user, limit))
        
        rows = cursor.fetchall()
        conn.close()
    
    events = []
    for row in rows:
        events.append({
            "id": row["event_id"],
            "type": row["event_type"],
            "user": row["user"],
            "message": row["message"],
            "timestamp": row["timestamp"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {}
        })
    
    return events

# Initialize database on import
_init_db()