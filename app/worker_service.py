"""Keep the single local ingestion worker running alongside the API."""

import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app import config

_process = None
_error = None


def running():
    path = config.DATA / "worker.lock.sqlite"
    if not path.exists():
        return False
    db = sqlite3.connect(path, timeout=0)
    try:
        db.execute("BEGIN EXCLUSIVE")
        db.rollback()
        return False
    except sqlite3.OperationalError:
        return True
    finally:
        db.close()


def status():
    return {"running": running(), "error": _error}


async def supervise():
    global _process, _error
    try:
        while True:
            if not running() and (_process is None or _process.poll() is not None):
                try:
                    with (config.DATA / "worker.log").open("ab", buffering=0) as log:
                        _process = subprocess.Popen(
                            [sys.executable, "-m", "app.worker"],
                            cwd=Path(__file__).resolve().parent.parent,
                            env={**os.environ, "DATA_DIR": str(config.DATA)},
                            stdout=log,
                            stderr=log,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        )
                    _error = None
                except OSError:
                    _error = "자료 분석기를 실행하지 못했습니다. 서버의 프로세스 실행 권한을 확인해주세요."
            await asyncio.sleep(3)
    finally:
        # Only stop the child owned by this API; an independently started worker is left alone.
        if _process is not None and _process.poll() is None:
            _process.terminate()
            try:
                await asyncio.to_thread(_process.wait, timeout=10)
            except subprocess.TimeoutExpired:
                _process.kill()
                await asyncio.to_thread(_process.wait)
        _process = None
