<div align="center">

# 🚀 ServiceHub

**Local Foreground & Background Persistent Service Manager for macOS and Linux**  
*Foreground Colored Terminal Logs · Native macOS LaunchAgent Identity · Copytruncate Log Rotation (3-Day Retention) · Full RESTful HTTP CRUD · Modern Web UI with Auto-Scroll · Swagger API · AI-Friendly · DockerHub Support*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Platform macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://apple.com)
[![Docker slcnx/service-hub](https://img.shields.io/badge/docker-slcnx%2Fservice--hub-blue.svg)](https://hub.docker.com/r/slcnx/service-hub)

[English Documentation](README_EN.md) | [中文文档](README.md)

</div>

---

## 💡 Why ServiceHub?

When developing locally or orchestrating AI agents on macOS, engineers often need multiple persistent foreground services running concurrently (e.g. Discord/Slack agents, FastAPI servers, reverse proxies, Celery workers, n8n runners, scrapers).

Existing tools fall short in specific areas:
- **PM2**: Runs primarily as a background daemon; viewing logs requires opening separate terminal tabs (`pm2 logs`). Its built-in `pm2 web` provides only read-only status JSON without dynamic RESTful service creation/deletion.
- **Supervisor (supervisord)**: Relies on legacy **XML-RPC** and static `.ini` files; dynamically registering a service requires manually rewriting files and reloading.
- **Process-Compose**: Offers great TUI interfaces, but is heavily tied to static `process-compose.yaml` files and lacks dynamic HTTP runtime manipulation.
- **Foreman / Overmind**: Great colored terminal logs, but **completely lack HTTP management interfaces**.

**ServiceHub combines their greatest strengths into a unified, lightweight supervisor:**
1. 🎨 **Foreground Multi-Colored Terminal Logs**: Multiplexes stdout/stderr of all services into the active terminal with distinct color badges.
2. 🛡️ **systemd-like Supervision & Isolation**: Crash auto-restart, graceful SIGTERM / SIGKILL timeout, and macOS process group isolation (`os.setsid` / `os.killpg`) to eliminate zombie child processes.
3. 📜 **Copytruncate Log Rotation & Default 3-Day Retention**: Designed for 24/7 reliability; `tail -f` never stops streaming; automated cleanup prunes historical logs older than 3 days.
4. 🌐 **Full RESTful HTTP CRUD API**: Create, inspect, update, restart, and delete services on-the-fly via clean JSON endpoints.
5. 🤖 **AI-Agent Native (Tool Calling / Self-Healing)**: Standard OpenAPI JSON schema allows LLM agents to spin up services and query logs for autonomous bug-fixing and restarts.
6. 🖥️ **Embedded Web Dashboard & Swagger UI**: Responsive UI with default auto-scroll, log maintenance panel, and Swagger API docs at `http://127.0.0.1:9099`.
7. 🍎 **macOS Native App Identity**: Packaged as `ServiceHub.app` Bundle to show verified identity in macOS System Settings -> Login Items without "unidentified developer" warnings.
8. 🐳 **Multi-Deployment Modes (Native macOS / DockerHub)**: Run natively with direct host filesystem access or run containerized via `slcnx/service-hub:latest`.

---

## 📜 Log Lifecycle: Copytruncate Rotation & 3-Day Retention

In long-running daemon environments, uncontrolled log growth exhausts disk space. ServiceHub provides built-in enterprise-grade log lifecycle management:

### 1. Zero-Disruption Copytruncate Rotation (`tail -f` Never Breaks)
When running in macOS/Linux terminal:
```bash
tail -f /Users/songliangcheng/.config/service-hub/logs/service-hub.log
```
Traditional log rotation moves the file, breaking `tail -f` descriptors. ServiceHub uses **Copytruncate**:
1. Copies active log contents to a timestamped archive (`service-hub.log.YYYYMMDD_HHMMSS`).
2. Truncates the active log file in-place to 0 bytes without closing file handles.
3. Existing terminal `tail -f` watchers and daemon writers continue smoothly without missing a beat!

### 2. Automatic Pruning: Retains Only Recent 3 Days
- **Smart Cleanup**: Only archives older than 3 days (`*.log.*`) are cleaned. **Active files are never touched**.
- **Configurable**: Retention days and maximum file size thresholds can be adjusted via Web UI or REST API (`PUT /api/settings`).

### 3. Modern Web Dashboard: Default Auto-Scroll
- Live SSE stream reader defaults to **Auto-Scroll** enabled.
- Click **"📋 Hub Log"** to inspect daemon logs in real time.
- Click **"⚙️ Logs & Settings"** to check disk usage, file statistics, or trigger immediate rotation/cleanup.

---

## 🐳 DockerHub Deployment & Host Directory Tradeoffs

> **Key Architectural Question**: ServiceHub supports DockerHub containerization, but when started inside a container, wouldn't host directories be difficult or inconvenient to recognize?

**Yes, absolutely. This is why ServiceHub clearly defines deployment boundaries:**

### 1. Why Native LaunchAgent (`ServiceHub.app`) is Superior on macOS
For local macOS development, containerizing ServiceHub introduces severe friction:
1. **Binary & Architecture Mismatch**: macOS runs Darwin Mach-O binaries (Apple Silicon arm64). Local Conda environments, Homebrew utilities, Node/Bun tools are Mach-O binaries. A Docker container runs a Linux ELF userland; **even if host directories are mounted, the Linux container cannot execute macOS Mach-O binaries**!
2. **Path Mapping Friction**: On macOS native, paths are `/Users/songliangcheng/...`. In Docker, host paths must be translated into `/app/workspace`, complicating AI prompt engineering and script execution.
3. **OS Privileges & Background Task Management**: Docker cannot register with macOS Apple Background Task Management (BTM).

**Conclusion**: For **local macOS development**, the native LaunchAgent (`ServiceHub.app`) provides the ultimate experience: 0 virtualization overhead, direct host path recognition, and full access to local Conda/Python environments.

---

### 2. Where DockerHub (`slcnx/service-hub`) Shines
The DockerHub image is designed for:
- **Linux Cloud Servers / VPS / NAS / Kubernetes**: Supervising micro-processes, background workers, or scrapers in pure Linux environments.
- **Hermetic Containerized Pipelines**: Packing services and dependencies inside the same container.
- **Isolated Sandboxes**: Preventing untrusted scripts from touching sensitive host files.

#### 🐳 Docker Run
```bash
docker run -d \
  --name service-hub \
  --restart unless-stopped \
  -p 9099:9099 \
  -v ~/.config/service-hub:/root/.config/service-hub \
  -v /your/host/workspace:/app/workspace \
  slcnx/service-hub:latest
```

#### 🐳 Docker Compose
```yaml
version: '3.8'

services:
  service-hub:
    image: slcnx/service-hub:latest
    container_name: service-hub
    restart: unless-stopped
    ports:
      - "9099:9099"
    volumes:
      - ~/.config/service-hub:/root/.config/service-hub
      - ./data:/app/data
    environment:
      - TZ=Asia/Shanghai
```

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

### Option 1: Native macOS LaunchAgent (`launchd`) with App Bundle Identity [Recommended]

macOS uses `launchd` (managed via `~/Library/LaunchAgents/`) as its native service supervision daemon for automatic startup upon login and crash recovery.

#### 🛡️ Why Native App Bundle Identity Matters
macOS Ventura, Sonoma, and Sequoia introduced strict **Background Task Management (BTM)**:
1. **Avoid Generic "bash / Unidentified Developer"**: Launching unbundled scripts triggers *"Item from unidentified developer"*.
2. **Prevent TCC Permission Failures**: Background launchd processes executing scripts inside restricted folders (such as `~/Documents`) trigger `Operation not permitted` errors.

#### 🚀 Automated Bundle Packaging & One-Click Setup
ServiceHub provides an automated pipeline via `build_mac_app.py` and `scripts/install-launchagent.sh`:
- Automatically packages `~/Applications/ServiceHub.app`
- Generates dedicated retina AppIcon (`.icns`)
- Embeds standard `Info.plist` with `CFBundleIdentifier = com.slcnx.servicehub`
- Compiles a native Mach-O C launcher binary
- Connects `AssociatedBundleIdentifiers` in the LaunchAgent plist

```bash
# 1. Automatically build app and install LaunchAgent
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

---

### Option 2: Detached `tmux` Session [Best for Interactive Monitoring]

```bash
# 1. Start in detached tmux session
tmux new -d -s service-hub "service-hub --port 9099"

# 2. Attach to view colored live terminal logs
tmux attach -t service-hub

# 3. Detach: Ctrl + B, then press D
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

### 5. Settings & Log Management
- **GET** `/api/settings`: Retrieve settings and log storage statistics.
- **PUT** `/api/settings`: Update settings (`log_retention_days`, `max_log_file_size_mb`, `log_auto_scroll`, etc.).
- **POST** `/api/settings/logs/rotate`: Manually trigger Copytruncate log rotation.
- **POST** `/api/settings/logs/cleanup`: Manually trigger pruning of expired archived logs (> retention days).
- **GET** `/api/logs/hub`: Tail main supervisor daemon log (`service-hub.log`).
- **GET** `/api/logs/hub/stream`: **SSE live stream of main supervisor daemon log**.

---

## 📊 Feature Comparison Matrix

| Feature | ServiceHub | PM2 | Supervisord | Process-Compose | Docker Containers |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Direct Host Paths** | ✅ Native Zero-Conversion | ✅ Native | ✅ Native | ✅ Native | ❌ Complex volume mapping |
| **macOS Native Binaries** | ✅ Native Mach-O execution | ✅ Supported | ✅ Supported | ✅ Supported | ❌ Cannot run macOS Mach-O |
| **Foreground Terminal Logs**| ✅ Multi-color Multiplex | ❌ Separate `pm2 logs` | ❌ Manual tail | ✅ TUI Split | ⚠️ docker logs |
| **Copytruncate 3-Day Retain**| ✅ Built-in + tail -f safe | ⚠️ Extra plugin | ⚠️ Needs logrotate | ❌ None | ⚠️ Host logrotate needed |
| **macOS BTM Verified Identity**| ✅ Native App Bundle | ❌ Shown as node | ❌ Shown as python | ❌ Shown as binary | ❌ Docker icon |
| **Full RESTful JSON CRUD** | ✅ Standard JSON CRUD | ❌ Read-only JSON | ❌ Legacy XML-RPC | ⚠️ Static YAML | ⚠️ Container-only API |
| **Out-of-the-Box Web UI** | ✅ Integrated + Swagger | ⚠️ Paid/Separate | ⚠️ 90s Web UI | ❌ Terminal only | ❌ Extra container needed |

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
