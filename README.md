<div align="center">

# 🚀 ServiceHub

**专为 macOS 本地开发环境设计的多服务前台管家**  
*前台终端聚合多色日志 · 类似 systemd 守护保活 · 全功能 RESTful HTTP CRUD · 现代 Web 控制台 & OpenAPI Swagger*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Platform macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://apple.com)

[English Documentation](README_EN.md) | [中文文档](README.md)

</div>

---

## 💡 为什么需要 ServiceHub？

在 macOS 本地做开发与 AI Agent 编排时，常常需要常驻多个前台服务（如 Discord/Slack Agent、FastAPI 接口服务、Proxy 代理、Celery Worker、n8n runner 等）。

现存工具的痛点：
- **PM2**：默认以后台守护进程运行，终端看日志需额外开窗口 `pm2 logs`；且自带的 `pm2 web` 仅提供只读监控 JSON，**无法通过 HTTP RESTful 动态注册、更新或注销服务**。
- **Supervisor (supervisord)**：基于古老的 **XML-RPC 协议**，动态增删服务必须修改 `.ini` 配置文件再 reload，极度不便。
- **Process-Compose**：终端 TUI 体验优秀，但严重依赖静态 `process-compose.yaml` 文件，不支持运行时随心所欲通过 HTTP 接口注册新进程。
- **Foreman / Overmind**：多色终端日志体验极佳，但**完全没有 HTTP 接口**，无法通过工作流（如 n8n、脚本、Webhook）联动。

**ServiceHub 将它们各自的优点合而为一：**
1. 🎨 **终端多色并流日志**：在当前终端前台聚合展示所有服务日志，独立分配色彩标签，直观清晰。
2. 🛡️ **类 systemd 守护与隔离**：支持 `auto_restart` 崩溃自动拉起，使用 macOS 原生进程组（Process Group, `os.setsid` / `os.killpg`），确保退出与停止时干净彻底，不留孤儿进程。
3. 🌐 **全功能 RESTful HTTP CRUD**：提供现代化的标准 JSON REST API，增删改查随时随地控制任意服务。
4. 🖥️ **开箱即用 Web 仪表盘 & Swagger**：浏览器直连 `http://127.0.0.1:9099` 即可视化交互，同时提供 `/docs` 接口调试。
5. 💾 **配置自动持久化**：所有服务配置自动落盘于 `~/.config/service-hub/services.json`，重启管家无缝拉起。

---

## 📦 安装与快速开始

### 方式 1：通过 pip 直接安装

```bash
git clone https://github.com/slcnx/service-hub.git
cd service-hub
pip install -e .
```

安装后在终端任意位置运行：
```bash
service-hub
```

### 方式 2：单文件免安装运行

克隆后直接使用内置启动脚本：
```bash
./service-hub --port 9099
```

启动后终端将展示前台服务看板：
```text
=================================================================
  🚀 ServiceHub - macOS Local Service & Process Manager
  📡 HTTP REST API & Web UI: http://127.0.0.1:9099
  📖 Swagger OpenAPI Docs:   http://127.0.0.1:9099/docs
  📁 Config Path:            ~/.config/service-hub/services.json
=================================================================
[15:05:53 demo-worker   ] Worker pulse 0
[15:05:54 discord-agent ] INFO: Uvicorn running on http://127.0.0.1:8088
```

---

## 🖥️ 可视化 Web 控制台

浏览器访问：
- **Web 控制台仪表盘**：`http://127.0.0.1:9099`
  - 实时卡片展示各服务状态、PID、CPU 使用率、内存 RSS、运行时间
  - 一键启动、停止、重启、删除
  - 点击“日志”打开内置终端，基于 **SSE (Server-Sent Events)** 实时流式滚屏
  - “添加新服务”快捷弹窗
- **Swagger 接口调试**：`http://127.0.0.1:9099/docs`

---

## 📡 HTTP RESTful CRUD API 规范

所有接口均返回标准 JSON，方便集成至自动化脚本、n8n 或前端应用。

### 1. Create（注册并启动新服务）
- **POST** `/api/services`
- **Payload**:
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
  "description": "生产环境 API 服务"
}
```

### 2. Read（查询服务与日志）
- **GET** `/api/services`：获取所有服务状态与 CPU/内存占用
- **GET** `/api/services/{name}`：获取指定服务详情
- **GET** `/api/services/{name}/logs?lines=100`：获取最近指定行数的历史日志
- **GET** `/api/services/{name}/logs/stream`：**SSE 实时日志流**

### 3. Update（生命周期与热更新）
- **POST** `/api/services/{name}/start`：启动服务
- **POST** `/api/services/{name}/stop`：优雅停止（SIGTERM，超时 SIGKILL 进程组）
- **POST** `/api/services/{name}/restart`：重启服务
- **PUT** `/api/services/{name}`：热更新服务配置（命令、目录、环境变量、重启策略等）

### 4. Delete（注销并停止服务）
- **DELETE** `/api/services/{name}`：停止进程并从托管列表中永久删除

---

## 🤖 联动 n8n / 自动化工作流

在 n8n 中通过 **HTTP Request Node** 即可零门槛控制本地服务：
- **批处理前自动拉起**：`POST http://localhost:9099/api/services/worker/start`
- **运行后健康检查**：`GET http://localhost:9099/api/services/worker` 校验 `status === "running"`
- **任务结束后释放资源**：`POST http://localhost:9099/api/services/worker/stop`

---

## 📊 方案横向对比

| 功能特性 | ServiceHub | PM2 | Supervisord | Process-Compose | Overmind |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **前台终端色彩日志** | ✅ 原生直显 | ❌ 需单独窗口 logs | ❌ 需自行 tail | ✅ TUI 窗口 | ✅ 极佳 |
| **现代 RESTful CRUD** | ✅ 完整 JSON CRUD | ❌ 仅只读 JSON | ❌ 老旧 XML-RPC | ⚠️ 依赖 YAML | ❌ 无 |
| **动态新增服务** | ✅ HTTP POST 任意命令 | ⚠️ 需自写 SDK 封装 | ❌ 需重写 ini 文件 | ❌ 需改 YAML | ❌ 需改 Procfile |
| **macOS 进程组隔离** | ✅ killpg 彻底清理 | ✅ 支持 | ✅ 支持 | ✅ 支持 | ✅ 支持 |
| **开箱即用 Web 仪表盘** | ✅ 内置 + Swagger | ⚠️ 需商业版或插件 | ⚠️ 极简 90s 界面 | ❌ 仅终端 TUI | ❌ 无 |

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源。
