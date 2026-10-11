import pytest

from app import config, main, store, worker


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELF_AUTOSTART_WORKER", "0")
    (tmp_path / "originals").mkdir()
    for module in [store, main, worker, config]:
        monkeypatch.setattr(module, "DATA", tmp_path)
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".env")
    for key in config.DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    store.init()
    return tmp_path


@pytest.fixture
def analyze():
    """Sources register as drafts now; tests that need a queued job start it explicitly."""

    def start(source):
        with store.connect() as db:
            db.execute("UPDATE sources SET status='queued' WHERE id=?", (source["id"],))
        return source

    return start
