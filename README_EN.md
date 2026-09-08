<div align="center">

# 🚀 ServiceHub

**Local Foreground Multi-Service Manager for macOS**  
*Foreground Colored Terminal Logs · systemd-like Supervision · Full RESTful HTTP CRUD · Modern Web UI & OpenAPI Swagger*

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
4. 🖥️ **Embedded Web Dashboard & Swagger UI**: Inspect metrics, trigger restarts, and stream live logs directly via `http://127.0.0.1:9099`.
5. 💾 **Automatic State Persistence**: Configurations are stored at `~/.config/service-hub/services.json` and restored on startup.

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

## 🤖 Integration with n8n & Automated Workflows

Use n8n's **HTTP Request Node** to orchestrate local services:
- **Spin up worker before batch**: `POST http://localhost:9099/api/services/worker/start`
- **Health check**: `GET http://localhost:9099/api/services/worker`
- **Tear down after completion**: `POST http://localhost:9099/api/services/worker/stop`

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
