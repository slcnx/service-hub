import os
import sys
import time
import shutil
import tempfile
from pathlib import Path
import pytest

# Ensure service_hub module can be imported
sys.path.insert(0, str(Path(__file__).parent.parent))

from service_hub import LogManager, HubSettings, app
from fastapi.testclient import TestClient

def test_log_manager_settings(monkeypatch):
    temp_dir = Path(tempfile.mkdtemp())
    monkeypatch.setattr("service_hub.CONFIG_DIR", temp_dir)
    monkeypatch.setattr("service_hub.SETTINGS_FILE", temp_dir / "settings.json")
    monkeypatch.setattr("service_hub.LOGS_DIR", temp_dir / "logs")
    (temp_dir / "logs").mkdir(parents=True, exist_ok=True)

    lm = LogManager()
    assert lm.settings.log_retention_days == 3
    assert lm.settings.log_auto_scroll is True
    assert lm.settings.max_log_file_size_mb == 20

    # Modify and save
    lm.settings.log_retention_days = 5
    lm.save_settings()

    lm2 = LogManager()
    assert lm2.settings.log_retention_days == 5

def test_copytruncate_rotation(monkeypatch):
    temp_dir = Path(tempfile.mkdtemp())
    logs_dir = temp_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("service_hub.CONFIG_DIR", temp_dir)
    monkeypatch.setattr("service_hub.SETTINGS_FILE", temp_dir / "settings.json")
    monkeypatch.setattr("service_hub.LOGS_DIR", logs_dir)

    lm = LogManager()
    logfile = logs_dir / "service-hub.log"
    logfile.write_text("line 1\nline 2\n")

    arch = lm.copytruncate_rotate(logfile)
    assert arch is not None
    assert arch.exists()
    assert logfile.exists()
    assert logfile.stat().st_size == 0
    assert "line 1" in arch.read_text()

def test_cleanup_expired_logs(monkeypatch):
    temp_dir = Path(tempfile.mkdtemp())
    logs_dir = temp_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("service_hub.CONFIG_DIR", temp_dir)
    monkeypatch.setattr("service_hub.SETTINGS_FILE", temp_dir / "settings.json")
    monkeypatch.setattr("service_hub.LOGS_DIR", logs_dir)

    lm = LogManager()
    lm.settings.log_retention_days = 3

    # Active log (should not be deleted)
    active = logs_dir / "service-hub.log"
    active.write_text("active content")

    # Recent archive (within 3 days)
    recent = logs_dir / "service-hub.log.recent"
    recent.write_text("recent archive")

    # Expired archive (older than 3 days)
    old = logs_dir / "service-hub.log.old_arch"
    old.write_text("old archive")
    old_time = time.time() - (4 * 86400)
    os.utime(old, (old_time, old_time))

    cleaned = lm.cleanup_expired_logs()
    assert "service-hub.log.old_arch" in cleaned
    assert not old.exists()
    assert recent.exists()
    assert active.exists()

def test_settings_api(monkeypatch):
    temp_dir = Path(tempfile.mkdtemp())
    logs_dir = temp_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("service_hub.CONFIG_DIR", temp_dir)
    monkeypatch.setattr("service_hub.SETTINGS_FILE", temp_dir / "settings.json")
    monkeypatch.setattr("service_hub.LOGS_DIR", logs_dir)

    import service_hub
    service_hub.log_manager = LogManager()

    client = TestClient(app)

    # GET /api/settings
    res = client.get("/api/settings")
    assert res.status_code == 200
    data = res.json()
    assert data["settings"]["log_retention_days"] == 3
    assert data["settings"]["log_auto_scroll"] is True

    # PUT /api/settings
    res = client.put("/api/settings", json={"log_retention_days": 7, "max_log_file_size_mb": 50})
    assert res.status_code == 200
    assert res.json()["settings"]["log_retention_days"] == 7
    assert res.json()["settings"]["max_log_file_size_mb"] == 50

    # POST /api/settings/logs/rotate
    (logs_dir / "service-hub.log").write_text("hub data\n")
    res = client.post("/api/settings/logs/rotate")
    assert res.status_code == 200
    assert res.json()["status"] == "rotated"

    # POST /api/settings/logs/cleanup
    res = client.post("/api/settings/logs/cleanup")
    assert res.status_code == 200
    assert res.json()["status"] == "cleaned"

    # GET /api/logs/hub
    (logs_dir / "service-hub.log").write_text("test line 1\ntest line 2\n")
    res = client.get("/api/logs/hub?lines=10")
    assert res.status_code == 200
    assert res.json()["count"] == 2
