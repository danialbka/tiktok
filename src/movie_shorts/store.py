from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class JobStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rd_download_id TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL,
                    download_url TEXT NOT NULL,
                    link_url TEXT NOT NULL,
                    filesize INTEGER NOT NULL,
                    mime_type TEXT,
                    local_video_path TEXT,
                    local_subtitle_path TEXT,
                    manifest_path TEXT,
                    output_path TEXT,
                    status TEXT NOT NULL DEFAULT 'discovered',
                    last_error TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                """
            )

    def upsert_download(self, payload: dict[str, Any]) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO jobs (
                    rd_download_id,
                    filename,
                    download_url,
                    link_url,
                    filesize,
                    mime_type,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rd_download_id) DO UPDATE SET
                    filename = excluded.filename,
                    download_url = excluded.download_url,
                    link_url = excluded.link_url,
                    filesize = excluded.filesize,
                    mime_type = excluded.mime_type,
                    metadata_json = excluded.metadata_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    payload["rd_download_id"],
                    payload["filename"],
                    payload["download_url"],
                    payload["link_url"],
                    payload["filesize"],
                    payload.get("mime_type"),
                    json.dumps(payload.get("metadata", {})),
                ),
            )
            job_id = self.get_job_id_by_rd_id(payload["rd_download_id"], connection)
            self.add_event(job_id, "synced", payload.get("metadata", {}), connection)
            return job_id

    def get_job_id_by_rd_id(self, rd_download_id: str, connection: sqlite3.Connection | None = None) -> int:
        conn = connection or self._connect()
        should_close = connection is None
        try:
            row = conn.execute("SELECT id FROM jobs WHERE rd_download_id = ?", (rd_download_id,)).fetchone()
            if not row:
                raise KeyError(f"Missing job for {rd_download_id}")
            return int(row["id"])
        finally:
            if should_close:
                conn.close()

    def get_job_by_rd_id(self, rd_download_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM jobs WHERE rd_download_id = ?", (rd_download_id,)).fetchone()

    def add_event(
        self,
        job_id: int,
        event_type: str,
        payload: dict[str, Any] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        conn = connection or self._connect()
        should_close = connection is None
        try:
            conn.execute(
                "INSERT INTO job_events (job_id, event_type, payload_json) VALUES (?, ?, ?)",
                (job_id, event_type, json.dumps(payload or {})),
            )
            conn.commit()
        finally:
            if should_close:
                conn.close()

    def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = list(fields.values()) + [job_id]
        with self._connect() as connection:
            connection.execute(
                f"UPDATE jobs SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values,
            )

    def list_jobs(self, status: str | None = None, limit: int = 25) -> list[sqlite3.Row]:
        query = "SELECT * FROM jobs"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            return list(connection.execute(query, params).fetchall())

    def get_job(self, job_id: int) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown job {job_id}")
        return row
