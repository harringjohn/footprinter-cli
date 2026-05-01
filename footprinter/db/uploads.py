"""Upload tracking — CRUD for the uploads table."""

import json
import sqlite3
from typing import Any, Dict, List, Optional


def create_upload(conn: sqlite3.Connection, data: Dict[str, Any]) -> int:
    """Create a new upload record."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO uploads
        (filename, file_hash, file_size, type, source, status, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (
            data["filename"],
            data["file_hash"],
            data.get("file_size"),
            data["type"],
            data.get("source"),
            data.get("status", "pending"),
            json.dumps(data.get("metadata", {})),
        ),
    )
    return cursor.lastrowid


def update_upload(conn: sqlite3.Connection, upload_id: int, **kwargs) -> None:
    """Update an upload record with results."""
    allowed = {
        "status",
        "items_added",
        "items_updated",
        "items_total",
        "completed_at",
        "error_message",
    }
    updates, values = [], []
    for field, value in kwargs.items():
        if field in allowed:
            updates.append(f"{field} = ?")
            values.append(value)
    if updates:
        values.append(upload_id)
        cursor = conn.cursor()
        cursor.execute(f"UPDATE uploads SET {', '.join(updates)} WHERE id = ?", values)
        conn.commit()


def get_upload_by_hash(conn: sqlite3.Connection, file_hash: str) -> Optional[Dict]:
    """Check if a file was already uploaded."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM uploads WHERE file_hash = ?", (file_hash,))
    row = cursor.fetchone()
    return dict(row) if row else None


def get_recent_uploads(conn: sqlite3.Connection, upload_type: Optional[str] = None, limit: int = 10) -> List[Dict]:
    """Get recent uploads, optionally filtered by type."""
    cursor = conn.cursor()
    if upload_type:
        cursor.execute(
            "SELECT * FROM uploads WHERE type = ? ORDER BY uploaded_at DESC LIMIT ?",
            (upload_type, limit),
        )
    else:
        cursor.execute("SELECT * FROM uploads ORDER BY uploaded_at DESC LIMIT ?", (limit,))
    return [dict(row) for row in cursor.fetchall()]
