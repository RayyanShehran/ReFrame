"""Bounded, source/tool/input-bound reading facts; never publishes caption tracks."""

import hashlib
import json

import projects
from references import ReferenceError

LIMITS = {"ocr": 32, "handwriting": 128}
MAX_PAYLOAD = 128 * 1024
PAYLOAD_LIMITS = {"ocr": MAX_PAYLOAD, "handwriting": 2048}


def identity(source, engine):
    return hashlib.sha256(
        json.dumps({"source": source, "engine": engine}, sort_keys=True).encode()
    ).hexdigest()


def read(project_id, kind, namespace, key):
    with projects.database() as db:
        row = db.execute(
            "SELECT payload FROM caption_readings WHERE project_id=? AND kind=? "
            "AND namespace=? AND key=?",
            (project_id, kind, namespace, key),
        ).fetchone()
    if not row:
        return None
    payload = bytes(row[0])
    if len(payload) > PAYLOAD_LIMITS[kind]:
        raise ReferenceError(
            422, "caption_cache_invalid", "Saved caption reading exceeds its bound."
        )
    return payload


def save(project_id, kind, namespace, key, payload):
    if kind not in LIMITS or not isinstance(payload, bytes) or len(payload) > PAYLOAD_LIMITS[kind]:
        raise ValueError("Invalid caption cache payload")
    with projects.database() as db:
        active = db.execute("SELECT status FROM projects WHERE id=?", (project_id,)).fetchone()
        if not active or active[0] != "active":
            return
        db.execute(
            "INSERT OR REPLACE INTO caption_readings VALUES(?,?,?,?,?,?)",
            (project_id, kind, namespace, key, payload, projects.now()),
        )
        db.execute(
            "DELETE FROM caption_readings WHERE project_id=? AND kind=? AND rowid NOT IN "
            "(SELECT rowid FROM caption_readings WHERE project_id=? AND kind=? "
            "ORDER BY saved_at DESC, key DESC LIMIT ?)",
            (project_id, kind, project_id, kind, LIMITS[kind]),
        )
