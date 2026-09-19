import asyncio
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import httpx

PREFIX = "excify-eur-huf-ai-v2/"


class StoreConflict(Exception):
    pass


class StoreUnavailable(Exception):
    pass


class LocalStore:
    def __init__(self, path=None):
        self.path = Path(path or ".excify/eur-huf-ai.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    async def read(self, key):
        with self.connect() as db:
            row = db.execute("SELECT data, version FROM records WHERE id = ?", (key,)).fetchone()
        return (json.loads(row[0]), row[1]) if row else (None, None)

    async def write(self, key, data, version):
        with self.connect() as db:
            if version is None:
                try:
                    db.execute("INSERT INTO records VALUES (?, 1, ?)", (key, json.dumps(data)))
                except sqlite3.IntegrityError as exc:
                    raise StoreConflict() from exc
            else:
                result = db.execute("UPDATE records SET data = ?, version = version + 1 WHERE id = ? AND version = ?",
                                    (json.dumps(data), key, version))
                if result.rowcount != 1:
                    raise StoreConflict()

    async def keys(self):
        with self.connect() as db:
            return [r[0] for r in db.execute("SELECT id FROM records WHERE id LIKE 'account-%'")]


class BlobStore:
    def __init__(self):
        self.token = os.environ["BLOB_READ_WRITE_TOKEN"]
        self.prefix = PREFIX + os.getenv("VERCEL_ENV", "development") + "/"
        store_id = os.getenv("BLOB_STORE_ID", "").removeprefix("store_")
        if not re.fullmatch(r"[A-Za-z0-9]{8,64}", store_id):
            raise StoreUnavailable("A private BLOB_STORE_ID must be configured.")
        self.origin = f"https://{store_id.lower()}.private.blob.vercel-storage.com"

    async def read(self, key):
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{self.origin}/{self.prefix}{key}.json", params={"cache": "0"},
                headers={"Authorization": "Bearer " + self.token, "Accept-Encoding": "identity"})
        if response.status_code == 404:
            return None, None
        if not response.is_success:
            raise StoreUnavailable("Persistent storage is unavailable.")
        etag = response.headers.get("etag", "")
        if not etag or etag.startswith("W/"):
            raise StoreUnavailable("Storage did not supply a strong version identifier. No write is allowed.")
        return response.json(), etag

    async def write(self, key, data, version):
        headers = {"Authorization": "Bearer " + self.token, "x-api-version": "12",
                   "x-vercel-blob-access": "private", "x-add-random-suffix": "0",
                   "x-content-type": "application/json", "x-allow-overwrite": "1" if version else "0"}
        if version:
            headers["x-if-match"] = version
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.put("https://blob.vercel-storage.com/", params={"pathname": self.prefix + key + ".json"},
                                        headers=headers, content=json.dumps(data).encode())
        if response.status_code in (409, 412):
            raise StoreConflict()
        if response.status_code == 400 and "already exists" in response.text:
            raise StoreConflict()
        if not response.is_success:
            raise StoreUnavailable("Persistent storage is unavailable. Please retry.")

    async def keys(self):
        keys, cursor = [], None
        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                params = {"prefix": self.prefix + "account-", "limit": "1000"}
                if cursor:
                    params["cursor"] = cursor
                response = await client.get("https://blob.vercel-storage.com/", params=params,
                    headers={"Authorization": "Bearer " + self.token, "x-api-version": "12"})
                if not response.is_success:
                    raise StoreUnavailable("Cannot list persistent plans.")
                data = response.json()
                keys.extend(item["pathname"][len(self.prefix):-5] for item in data["blobs"])
                if not data.get("hasMore"):
                    return keys
                cursor = data.get("cursor")
                if not cursor:
                    raise StoreUnavailable("Incomplete storage pagination.")


_store = None


def get_store():
    global _store
    if _store is None:
        if os.getenv("BLOB_READ_WRITE_TOKEN"):
            _store = BlobStore()
        elif os.getenv("VERCEL"):
            raise StoreUnavailable("Connect private Vercel Blob storage before activating autopilot.")
        else:
            _store = LocalStore()
    return _store


async def update_record(key, transform):
    if not re.fullmatch(r"(?:account-[a-f0-9]{64}|heartbeat|ai-budget-\d{4}-\d{2}-\d{2})", key):
        raise ValueError("Invalid record key")
    store = get_store()
    for attempt in range(5):
        current, version = await store.read(key)
        updated = transform(current)
        if updated == current:
            return updated
        try:
            await store.write(key, updated, version)
            return updated
        except StoreConflict:
            await asyncio.sleep(0.03 * (attempt + 1))
    raise StoreConflict("Plan changed concurrently. Refresh and retry.")
