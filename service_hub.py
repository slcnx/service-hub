#!/usr/bin/env python3
"""
ServiceHub (macOS Process Manager & Supervisor)
- Runs in foreground/background with native macOS LaunchAgent identity
- Multiplexes colorful logs from all managed services into terminal
- Copytruncate log rotation: tail -f /Users/.../service-hub.log never breaks
- Default 3-day log retention with automatic background pruning
- Exposes RESTful HTTP API (CRUD) for full dynamic control
- Features auto-restart, process-group isolation, resource tracking (CPU/MEM)
- Responsive Web Dashboard with default auto-scroll & Swagger UI
"""

import os
import sys
import time
import signal
import asyncio
import logging
import shutil
from typing import Dict, List, Optional, Any
from collections import deque
from pathlib import Path
from datetime import datetime
import json

import psutil
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# --- ANSI Terminal Colors ---
COLORS = [
    "\033[36m",  # Cyan
    "\033[32m",  # Green
    "\033[33m",  # Yellow
    "\033[35m",  # Magenta
    "\033[34m",  # Blue
    "\033[96m",  # Bright Cyan
    "\033[92m",  # Bright Green
    "\033[93m",  # Bright Yellow
    "\033[95m",  # Bright Magenta
    "\033[94m",  # Bright Blue
]
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"

CONFIG_DIR = Path(os.environ.get("SERVICEHUB_CONFIG_DIR", Path.home() / ".config" / "service-hub"))
CONFIG_FILE = CONFIG_DIR / "services.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
LOGS_DIR = CONFIG_DIR / "logs"

# Ensure config directories exist
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


class ServiceConfig(BaseModel):
    name: str = Field(..., description="Unique service name (alphanumeric, dash, underscore)")
    command: str = Field(..., description="Shell command or executable to run")
    cwd: Optional[str] = Field(None, description="Working directory")
    env: Optional[Dict[str, str]] = Field(default_factory=dict, description="Custom environment variables")
    auto_restart: bool = Field(True, description="Automatically restart process if it crashes")
    autostart: bool = Field(True, description="Automatically start this service when ServiceHub starts")
    description: Optional[str] = Field("", description="Short description")


class ServiceUpdate(BaseModel):
    command: Optional[str] = None
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    auto_restart: Optional[bool] = None
    autostart: Optional[bool] = None
    description: Optional[str] = None


class HubSettings(BaseModel):
    log_retention_days: int = Field(3, ge=1, le=365, description="保留日志天数，默认3天")
    log_auto_scroll: bool = Field(True, description="网页日志弹窗默认开启自动滚动")
    max_log_file_size_mb: int = Field(20, ge=1, le=1024, description="单文件自动轮转大小阈值(MB)")
    auto_rotate_enabled: bool = Field(True, description="是否启用后台定时轮转与清理")


class HubSettingsUpdate(BaseModel):
    log_retention_days: Optional[int] = Field(None, ge=1, le=365)
    log_auto_scroll: Optional[bool] = None
    max_log_file_size_mb: Optional[int] = Field(None, ge=1, le=1024)
    auto_rotate_enabled: Optional[bool] = None


class LogManager:
    def __init__(self):
        self.settings = HubSettings()
        self.load_settings()

    def load_settings(self):
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.settings = HubSettings(**data)
            except Exception as e:
                print(f"{RED}[LogManager] Failed to load settings.json: {e}{RESET}")
        else:
            self.save_settings()

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                content = self.settings.model_dump_json(indent=2) if hasattr(self.settings, "model_dump_json") else json.dumps(self.settings.dict(), indent=2)
                f.write(content)
        except Exception as e:
            print(f"{RED}[LogManager] Failed to save settings.json: {e}{RESET}")

    def copytruncate_rotate(self, file_path: Path) -> Optional[Path]:
        """
        Copytruncate: copy current content to timestamped archive, then truncate original file to 0 in-place.
        Guarantees that running `tail -f ~/.config/service-hub/logs/service-hub.log` or open subprocess fds
        continue streaming without interruption or losing the file descriptor.
        """
        if not file_path.exists() or file_path.stat().st_size == 0:
            return None

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        archive_name = f"{file_path.name}.{timestamp}"
        archive_path = LOGS_DIR / archive_name

        try:
            shutil.copyfile(file_path, archive_path)
            with open(file_path, "r+", encoding="utf-8") as f:
                f.truncate(0)
            print(f"{GREEN}[LogManager] Rotated (copytruncate): {file_path.name} -> {archive_name}{RESET}", flush=True)
            return archive_path
        except Exception as e:
            print(f"{RED}[LogManager] Failed to rotate {file_path.name}: {e}{RESET}", flush=True)
            return None

    def rotate_all(self) -> List[str]:
        rotated = []
        for log_file in LOGS_DIR.glob("*.log"):
            if log_file.is_file() and log_file.stat().st_size > 0:
                arch = self.copytruncate_rotate(log_file)
                if arch:
                    rotated.append(arch.name)
        return rotated

    def check_and_auto_rotate(self) -> List[str]:
        if not self.settings.auto_rotate_enabled:
            return []
        threshold_bytes = self.settings.max_log_file_size_mb * 1024 * 1024
        rotated = []
        for log_file in LOGS_DIR.glob("*.log"):
            if log_file.is_file():
                try:
                    if log_file.stat().st_size >= threshold_bytes:
                        arch = self.copytruncate_rotate(log_file)
                        if arch:
                            rotated.append(arch.name)
                except Exception:
                    pass
        return rotated

    def cleanup_expired_logs(self) -> List[str]:
        """
        Delete log archive files (*.log.*) older than retention_days (default: 3 days).
        Active .log files are NEVER deleted, only rotated/truncated.
        """
        retention_seconds = self.settings.log_retention_days * 86400
        cutoff_time = time.time() - retention_seconds
        removed = []

        for item in LOGS_DIR.iterdir():
            if not item.is_file():
                continue
            is_archive = ".log." in item.name
            if is_archive:
                try:
                    if item.stat().st_mtime < cutoff_time:
                        item.unlink()
                        removed.append(item.name)
                        print(f"{YELLOW}[LogManager] Cleaned expired log archive (> {self.settings.log_retention_days} days): {item.name}{RESET}", flush=True)
                except Exception as e:
                    print(f"{RED}[LogManager] Error removing {item.name}: {e}{RESET}", flush=True)
        return removed

    def get_stats(self) -> Dict[str, Any]:
        files = []
        total_size = 0
        if LOGS_DIR.exists():
            for item in sorted(LOGS_DIR.iterdir(), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True):
                if item.is_file():
                    try:
                        st = item.stat()
                        total_size += st.st_size
                        mtime_str = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                        is_arch = ".log." in item.name
                        files.append({
                            "name": item.name,
                            "size_bytes": st.st_size,
                            "size_display": f"{st.st_size / 1024:.1f} KB" if st.st_size < 1048576 else f"{st.st_size / (1024*1024):.2f} MB",
                            "mtime": mtime_str,
                            "is_archive": is_arch,
                        })
                    except Exception:
                        pass
        return {
            "logs_dir": str(LOGS_DIR),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "file_count": len(files),
            "files": files,
        }


class ManagedService:
    def __init__(self, config: ServiceConfig, color_idx: int):
        self.config = config
        self.color = COLORS[color_idx % len(COLORS)]
        self.process: Optional[asyncio.subprocess.Process] = None
        self.status = "stopped"  # stopped, running, starting, crashed, stopping
        self.start_time: Optional[float] = None
        self.restarts = 0
        self.manual_stopped = not config.autostart
        self.log_history: deque = deque(maxlen=2000)
        self.sse_queues: List[asyncio.Queue] = []
        self._monitor_task: Optional[asyncio.Task] = None
        self._log_tasks: List[asyncio.Task] = []

    def log_terminal(self, text: str, is_err: bool = False):
        now_str = time.strftime("%H:%M:%S")
        prefix = f"{self.color}[{now_str} {self.config.name:14s}]{RESET}"
        if is_err:
            print(f"{prefix} {RED}{text}{RESET}", flush=True)
        else:
            print(f"{prefix} {text}", flush=True)

        entry = {"time": now_str, "text": text, "is_err": is_err}
        self.log_history.append(entry)

        # Write to service log file
        try:
            log_path = LOGS_DIR / f"{self.config.name}.log"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{now_str}] {text}\n")
        except Exception:
            pass

        # Broadcast to SSE queues
        dead_queues = []
        for q in self.sse_queues:
            try:
                q.put_nowait(entry)
            except Exception:
                dead_queues.append(q)
        for q in dead_queues:
            if q in self.sse_queues:
                self.sse_queues.remove(q)

    async def _read_stream(self, stream: asyncio.StreamReader, is_err: bool):
        while True:
            try:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                self.log_terminal(text, is_err=is_err)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log_terminal(f"Log stream read error: {e}", is_err=True)
                break

    async def start(self):
        if self.process is not None and self.process.returncode is None:
            return  # Already running

        self.status = "starting"
        self.manual_stopped = False
        self.log_terminal(f"Starting service: {self.config.command}")

        cwd = self.config.cwd or os.getcwd()
        env = os.environ.copy()
        if self.config.env:
            env.update(self.config.env)

        try:
            # start_new_session=True creates a new process group for clean group kills on macOS
            self.process = await asyncio.create_subprocess_shell(
                self.config.command,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            self.status = "running"
            self.start_time = time.time()
            self.log_terminal(f"Service started with PID {self.process.pid}")

            # Start reading stdout and stderr
            self._log_tasks = [
                asyncio.create_task(self._read_stream(self.process.stdout, is_err=False)),
                asyncio.create_task(self._read_stream(self.process.stderr, is_err=True)),
            ]

            # Start monitor task
            self._monitor_task = asyncio.create_task(self._watch_process())
        except Exception as e:
            self.status = "crashed"
            self.log_terminal(f"Failed to start: {e}", is_err=True)

    async def _watch_process(self):
        try:
            return_code = await self.process.wait()
            self.log_terminal(f"Process exited with code {return_code}", is_err=(return_code != 0))

            for task in self._log_tasks:
                if not task.done():
                    task.cancel()

            if self.manual_stopped:
                self.status = "stopped"
            else:
                if return_code == 0:
                    self.status = "stopped"
                else:
                    self.status = "crashed"

                # Auto restart check
                if self.config.auto_restart and not self.manual_stopped:
                    self.restarts += 1
                    self.log_terminal(f"Auto-restart triggered (attempt {self.restarts}) in 2 seconds...")
                    await asyncio.sleep(2.0)
                    if not self.manual_stopped:
                        await self.start()
        except asyncio.CancelledError:
            pass

    async def stop(self):
        self.manual_stopped = True
        if self.process is None or self.process.returncode is not None:
            self.status = "stopped"
            return

        self.status = "stopping"
        self.log_terminal("Stopping service...")

        pid = self.process.pid
        try:
            # Kill process group on macOS
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:
            try:
                self.process.terminate()
            except Exception:
                pass

        # Wait up to 4s for graceful shutdown
        for _ in range(40):
            if self.process.returncode is not None:
                break
            await asyncio.sleep(0.1)

        # Force kill if still alive
        if self.process.returncode is None:
            self.log_terminal("Service did not exit gracefully, sending SIGKILL...", is_err=True)
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()

        self.status = "stopped"
        self.log_terminal("Service stopped.")

    async def restart(self):
        await self.stop()
        await asyncio.sleep(0.5)
        await self.start()

    def get_stats(self) -> Dict[str, Any]:
        cpu_percent = 0.0
        memory_mb = 0.0
        pid = self.process.pid if (self.process and self.process.returncode is None) else None
        uptime = round(time.time() - self.start_time, 1) if (self.start_time and pid) else 0.0

        if pid:
            try:
                p = psutil.Process(pid)
                cpu_percent = round(p.cpu_percent(interval=None), 1)
                memory_mb = round(p.memory_info().rss / (1024 * 1024), 1)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        return {
            "name": self.config.name,
            "command": self.config.command,
            "cwd": self.config.cwd or os.getcwd(),
            "status": self.status,
            "pid": pid,
            "cpu_percent": cpu_percent,
            "memory_mb": memory_mb,
            "uptime_seconds": uptime,
            "restarts": self.restarts,
            "auto_restart": self.config.auto_restart,
            "autostart": self.config.autostart,
            "description": self.config.description or "",
            "env": self.config.env or {},
        }


class ServiceManager:
    def __init__(self):
        self.services: Dict[str, ManagedService] = {}
        self.color_counter = 0

    def load_configs(self):
        if not CONFIG_FILE.exists():
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                cfg = ServiceConfig(**item)
                self.add_service(cfg, autostart=False)
        except Exception as e:
            print(f"{RED}[ServiceHub] Failed to load config: {e}{RESET}")

    def save_configs(self):
        data = [s.config.model_dump() if hasattr(s.config, "model_dump") else s.config.dict() for s in self.services.values()]
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"{RED}[ServiceHub] Failed to save config: {e}{RESET}")

    def add_service(self, config: ServiceConfig, autostart: bool = True) -> ManagedService:
        if config.name in self.services:
            raise ValueError(f"Service '{config.name}' already exists.")
        svc = ManagedService(config, self.color_counter)
        self.color_counter += 1
        self.services[config.name] = svc
        self.save_configs()
        return svc

    async def remove_service(self, name: str):
        if name not in self.services:
            raise KeyError(f"Service '{name}' not found.")
        svc = self.services[name]
        await svc.stop()
        del self.services[name]
        self.save_configs()

    async def start_all(self):
        for svc in self.services.values():
            if svc.config.autostart:
                await svc.start()

    async def stop_all(self):
        print(f"\n{BOLD}[ServiceHub] Shutting down all services gracefully...{RESET}")
        tasks = [svc.stop() for svc in self.services.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


# --- Global Instances ---
manager = ServiceManager()
log_manager = LogManager()
app = FastAPI(
    title="ServiceHub - Local Process Manager",
    description="Manage persistent background/foreground services on macOS with RESTful CRUD, copytruncate logs, and 3-day retention.",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Background Periodic Maintenance Task ---
async def background_log_maintenance_loop():
    """Periodically check file sizes for rotation and prune logs older than 3 days."""
    while True:
        try:
            await asyncio.sleep(1800)  # Check every 30 minutes
            log_manager.check_and_auto_rotate()
            log_manager.cleanup_expired_logs()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"{RED}[ServiceHub] Error in log maintenance loop: {e}{RESET}", flush=True)


# --- REST API Endpoints (CRUD) ---

@app.get("/api/services", summary="List all services (Read All)")
async def list_services():
    """Returns list of all managed services and their current real-time stats."""
    return [svc.get_stats() for svc in manager.services.values()]


@app.post("/api/services", summary="Create new service (Create)")
async def create_service(payload: ServiceConfig):
    """Registers and optionally starts a new service."""
    if payload.name in manager.services:
        raise HTTPException(status_code=400, detail=f"Service '{payload.name}' already exists.")
    svc = manager.add_service(payload)
    if payload.autostart:
        asyncio.create_task(svc.start())
    return {"status": "created", "service": svc.get_stats()}


@app.get("/api/services/{name}", summary="Get service details (Read One)")
async def get_service(name: str):
    """Get metrics and configuration of a single service."""
    if name not in manager.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    return manager.services[name].get_stats()


@app.put("/api/services/{name}", summary="Update service config (Update)")
async def update_service(name: str, payload: ServiceUpdate):
    """Updates service configuration (command, cwd, env, auto_restart, etc.)."""
    if name not in manager.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    svc = manager.services[name]
    cfg = svc.config

    if payload.command is not None:
        cfg.command = payload.command
    if payload.cwd is not None:
        cfg.cwd = payload.cwd
    if payload.env is not None:
        cfg.env = payload.env
    if payload.auto_restart is not None:
        cfg.auto_restart = payload.auto_restart
    if payload.autostart is not None:
        cfg.autostart = payload.autostart
    if payload.description is not None:
        cfg.description = payload.description

    manager.save_configs()
    return {"status": "updated", "service": svc.get_stats()}


@app.delete("/api/services/{name}", summary="Delete service (Delete)")
async def delete_service(name: str):
    """Stops the process and removes the service permanently."""
    if name not in manager.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    await manager.remove_service(name)
    return {"status": "deleted", "name": name}


@app.post("/api/services/{name}/start", summary="Start service")
async def start_service(name: str):
    if name not in manager.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    svc = manager.services[name]
    await svc.start()
    return {"status": "started", "service": svc.get_stats()}


@app.post("/api/services/{name}/stop", summary="Stop service")
async def stop_service(name: str):
    if name not in manager.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    svc = manager.services[name]
    await svc.stop()
    return {"status": "stopped", "service": svc.get_stats()}


@app.post("/api/services/{name}/restart", summary="Restart service")
async def restart_service(name: str):
    if name not in manager.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    svc = manager.services[name]
    await svc.restart()
    return {"status": "restarted", "service": svc.get_stats()}


@app.get("/api/services/{name}/logs", summary="Get historical service logs")
async def get_logs(name: str, lines: int = Query(100, ge=1, le=2000)):
    if name not in manager.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    svc = manager.services[name]
    recent = list(svc.log_history)[-lines:]
    return {"name": name, "count": len(recent), "logs": recent}


@app.get("/api/services/{name}/logs/stream", summary="Stream live service logs (SSE)")
async def stream_logs(name: str):
    if name not in manager.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    svc = manager.services[name]
    queue: asyncio.Queue = asyncio.Queue()
    svc.sse_queues.append(queue)

    async def event_generator():
        # First send last 25 lines
        for item in list(svc.log_history)[-25:]:
            yield f"data: {json.dumps(item)}\n\n"
        try:
            while True:
                entry = await queue.get()
                yield f"data: {json.dumps(entry)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in svc.sse_queues:
                svc.sse_queues.remove(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# --- Hub Logs & Settings APIs ---

@app.get("/api/settings", summary="Get system settings and log storage statistics")
async def get_settings():
    """Returns current log retention configuration, auto-scroll preference, and disk stats."""
    settings_data = log_manager.settings.model_dump() if hasattr(log_manager.settings, "model_dump") else log_manager.settings.dict()
    return {
        "settings": settings_data,
        "stats": log_manager.get_stats(),
    }


@app.put("/api/settings", summary="Update system settings")
async def update_settings(payload: HubSettingsUpdate):
    """Updates log retention days, auto-scroll defaults, or max log file size."""
    if payload.log_retention_days is not None:
        log_manager.settings.log_retention_days = payload.log_retention_days
    if payload.log_auto_scroll is not None:
        log_manager.settings.log_auto_scroll = payload.log_auto_scroll
    if payload.max_log_file_size_mb is not None:
        log_manager.settings.max_log_file_size_mb = payload.max_log_file_size_mb
    if payload.auto_rotate_enabled is not None:
        log_manager.settings.auto_rotate_enabled = payload.auto_rotate_enabled

    log_manager.save_settings()
    settings_data = log_manager.settings.model_dump() if hasattr(log_manager.settings, "model_dump") else log_manager.settings.dict()
    return {"status": "updated", "settings": settings_data}


@app.post("/api/settings/logs/rotate", summary="Trigger manual copytruncate rotation")
async def trigger_rotate():
    """Manually rotates all active non-empty logs using copytruncate, preserving running tail -f."""
    rotated = log_manager.rotate_all()
    return {"status": "rotated", "count": len(rotated), "files": rotated}


@app.post("/api/settings/logs/cleanup", summary="Trigger manual cleanup of expired logs (> retention_days)")
async def trigger_cleanup():
    """Deletes log archives older than configured retention days (default 3 days)."""
    cleaned = log_manager.cleanup_expired_logs()
    return {"status": "cleaned", "count": len(cleaned), "files": cleaned, "retention_days": log_manager.settings.log_retention_days}


@app.get("/api/logs/hub", summary="Get supervisor service-hub.log tail")
async def get_hub_logs(lines: int = Query(100, ge=1, le=2000)):
    """Returns recent lines from main daemon log (~/.config/service-hub/logs/service-hub.log)."""
    hub_log = LOGS_DIR / "service-hub.log"
    if not hub_log.exists():
        return {"name": "service-hub", "count": 0, "logs": []}
    try:
        with open(hub_log, "r", encoding="utf-8", errors="replace") as f:
            tail_lines = deque(f, maxlen=lines)
        entries = [{"time": "", "text": l.rstrip("\r\n"), "is_err": ("error" in l.lower() or "exception" in l.lower())} for l in tail_lines]
        return {"name": "service-hub", "count": len(entries), "logs": entries}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/logs/hub/stream", summary="Stream live supervisor service-hub.log (SSE)")
async def stream_hub_logs():
    """SSE live tail of service-hub.log, automatically resuming if file is copytruncated."""
    hub_log = LOGS_DIR / "service-hub.log"
    if not hub_log.exists():
        hub_log.touch()

    async def event_generator():
        # Yield last 35 lines initially
        try:
            with open(hub_log, "r", encoding="utf-8", errors="replace") as f:
                initial_lines = deque(f, maxlen=35)
            for l in initial_lines:
                clean = l.rstrip("\r\n")
                yield f"data: {json.dumps({'time': '', 'text': clean, 'is_err': ('error' in clean.lower() or 'exception' in clean.lower())})}\n\n"
        except Exception:
            pass

        # Continuously tail new lines
        try:
            with open(hub_log, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, os.SEEK_END)
                while True:
                    # Detect copytruncate: if file size shrank, reset seek to 0
                    try:
                        cur_size = os.path.getsize(hub_log)
                        if f.tell() > cur_size:
                            f.seek(0, os.SEEK_SET)
                    except Exception:
                        pass

                    line = f.readline()
                    if line:
                        clean = line.rstrip("\r\n")
                        yield f"data: {json.dumps({'time': time.strftime('%H:%M:%S'), 'text': clean, 'is_err': ('error' in clean.lower() or 'exception' in clean.lower())})}\n\n"
                    else:
                        await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# --- Embedded Responsive Web Dashboard ---
@app.get("/", response_class=HTMLResponse, summary="Web Dashboard")
async def dashboard():
    return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ServiceHub - 本地前台服务管家</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .status-running { background-color: #10B981; }
    .status-stopped { background-color: #6B7280; }
    .status-crashed { background-color: #EF4444; }
    .status-starting { background-color: #F59E0B; }
    /* Custom scrollbar */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0f172a; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #475569; }
  </style>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen">
  <div class="max-w-7xl mx-auto px-4 py-8">
    <!-- Header -->
    <div class="flex flex-col md:flex-row items-start md:items-center justify-between pb-6 border-b border-slate-800 gap-4">
      <div>
        <div class="flex items-center gap-3">
          <span class="text-3xl">🚀</span>
          <h1 class="text-2xl font-bold tracking-tight">ServiceHub</h1>
          <span class="px-2.5 py-0.5 text-xs font-semibold rounded-full bg-indigo-500/20 text-indigo-400 border border-indigo-500/30">macOS Daemon</span>
        </div>
        <p class="text-slate-400 text-sm mt-1">常驻托管 · Copytruncate 日志轮转 · 默认保留3天 · 全功能 HTTP CRUD</p>
      </div>
      <div class="flex flex-wrap items-center gap-2.5">
        <button onclick="openHubLogViewer()" class="px-3.5 py-2 text-sm bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg transition border border-slate-700 flex items-center gap-1.5 shadow-sm">
          <span>📋 主管家日志</span>
        </button>
        <button onclick="openSettingsModal()" class="px-3.5 py-2 text-sm bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg transition border border-slate-700 flex items-center gap-1.5 shadow-sm">
          <span>⚙️ 日志与设置</span>
        </button>
        <a href="/docs" target="_blank" class="px-3 py-2 text-sm bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg transition border border-slate-700 flex items-center gap-1.5">
          <span>📖 Swagger</span>
        </a>
        <button onclick="openModal()" class="px-4 py-2 text-sm bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg shadow-lg shadow-indigo-600/20 transition flex items-center gap-1.5">
          <span>➕ 添加新服务</span>
        </button>
      </div>
    </div>

    <!-- Toast Notification Container -->
    <div id="toast" class="fixed top-4 right-4 z-50 transform transition-all duration-300 opacity-0 translate-y-[-10px] pointer-events-none">
      <div id="toast-content" class="px-4 py-3 rounded-xl shadow-2xl text-sm font-medium flex items-center gap-2 bg-indigo-600 text-white border border-indigo-400/30">
      </div>
    </div>

    <!-- Services Grid -->
    <div class="mt-8">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold text-slate-200">托管服务列表</h2>
        <span id="service-count" class="text-xs text-slate-400">正在刷新...</span>
      </div>
      <div id="services-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <!-- Cards dynamically injected -->
      </div>
    </div>

    <!-- Log Viewer Modal -->
    <div id="log-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
      <div class="bg-slate-800 border border-slate-700 rounded-2xl w-full max-w-5xl max-h-[88vh] flex flex-col shadow-2xl overflow-hidden">
        <div class="px-6 py-4 border-b border-slate-700 flex items-center justify-between bg-slate-850">
          <div class="flex items-center gap-3">
            <span class="text-xl">📜</span>
            <div>
              <h3 id="log-title" class="text-base font-bold text-slate-200">实时日志</h3>
              <p id="log-subtitle" class="text-xs text-slate-400 font-mono">连接中...</p>
            </div>
            <span id="log-status" class="text-xs px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 ml-2">Live SSE</span>
          </div>
          <button onclick="closeLogModal()" class="text-slate-400 hover:text-slate-200 text-2xl font-bold leading-none">&times;</button>
        </div>
        <div id="log-content" class="p-4 bg-slate-950 font-mono text-xs text-slate-300 overflow-y-auto flex-1 h-[520px] space-y-1 select-text">
        </div>
        <div class="px-6 py-3 border-t border-slate-700 flex flex-wrap justify-between items-center text-xs text-slate-400 bg-slate-900/60 gap-3">
          <label class="flex items-center gap-2 cursor-pointer select-none text-slate-300">
            <input type="checkbox" id="log-autoscroll" checked onchange="toggleAutoScroll(this.checked)" class="rounded bg-slate-900 border-slate-700 text-indigo-600 focus:ring-0">
            <span>默认自动滚动 (Auto-scroll)</span>
          </label>
          <div class="flex items-center gap-3">
            <button onclick="clearLogViewer()" class="hover:text-slate-200 px-2.5 py-1 bg-slate-800 rounded border border-slate-700">清空窗口</button>
            <button onclick="closeLogModal()" class="hover:text-slate-200 px-3 py-1 bg-slate-700 text-slate-200 rounded">关闭</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Settings Modal -->
    <div id="settings-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
      <div class="bg-slate-800 border border-slate-700 rounded-2xl w-full max-w-2xl shadow-2xl p-6 flex flex-col max-h-[90vh]">
        <div class="flex justify-between items-center pb-4 border-b border-slate-700">
          <div class="flex items-center gap-2.5">
            <span class="text-2xl">⚙️</span>
            <div>
              <h3 class="text-lg font-bold text-slate-200">系统与日志配置</h3>
              <p class="text-xs text-slate-400">日志保留期限 · Copytruncate 轮转 · 磁盘容量状态</p>
            </div>
          </div>
          <button onclick="closeSettingsModal()" class="text-slate-400 hover:text-slate-200 text-2xl font-bold leading-none">&times;</button>
        </div>
        
        <div class="overflow-y-auto flex-1 py-4 space-y-6">
          <form id="settings-form" onsubmit="handleSaveSettings(event)" class="space-y-4">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label class="block text-xs font-semibold text-slate-300 mb-1">日志保留天数 (只保留 N 天)</label>
                <input type="number" id="setting-retention-days" min="1" max="365" required class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
                <p class="text-[11px] text-slate-400 mt-1">默认 3 天。系统定时自动清理超过此期限的归档日志。</p>
              </div>
              <div>
                <label class="block text-xs font-semibold text-slate-300 mb-1">单日志文件轮转阈值 (MB)</label>
                <input type="number" id="setting-max-size" min="1" max="1024" required class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
                <p class="text-[11px] text-slate-400 mt-1">默认 20MB。超额自动采用 Copytruncate 轮转截断。</p>
              </div>
            </div>

            <div class="flex flex-wrap items-center gap-6 pt-1">
              <label class="flex items-center gap-2 text-sm text-slate-300 cursor-pointer select-none">
                <input type="checkbox" id="setting-autoscroll" class="rounded bg-slate-900 border-slate-700 text-indigo-600 focus:ring-0">
                <span>日志弹窗默认开启自动滚动</span>
              </label>
              <label class="flex items-center gap-2 text-sm text-slate-300 cursor-pointer select-none">
                <input type="checkbox" id="setting-autorotate" class="rounded bg-slate-900 border-slate-700 text-indigo-600 focus:ring-0">
                <span>启用后台自动轮转与清理 (每30分钟)</span>
              </label>
            </div>

            <div class="flex justify-end pt-2">
              <button type="submit" class="px-4 py-2 text-xs bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg shadow transition">
                💾 保存配置
              </button>
            </div>
          </form>

          <!-- Disk usage & Action bar -->
          <div class="border-t border-slate-700/80 pt-4">
            <div class="flex items-center justify-between mb-2">
              <h4 class="text-sm font-semibold text-slate-200">日志存储状态 (Logs Directory)</h4>
              <span id="log-disk-usage" class="text-xs text-indigo-300 font-mono font-medium">加载中...</span>
            </div>
            <p class="text-xs text-slate-500 mb-3 font-mono break-all" id="log-dir-path"></p>
            
            <div class="flex flex-wrap items-center gap-2 mb-4">
              <button onclick="triggerLogRotate()" class="px-3 py-1.5 text-xs bg-slate-700 hover:bg-slate-600 text-slate-200 rounded-lg border border-slate-600 flex items-center gap-1.5 transition">
                <span>🔄 立即 Copytruncate 轮转</span>
              </button>
              <button onclick="triggerLogCleanup()" class="px-3 py-1.5 text-xs bg-rose-900/40 hover:bg-rose-900/60 text-rose-300 rounded-lg border border-rose-800/50 flex items-center gap-1.5 transition">
                <span>🧹 立即清理超期历史日志</span>
              </button>
            </div>

            <!-- Files list table -->
            <div class="bg-slate-950/80 border border-slate-800 rounded-xl overflow-hidden max-h-48 overflow-y-auto">
              <table class="w-full text-left text-xs text-slate-400">
                <thead class="bg-slate-900/90 text-slate-300 sticky top-0 border-b border-slate-800">
                  <tr>
                    <th class="py-2 px-3">文件名</th>
                    <th class="py-2 px-3">大小</th>
                    <th class="py-2 px-3">修改时间</th>
                    <th class="py-2 px-3">属性</th>
                  </tr>
                </thead>
                <tbody id="log-files-tbody" class="divide-y divide-slate-800/50 font-mono">
                  <!-- Dynamically populated -->
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Add Service Modal -->
    <div id="add-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
      <div class="bg-slate-800 border border-slate-700 rounded-2xl w-full max-w-lg shadow-2xl p-6">
        <div class="flex justify-between items-center pb-4 border-b border-slate-700">
          <h3 class="text-lg font-bold text-slate-200">注册新服务</h3>
          <button onclick="closeModal()" class="text-slate-400 hover:text-slate-200 text-2xl font-bold leading-none">&times;</button>
        </div>
        <form onsubmit="handleCreate(event)" class="mt-4 space-y-4">
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1">服务唯一名称 (Name)</label>
            <input id="form-name" required placeholder="如: discord-agent 或 proxy-worker" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1">启动命令 (Command)</label>
            <input id="form-command" required placeholder="如: python app.py 或 npm start" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1">运行工作目录 (CWD - 选填)</label>
            <input id="form-cwd" placeholder="默认当前执行路径" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1">服务简要说明 (选填)</label>
            <input id="form-desc" placeholder="简述该服务用途" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
          </div>
          <div class="flex items-center gap-6 pt-2">
            <label class="flex items-center gap-2 text-sm text-slate-300 cursor-pointer select-none">
              <input type="checkbox" id="form-autorestart" checked class="rounded bg-slate-900 border-slate-700 text-indigo-600 focus:ring-0">
              <span>崩溃自动重启</span>
            </label>
            <label class="flex items-center gap-2 text-sm text-slate-300 cursor-pointer select-none">
              <input type="checkbox" id="form-autostart" checked class="rounded bg-slate-900 border-slate-700 text-indigo-600 focus:ring-0">
              <span>随管家自启动</span>
            </label>
          </div>
          <div class="flex justify-end gap-3 pt-4 border-t border-slate-700">
            <button type="button" onclick="closeModal()" class="px-4 py-2 text-sm rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300 transition">取消</button>
            <button type="submit" class="px-4 py-2 text-sm rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium transition">创建并启动</button>
          </div>
        </form>
      </div>
    </div>

  </div>

  <script>
    let activeEventSource = null;
    let autoScroll = true;

    function showToast(message, isError = false) {
      const toast = document.getElementById('toast');
      const content = document.getElementById('toast-content');
      content.className = isError 
        ? 'px-4 py-3 rounded-xl shadow-2xl text-sm font-medium flex items-center gap-2 bg-rose-600 text-white border border-rose-400/30'
        : 'px-4 py-3 rounded-xl shadow-2xl text-sm font-medium flex items-center gap-2 bg-indigo-600 text-white border border-indigo-400/30';
      content.innerHTML = `<span>${isError ? '⚠️' : '✅'}</span><span>${message}</span>`;
      toast.classList.remove('opacity-0', 'translate-y-[-10px]', 'pointer-events-none');
      setTimeout(() => {
        toast.classList.add('opacity-0', 'translate-y-[-10px]', 'pointer-events-none');
      }, 3000);
    }

    async function fetchServices() {
      try {
        const res = await fetch('/api/services');
        const services = await res.json();
        renderServices(services);
      } catch (err) {
        console.error('Failed to fetch services:', err);
      }
    }

    function renderServices(services) {
      const container = document.getElementById('services-container');
      document.getElementById('service-count').innerText = `共托管 ${services.length} 个服务 · 每 2 秒自动刷新`;

      if (services.length === 0) {
        container.innerHTML = `
          <div class="col-span-full py-12 text-center border-2 border-dashed border-slate-800 rounded-2xl">
            <span class="text-4xl">📦</span>
            <p class="mt-2 text-sm text-slate-400">当前尚未托管任何服务</p>
            <button onclick="openModal()" class="mt-4 px-4 py-2 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg">点击立即添加一个</button>
          </div>
        `;
        return;
      }

      container.innerHTML = services.map(s => {
        const isRunning = s.status === 'running';
        const badgeColor = isRunning ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' : 
                           (s.status === 'crashed' ? 'bg-rose-500/20 text-rose-400 border-rose-500/30' : 'bg-slate-500/20 text-slate-400 border-slate-500/30');

        return `
          <div class="bg-slate-800/80 border border-slate-700/80 rounded-xl p-5 flex flex-col justify-between hover:border-slate-600 transition shadow-lg">
            <div>
              <div class="flex items-center justify-between mb-3">
                <div class="truncate pr-2">
                  <h3 class="font-bold text-slate-100 text-base truncate" title="${s.name}">${s.name}</h3>
                  ${s.description ? `<p class="text-xs text-slate-400 truncate mt-0.5">${s.description}</p>` : ''}
                </div>
                <span class="px-2 py-0.5 text-xs font-semibold rounded-full border shrink-0 ${badgeColor}">
                  ${s.status.toUpperCase()}
                </span>
              </div>
              <p class="text-xs font-mono bg-slate-900/90 text-indigo-300 p-2 rounded-lg border border-slate-800 truncate mb-3 select-all" title="${s.command}">
                $ ${s.command}
              </p>
              <div class="grid grid-cols-2 gap-2 text-xs text-slate-400 mb-4">
                <div>PID: <span class="text-slate-200 font-mono font-medium">${s.pid || '-'}</span></div>
                <div>重启次数: <span class="text-slate-200 font-mono font-medium">${s.restarts}</span></div>
                <div>CPU: <span class="text-slate-200 font-mono font-medium">${s.cpu_percent}%</span></div>
                <div>内存: <span class="text-slate-200 font-mono font-medium">${s.memory_mb} MB</span></div>
                <div class="col-span-2">运行时长: <span class="text-slate-200 font-mono font-medium">${formatUptime(s.uptime_seconds)}</span></div>
              </div>
            </div>

            <div class="pt-4 border-t border-slate-700/60 flex items-center justify-between gap-2">
              <div class="flex items-center gap-1.5">
                ${isRunning ? `
                  <button onclick="controlService('${s.name}', 'stop')" class="px-2.5 py-1.5 text-xs bg-amber-600/20 text-amber-300 hover:bg-amber-600/30 rounded-lg border border-amber-600/40 transition">停止</button>
                  <button onclick="controlService('${s.name}', 'restart')" class="px-2.5 py-1.5 text-xs bg-indigo-600/20 text-indigo-300 hover:bg-indigo-600/30 rounded-lg border border-indigo-600/40 transition">重启</button>
                ` : `
                  <button onclick="controlService('${s.name}', 'start')" class="px-2.5 py-1.5 text-xs bg-emerald-600/20 text-emerald-300 hover:bg-emerald-600/30 rounded-lg border border-emerald-600/40 transition">启动</button>
                `}
                <button onclick="openLogViewer('${s.name}')" class="px-2.5 py-1.5 text-xs bg-slate-700 hover:bg-slate-600 text-slate-200 rounded-lg transition">日志</button>
              </div>
              <button onclick="deleteService('${s.name}')" class="text-xs text-rose-400 hover:text-rose-300 p-1.5 transition" title="删除服务">🗑️</button>
            </div>
          </div>
        `;
      }).join('');
    }

    function formatUptime(seconds) {
      if (!seconds) return '0s';
      const m = Math.floor(seconds / 60);
      const s = Math.floor(seconds % 60);
      if (m > 60) {
        const h = Math.floor(m / 60);
        return `${h}h ${m % 60}m`;
      }
      return `${m}m ${s}s`;
    }

    async function controlService(name, action) {
      try {
        await fetch(`/api/services/${name}/${action}`, { method: 'POST' });
        fetchServices();
      } catch (err) {
        showToast('操作失败: ' + err, true);
      }
    }

    async function deleteService(name) {
      if (!confirm(`确定要删除服务 "${name}" 吗？该操作将停止并移除它。`)) return;
      try {
        await fetch(`/api/services/${name}`, { method: 'DELETE' });
        showToast(`服务 ${name} 已删除`);
        fetchServices();
      } catch (err) {
        showToast('删除失败: ' + err, true);
      }
    }

    async function handleCreate(e) {
      e.preventDefault();
      const payload = {
        name: document.getElementById('form-name').value.trim(),
        command: document.getElementById('form-command').value.trim(),
        cwd: document.getElementById('form-cwd').value.trim() || null,
        description: document.getElementById('form-desc').value.trim() || "",
        auto_restart: document.getElementById('form-autorestart').checked,
        autostart: document.getElementById('form-autostart').checked,
      };
      try {
        const res = await fetch('/api/services', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (!res.ok) {
          const err = await res.json();
          showToast('创建失败: ' + (err.detail || '未知错误'), true);
          return;
        }
        closeModal();
        showToast(`服务 ${payload.name} 注册成功`);
        fetchServices();
      } catch (err) {
        showToast('网络错误: ' + err, true);
      }
    }

    function openModal() { document.getElementById('add-modal').classList.remove('hidden'); }
    function closeModal() { document.getElementById('add-modal').classList.add('hidden'); }

    function toggleAutoScroll(enabled) {
      autoScroll = enabled;
      if (autoScroll) {
        const content = document.getElementById('log-content');
        content.scrollTop = content.scrollHeight;
      }
    }

    function openLogViewer(name) {
      const isHub = (name === '__hub__');
      document.getElementById('log-title').innerText = isHub ? '主管家日志 - [service-hub.log]' : `实时日志 - [${name}]`;
      document.getElementById('log-subtitle').innerText = isHub 
        ? 'tail -f ~/.config/service-hub/logs/service-hub.log' 
        : `tail -f ~/.config/service-hub/logs/${name}.log`;

      const content = document.getElementById('log-content');
      content.innerHTML = '<div class="text-slate-500">正在建立 SSE 日志连接...</div>';
      
      const scrollCheckbox = document.getElementById('log-autoscroll');
      scrollCheckbox.checked = true;
      autoScroll = true;

      document.getElementById('log-modal').classList.remove('hidden');

      if (activeEventSource) {
        activeEventSource.close();
      }

      const streamUrl = isHub ? '/api/logs/hub/stream' : `/api/services/${name}/logs/stream`;
      activeEventSource = new EventSource(streamUrl);
      content.innerHTML = '';

      activeEventSource.onmessage = function(e) {
        try {
          const data = JSON.parse(e.data);
          const p = document.createElement('div');
          p.className = data.is_err ? 'text-rose-400 break-all' : 'text-slate-300 break-all';
          const timePrefix = data.time ? `[${data.time}] ` : '';
          p.innerText = `${timePrefix}${data.text}`;
          content.appendChild(p);

          if (autoScroll) {
            content.scrollTop = content.scrollHeight;
          }
        } catch (err) {}
      };

      activeEventSource.onerror = function() {
        const p = document.createElement('div');
        p.className = 'text-amber-400';
        p.innerText = '[System] 日志连接已断开或暂无新输出';
        content.appendChild(p);
      };
    }

    function openHubLogViewer() {
      openLogViewer('__hub__');
    }

    function closeLogModal() {
      if (activeEventSource) {
        activeEventSource.close();
        activeEventSource = null;
      }
      document.getElementById('log-modal').classList.add('hidden');
    }

    function clearLogViewer() {
      document.getElementById('log-content').innerHTML = '';
    }

    // --- Settings Modal Functions ---
    async function openSettingsModal() {
      document.getElementById('settings-modal').classList.remove('hidden');
      await refreshSettingsData();
    }

    function closeSettingsModal() {
      document.getElementById('settings-modal').classList.add('hidden');
    }

    async function refreshSettingsData() {
      try {
        const res = await fetch('/api/settings');
        const data = await res.json();
        const s = data.settings;
        const stats = data.stats;

        document.getElementById('setting-retention-days').value = s.log_retention_days;
        document.getElementById('setting-max-size').value = s.max_log_file_size_mb;
        document.getElementById('setting-autoscroll').checked = s.log_auto_scroll;
        document.getElementById('setting-autorotate').checked = s.auto_rotate_enabled;

        document.getElementById('log-disk-usage').innerText = `共 ${stats.file_count} 个文件 · 占用 ${stats.total_size_mb} MB`;
        document.getElementById('log-dir-path').innerText = stats.logs_dir;

        const tbody = document.getElementById('log-files-tbody');
        if (stats.files.length === 0) {
          tbody.innerHTML = '<tr><td colspan="4" class="py-3 px-3 text-center text-slate-500">暂无日志文件</td></tr>';
        } else {
          tbody.innerHTML = stats.files.map(f => `
            <tr class="hover:bg-slate-900/50">
              <td class="py-1.5 px-3 truncate max-w-xs text-slate-200" title="${f.name}">${f.name}</td>
              <td class="py-1.5 px-3 text-slate-400">${f.size_display}</td>
              <td class="py-1.5 px-3 text-slate-500">${f.mtime}</td>
              <td class="py-1.5 px-3">
                ${f.is_archive 
                  ? '<span class="px-1.5 py-0.5 text-[10px] rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">历史归档</span>' 
                  : '<span class="px-1.5 py-0.5 text-[10px] rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">当前活跃</span>'}
              </td>
            </tr>
          `).join('');
        }
      } catch (err) {
        showToast('获取配置失败: ' + err, true);
      }
    }

    async function handleSaveSettings(e) {
      e.preventDefault();
      const payload = {
        log_retention_days: parseInt(document.getElementById('setting-retention-days').value),
        max_log_file_size_mb: parseInt(document.getElementById('setting-max-size').value),
        log_auto_scroll: document.getElementById('setting-autoscroll').checked,
        auto_rotate_enabled: document.getElementById('setting-autorotate').checked,
      };
      try {
        const res = await fetch('/api/settings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (res.ok) {
          showToast('配置保存成功！');
          refreshSettingsData();
        } else {
          showToast('保存失败', true);
        }
      } catch (err) {
        showToast('保存异常: ' + err, true);
      }
    }

    async function triggerLogRotate() {
      try {
        const res = await fetch('/api/settings/logs/rotate', { method: 'POST' });
        const data = await res.json();
        showToast(`已完成 Copytruncate 轮转 (${data.count} 个文件)`);
        refreshSettingsData();
      } catch (err) {
        showToast('轮转失败: ' + err, true);
      }
    }

    async function triggerLogCleanup() {
      try {
        const res = await fetch('/api/settings/logs/cleanup', { method: 'POST' });
        const data = await res.json();
        showToast(`已清理 ${data.count} 个超过 ${data.retention_days} 天的归档日志`);
        refreshSettingsData();
      } catch (err) {
        showToast('清理失败: ' + err, true);
      }
    }

    // Auto poll every 2 seconds
    setInterval(fetchServices, 2000);
    fetchServices();
  </script>
</body>
</html>
    """


# --- Main Execution ---
def main():
    import argparse
    parser = argparse.ArgumentParser(description="ServiceHub - macOS Foreground Process Manager with HTTP CRUD")
    parser.add_argument("--port", type=int, default=9099, help="HTTP Server port (default: 9099)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="HTTP Server host (default: 127.0.0.1)")
    args = parser.parse_args()

    print(f"{BOLD}{COLORS[0]}")
    print("=" * 68)
    print("  🚀 ServiceHub - macOS Local Service & Process Manager")
    print(f"  📡 HTTP REST API & Web UI: http://{args.host}:{args.port}")
    print(f"  📖 Swagger OpenAPI Docs:   http://{args.host}:{args.port}/docs")
    print(f"  📁 Config Path:            {CONFIG_FILE}")
    print(f"  📜 Log Directory:          {LOGS_DIR}")
    print(f"  ⏳ Default Retention:      {log_manager.settings.log_retention_days} Days (Copytruncate Enabled)")
    print("=" * 68)
    print(f"{RESET}")

    # Load persistent configs
    manager.load_configs()

    # Setup signal handlers for graceful exit
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Start all autostart services
    loop.run_until_complete(manager.start_all())

    # Start background periodic log maintenance task (auto-rotate & prune > 3 days)
    maintenance_task = loop.create_task(background_log_maintenance_loop())

    # Configure uvicorn server
    config = uvicorn.Config(app=app, host=args.host, port=args.port, log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)

    def signal_handler():
        print(f"\n{YELLOW}[ServiceHub] Signal received. Shutting down...{RESET}")
        maintenance_task.cancel()
        loop.create_task(manager.stop_all())
        server.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(server.serve())
    except KeyboardInterrupt:
        pass
    finally:
        maintenance_task.cancel()
        loop.run_until_complete(manager.stop_all())
        loop.close()


if __name__ == "__main__":
    main()
