#!/usr/bin/env python3
"""
ServiceHub (macOS Process Manager)
- Runs in terminal foreground like systemd/foreman
- Multiplexes colorful logs from all managed services into terminal
- Exposes RESTful HTTP API (CRUD) for full dynamic control
- Features auto-restart, process-group isolation, resource tracking (CPU/MEM)
- Includes built-in Web Dashboard & Swagger UI
"""

import os
import sys
import time
import signal
import asyncio
import logging
from typing import Dict, List, Optional, Any
from collections import deque
from pathlib import Path
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
YELLOW = "[33m"
GREEN = "[32m"

CONFIG_DIR = Path.home() / ".config" / "service-hub"
CONFIG_FILE = CONFIG_DIR / "services.json"
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
        self._log_file = None

    def log_terminal(self, text: str, is_err: bool = False):
        now_str = time.strftime("%H:%M:%S")
        prefix = f"{self.color}[{now_str} {self.config.name:14s}]{RESET}"
        if is_err:
            print(f"{prefix} {RED}{text}{RESET}", flush=True)
        else:
            print(f"{prefix} {text}", flush=True)

        entry = {"time": now_str, "text": text, "is_err": is_err}
        self.log_history.append(entry)

        # Write to log file
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
        except Exception as e:
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
app = FastAPI(
    title="ServiceHub - Local Process Manager",
    description="Manage persistent background/foreground services on macOS with RESTful CRUD and live logs.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.get("/api/services/{name}/logs", summary="Get historical logs")
async def get_logs(name: str, lines: int = Query(100, ge=1, le=2000)):
    if name not in manager.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    svc = manager.services[name]
    recent = list(svc.log_history)[-lines:]
    return {"name": name, "count": len(recent), "logs": recent}


@app.get("/api/services/{name}/logs/stream", summary="Stream live logs (SSE)")
async def stream_logs(name: str):
    if name not in manager.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    svc = manager.services[name]
    queue: asyncio.Queue = asyncio.Queue()
    svc.sse_queues.append(queue)

    async def event_generator():
        # First send last 20 lines
        for item in list(svc.log_history)[-20:]:
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
        <p class="text-slate-400 text-sm mt-1">前台常驻托管 · 终端色彩并流 · 类似 systemd 守护 · 全功能 HTTP CRUD</p>
      </div>
      <div class="flex items-center gap-3">
        <a href="/docs" target="_blank" class="px-4 py-2 text-sm bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg transition border border-slate-700 flex items-center gap-2">
          <span>📖 Swagger 文档</span>
        </a>
        <button onclick="openModal()" class="px-4 py-2 text-sm bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg shadow-lg shadow-indigo-600/20 transition flex items-center gap-2">
          <span>➕ 添加新服务</span>
        </button>
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
    <div id="log-modal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
      <div class="bg-slate-800 border border-slate-700 rounded-2xl w-full max-w-4xl max-h-[85vh] flex flex-col shadow-2xl">
        <div class="px-6 py-4 border-b border-slate-700 flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="text-xl">📜</span>
            <h3 id="log-title" class="text-lg font-bold text-slate-200">实时日志</h3>
            <span id="log-status" class="text-xs px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400">Live SSE</span>
          </div>
          <button onclick="closeLogModal()" class="text-slate-400 hover:text-slate-200 text-2xl font-bold">&times;</button>
        </div>
        <div id="log-content" class="p-4 bg-slate-950 font-mono text-xs text-slate-300 overflow-y-auto flex-1 h-96 space-y-1 select-text">
        </div>
        <div class="px-6 py-3 border-t border-slate-700 flex justify-between items-center text-xs text-slate-400">
          <span>自动滚动开启</span>
          <button onclick="clearLogViewer()" class="hover:text-slate-200">清空当前窗口</button>
        </div>
      </div>
    </div>

    <!-- Add Service Modal -->
    <div id="add-modal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
      <div class="bg-slate-800 border border-slate-700 rounded-2xl w-full max-w-lg shadow-2xl p-6">
        <div class="flex justify-between items-center pb-4 border-b border-slate-700">
          <h3 class="text-lg font-bold text-slate-200">注册新服务</h3>
          <button onclick="closeModal()" class="text-slate-400 hover:text-slate-200 text-2xl font-bold">&times;</button>
        </div>
        <form onsubmit="handleCreate(event)" class="mt-4 space-y-4">
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1">服务唯一名称 (Name)</label>
            <input id="form-name" required placeholder="如: discord-agent 或 clash-proxy" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1">启动命令 (Command)</label>
            <input id="form-command" required placeholder="如: bash run_server.sh 或 python app.py" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1">运行工作目录 (CWD - 选填)</label>
            <input id="form-cwd" placeholder="默认当前执行路径" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
          </div>
          <div class="flex items-center gap-6 pt-2">
            <label class="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
              <input type="checkbox" id="form-autorestart" checked class="rounded bg-slate-900 border-slate-700 text-indigo-600">
              <span>崩溃自动重启</span>
            </label>
            <label class="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
              <input type="checkbox" id="form-autostart" checked class="rounded bg-slate-900 border-slate-700 text-indigo-600">
              <span>随管家自启动</span>
            </label>
          </div>
          <div class="flex justify-end gap-3 pt-4 border-t border-slate-700">
            <button type="button" onclick="closeModal()" class="px-4 py-2 text-sm rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300">取消</button>
            <button type="submit" class="px-4 py-2 text-sm rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium">创建并启动</button>
          </div>
        </form>
      </div>
    </div>

  </div>

  <script>
    let activeEventSource = null;

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
                <h3 class="font-bold text-slate-100 text-base truncate" title="${s.name}">${s.name}</h3>
                <span class="px-2 py-0.5 text-xs font-semibold rounded-full border ${badgeColor}">
                  ${s.status.toUpperCase()}
                </span>
              </div>
              <p class="text-xs font-mono bg-slate-900/90 text-indigo-300 p-2 rounded-lg border border-slate-800 truncate mb-3" title="${s.command}">
                $ ${s.command}
              </p>
              <div class="grid grid-cols-2 gap-2 text-xs text-slate-400 mb-4">
                <div>PID: <span class="text-slate-200 font-mono">${s.pid || '-'}</span></div>
                <div>重启次数: <span class="text-slate-200 font-mono">${s.restarts}</span></div>
                <div>CPU: <span class="text-slate-200 font-mono">${s.cpu_percent}%</span></div>
                <div>内存: <span class="text-slate-200 font-mono">${s.memory_mb} MB</span></div>
                <div class="col-span-2">运行时长: <span class="text-slate-200 font-mono">${formatUptime(s.uptime_seconds)}</span></div>
              </div>
            </div>

            <div class="pt-4 border-t border-slate-700/60 flex items-center justify-between gap-2">
              <div class="flex items-center gap-1.5">
                ${isRunning ? `
                  <button onclick="controlService('${s.name}', 'stop')" class="px-2.5 py-1.5 text-xs bg-amber-600/20 text-amber-300 hover:bg-amber-600/30 rounded-lg border border-amber-600/40">停止</button>
                  <button onclick="controlService('${s.name}', 'restart')" class="px-2.5 py-1.5 text-xs bg-indigo-600/20 text-indigo-300 hover:bg-indigo-600/30 rounded-lg border border-indigo-600/40">重启</button>
                ` : `
                  <button onclick="controlService('${s.name}', 'start')" class="px-2.5 py-1.5 text-xs bg-emerald-600/20 text-emerald-300 hover:bg-emerald-600/30 rounded-lg border border-emerald-600/40">启动</button>
                `}
                <button onclick="openLogViewer('${s.name}')" class="px-2.5 py-1.5 text-xs bg-slate-700 hover:bg-slate-600 text-slate-200 rounded-lg">日志</button>
              </div>
              <button onclick="deleteService('${s.name}')" class="text-xs text-rose-400 hover:text-rose-300 p-1.5" title="删除服务">🗑️</button>
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
        alert('操作失败: ' + err);
      }
    }

    async function deleteService(name) {
      if (!confirm(`确定要删除服务 "${name}" 吗？该操作将停止并移除它。`)) return;
      try {
        await fetch(`/api/services/${name}`, { method: 'DELETE' });
        fetchServices();
      } catch (err) {
        alert('删除失败: ' + err);
      }
    }

    async function handleCreate(e) {
      e.preventDefault();
      const payload = {
        name: document.getElementById('form-name').value.trim(),
        command: document.getElementById('form-command').value.trim(),
        cwd: document.getElementById('form-cwd').value.trim() || null,
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
          alert('创建失败: ' + (err.detail || '未知错误'));
          return;
        }
        closeModal();
        fetchServices();
      } catch (err) {
        alert('网络错误: ' + err);
      }
    }

    function openModal() { document.getElementById('add-modal').classList.remove('hidden'); }
    function closeModal() { document.getElementById('add-modal').classList.add('hidden'); }

    function openLogViewer(name) {
      document.getElementById('log-title').innerText = `实时日志 - [${name}]`;
      const content = document.getElementById('log-content');
      content.innerHTML = '<div class="text-slate-500">正在连接日志流...</div>';
      document.getElementById('log-modal').classList.remove('hidden');

      if (activeEventSource) {
        activeEventSource.close();
      }

      activeEventSource = new EventSource(`/api/services/${name}/logs/stream`);
      content.innerHTML = '';

      activeEventSource.onmessage = function(e) {
        try {
          const data = JSON.parse(e.data);
          const p = document.createElement('div');
          p.className = data.is_err ? 'text-rose-400' : 'text-slate-300';
          p.innerText = `[${data.time}] ${data.text}`;
          content.appendChild(p);
          content.scrollTop = content.scrollHeight;
        } catch (err) {}
      };

      activeEventSource.onerror = function() {
        const p = document.createElement('div');
        p.className = 'text-amber-400';
        p.innerText = '[System] 日志连接已断开或服务无新日志';
        content.appendChild(p);
      };
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
    print("=" * 65)
    print("  🚀 ServiceHub - macOS Local Service & Process Manager")
    print(f"  📡 HTTP REST API & Web UI: http://{args.host}:{args.port}")
    print(f"  📖 Swagger OpenAPI Docs:   http://{args.host}:{args.port}/docs")
    print(f"  📁 Config Path:            {CONFIG_FILE}")
    print("=" * 65)
    print(f"{RESET}")

    # Load persistent configs
    manager.load_configs()

    # Setup signal handlers for graceful exit
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Start all autostart services
    loop.run_until_complete(manager.start_all())

    # Configure uvicorn server
    config = uvicorn.Config(app=app, host=args.host, port=args.port, log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)

    def signal_handler():
        print(f"\n{YELLOW}[ServiceHub] Signal received. Shutting down...{RESET}")
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
        loop.run_until_complete(manager.stop_all())
        loop.close()


if __name__ == "__main__":
    main()
