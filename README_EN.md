<div align="center">

# 🚀 ServiceHub

**Local Foreground Multi-Service Manager for macOS**  
*Foreground Colored Terminal Logs · systemd-like Supervision · Full RESTful HTTP CRUD · Modern Web UI & OpenAPI Swagger · AI-Friendly*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Platform macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://apple.com)

[English Documentation](README_EN.md) | [中文文档](README.md)

</div>

---

## 💡 Why ServiceHub?

When developing locally or orchestrating AI agents on macOS, engineers often need multiple persistent foreground services running concurrently (e.g. Discord/Slack agents, FastAPI servers, reverse proxies, Celery workers, n8n runners).

Existing tools fall short in specific areas:
- **PM2**: Runs primarily as a background daemon; viewing logs requires opening separate terminal tabs (`pm2 logs`). Its built-in `pm2 web` provides only read-only status JSON without dynamic RESTful service creation/deletion.
- **Supervisor (supervisord)**: Relies on legacy **XML-RPC** and static `.ini` files; dynamically registering a service requires manually rewriting files and reloading.
- **Process-Compose**: Offers great TUI interfaces, but is heavily tied to static `process-compose.yaml` files.
- **Foreman / Overmind**: Great colored terminal logs, but **completely lack HTTP management interfaces**.

**ServiceHub combines their greatest strengths into a unified, lightweight daemon:**
1. 🎨 **Foreground Multi-Colored Terminal Logs**: Multiplexes stdout/stderr of all services into the active terminal with distinct color badges.
2. 🛡️ **systemd-like Supervision & Isolation**: Crash auto-restart, graceful SIGTERM / SIGKILL timeout, and macOS process group isolation (`os.setsid` / `os.killpg`) to eliminate zombie child processes.
3. 🌐 **Full RESTful HTTP CRUD API**: Create, inspect, update, restart, and delete services on-the-fly via clean JSON endpoints.
4. 🤖 **AI-Agent Native (Tool Calling / Self-Healing)**: Standard OpenAPI JSON schema allows LLM agents to spin up services and query logs for autonomous bug-fixing and restarts.
5. 🖥️ **Embedded Web Dashboard & Swagger UI**: Inspect metrics, trigger restarts, and stream live logs directly via `http://127.0.0.1:9099`.
6. 💾 **Automatic State Persistence**: Configurations are stored at `~/.config/service-hub/services.json` and restored on startup.

---

## 🤖 AI-Agent Integration: Adding Services via LLMs

Traditional service managers force AI agents to construct intricate shell commands or edit config files, often leading to escaping bugs or hallucinations. ServiceHub exposes standard OpenAPI JSON schemas (`/openapi.json`), making service registration effortless via Function Calling / Tool Use.

### 1. Function Calling / Tool Definition
```python
import httpx

def create_service(name: str, command: str, cwd: str = None, auto_restart: bool = True):
    """Registers and starts a new local persistent service."""
    resp = httpx.post("http://127.0.0.1:9099/api/services", json={
        "name": name,
        "command": command,
        "cwd": cwd,
        "auto_restart": auto_restart,
        "autostart": True
    })
    return resp.json()
```
When a user tells their AI assistant: *"Start my crawler as a persistent local service and restart it if it crashes"*, the agent simply invokes `create_service(...)`.

### 2. Autonomous Error Recovery Loop (Self-Healing)
- **Monitoring**: AI polls `GET /api/services/{name}`.
- **Diagnostics**: If `status == "crashed"`, the AI fetches stack traces from `GET /api/services/{name}/logs`.
- **Self-Healing**: The agent patches the bug in source code and re-triggers `POST /api/services/{name}/restart`.

---

## 🍎 How to Run in the Background on macOS

While ServiceHub defaults to foreground execution for real-time terminal visibility, you can easily run it as a silent background daemon on macOS:

### Option 1: Native macOS LaunchAgent (`launchd`) [Recommended]

macOS uses `launchd` instead of systemd. Registering a LaunchAgent enables automatic startup on login and system-level crash recovery:

```bash
# 1. Register and start as LaunchAgent
./scripts/install-launchagent.sh

# 2. View background logs
tail -f ~/.config/service-hub/logs/service-hub.log

# 3. Stop background service
launchctl unload ~/Library/LaunchAgents/com.slcnx.service-hub.plist

# 4. Restart background service
launchctl load -w ~/Library/LaunchAgents/com.slcnx.service-hub.plist

# 5. Uninstall LaunchAgent
./scripts/install-launchagent.sh --uninstall
```
*Even when running silently in the background, the Web Dashboard (`http://127.0.0.1:9099`) and Swagger API remain accessible!*

---

### Option 2: Detached `tmux` Session [Best for Interactive Monitoring]

Keep it running in the background while retaining the ability to re-attach to the full-color live terminal anytime:

```bash
# 1. Start in detached tmux session
tmux new -d -s service-hub "service-hub --port 9099"

# 2. Attach to view colored live terminal logs
tmux attach -t service-hub

# 3. Detach back to background:
#    Press: Ctrl + B, then press D
```

---

### Option 3: Standard `nohup`

```bash
nohup service-hub --port 9099 > ~/.config/service-hub/logs/hub.log 2>&1 &
```

---

## 📦 Installation & Quick Start

### Option 1: Install via pip

```bash
git clone https://github.com/slcnx/service-hub.git
cd service-hub
pip install -e .
```

Then run anywhere:
```bash
service-hub
```

### Option 2: Run directly without installation

```bash
./service-hub --port 9099
```

---

## 🖥️ Web Dashboard & API Docs

Open in your browser:
- **Web Dashboard**: `http://127.0.0.1:9099` (real-time cards, CPU/memory stats, SSE log viewer, service modal)
- **Swagger OpenAPI Docs**: `http://127.0.0.1:9099/docs`

---

## 📡 RESTful HTTP CRUD API Specification

### 1. Create (Register & Start)
- **POST** `/api/services`
```json
{
  "name": "my-api",
  "command": "python -m uvicorn server:app --port 8080",
  "cwd": "/path/to/project",
  "env": {
    "ENV": "production"
  },
  "auto_restart": true,
  "autostart": true,
  "description": "Production API server"
}
```

### 2. Read (Status & Logs)
- **GET** `/api/services`: List all services with CPU%, RSS RAM, PID, and uptime.
- **GET** `/api/services/{name}`: Get single service details.
- **GET** `/api/services/{name}/logs?lines=100`: Get historical log tail.
- **GET** `/api/services/{name}/logs/stream`: **Server-Sent Events (SSE) live stream**.

### 3. Update (Lifecycle & Configuration)
- **POST** `/api/services/{name}/start`: Start service.
- **POST** `/api/services/{name}/stop`: Graceful stop with process group cleanup.
- **POST** `/api/services/{name}/restart`: Restart service.
- **PUT** `/api/services/{name}`: Update command, env, cwd, or restart policy.

### 4. Delete (Remove Service)
- **DELETE** `/api/services/{name}`: Terminate process and permanently remove from registry.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
