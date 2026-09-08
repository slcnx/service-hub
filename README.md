<div align="center">

# 🚀 ServiceHub

**专为 macOS 本地开发环境设计的多服务前台管家**  
*前台终端聚合多色日志 · 类似 systemd 守护保活 · 全功能 RESTful HTTP CRUD · 现代 Web 控制台 & OpenAPI Swagger · AI 友好*

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
4. 🤖 **AI Agent 极简接入与自愈闭环**：标准 OpenAPI 规范，AI 无需敲终端命令，直接通过 Tool / Function Call 即可动态注册新服务并读取报错日志实现自愈。
5. 🖥️ **开箱即用 Web 仪表盘 & Swagger**：浏览器直连 `http://127.0.0.1:9099` 即可视化交互，同时提供 `/docs` 接口调试。
6. 💾 **配置自动持久化**：所有服务配置自动落盘于 `~/.config/service-hub/services.json`，重启管家无缝拉起。

---

## 🤖 AI 如何在项目中轻松添加与管理新服务？

ServiceHub 是**专门为 AI Agent / LLM 编排场景优化设计的**。传统工具（如 PM2 / systemd）要求 AI 生成复杂的 Shell 命令并解析非结构化的终端输出，极易发生转义错误或幻觉；而 ServiceHub 具备以下原生优势：

### 1. 结构化 Function Calling / Tool Use
ServiceHub 自带标准 OpenAPI JSON Schema (`http://127.0.0.1:9099/openapi.json`)。任何 AI Agent 框架（如 OpenAI GPT-4o、Claude 3.5 Sonnet、LangChain、AutoGen、Microsoft Agent Framework、Cursor 等）都可以直接将服务管理作为工具调用：

```python
# 给 AI Agent 定义的标准 Tool 示例 (Python)
import httpx

def create_service(name: str, command: str, cwd: str = None, auto_restart: bool = True):
    """让 AI 在本地后台注册并运行一个新的常驻服务"""
    resp = httpx.post("http://127.0.0.1:9099/api/services", json={
        "name": name,
        "command": command,
        "cwd": cwd,
        "auto_restart": auto_restart,
        "autostart": True
    })
    return resp.json()
```

用户只需对 AI 说：
> *“帮我把刚才写的爬虫脚本 `spider.py` 作为一个常驻后台服务跑起来，异常挂掉自动拉起”*

AI 会直接调用 `create_service(name="crawler", command="python spider.py", cwd="/path/to/dir")`，完成服务注册与启动。

### 2. 报错自愈闭环 (Self-Healing Feedback Loop)
- **监控状态**：AI 通过 `GET /api/services/{name}` 探测运行状态。
- **排查故障**：一旦服务变为 `crashed`，AI 直接调用 `GET /api/services/{name}/logs` 拉取最后几十行崩溃堆栈。
- **自动修复**：AI 读懂堆栈后修改代码，并调用 `POST /api/services/{name}/restart` 一键热重启，形成完全闭环的自愈能力。

---

## 🍎 macOS 下如何将前台服务转移到后台常驻？

虽然 ServiceHub 默认设计为在终端前台直观展示多色聚合日志，但当你需要它长期无感静默运行时，提供以下三种方案：

### 方案 1：macOS 原生系统级方案 —— LaunchAgent (`launchd`) 与 App Bundle 身份封装 【最推荐】

macOS 苹果官方的标准后台守护机制是 `launchd`（服务配置文件存放在 `~/Library/LaunchAgents/`），可实现开机静默常驻自启与系统级崩溃保活。

#### 🛡️ 为什么需要原生 App Bundle 身份识别？
在 macOS Ventura / Sonoma / Sequoia 中，系统引入了严格的**后台任务管理 (Background Task Management, BTM)** 与隐私权限管控（TCC）：
1. **避免显示为「bash / 身份不明」**：若后台 plist 直接使用 `/bin/bash` 或普通脚本启动，系统设置的「通用 -> 登录项与扩展 -> 允许在后台」中会显示为一个通用的可执行图标，名称为 `bash`，并附带黄色的 *“项目来自身份不明的开发者”* 警告。
2. **避免沙盒权限拦截**：若脚本位于受保护的个人目录（如 `~/Documents`），由 `launchd` 启动时极易触发 macOS TCC 权限拦截报错（`Operation not permitted`）。

#### 🚀 自动化 App 封装与一键安装
本项目内置了 `build_mac_app.py` 与自动化安装脚本：
- 自动在 `~/Applications/` 编译构建原生 `ServiceHub.app` Bundle
- 生成独立专属 AppIcon 高清图标 (`.icns`)
- 配置标准 `Info.plist`（包含 Bundle Identifier `com.slcnx.servicehub`、显示名称与版权信息）
- 编译原生 arm64/x86_64 Mach-O C 二进制启动器
- 执行本地代码签名（Ad-hoc Codesign）并在 LaunchServices 中注册
- 在 LaunchAgent plist 中关联 `<key>AssociatedBundleIdentifiers</key>`

这使得 macOS 系统设置与后台任务管理面板能够以独立应用（**ServiceHub**）的名义识别并管理后台自启项！

```bash
# 1. 一键构建 App 并安装注册后台常驻 LaunchAgent
./scripts/install-launchagent.sh

# 2. 查看后台运行日志
tail -f ~/.config/service-hub/logs/service-hub.log

# 3. 停止后台服务
launchctl unload ~/Library/LaunchAgents/com.slcnx.service-hub.plist

# 4. 重新启动后台服务
launchctl load -w ~/Library/LaunchAgents/com.slcnx.service-hub.plist

# 5. 彻底卸载 LaunchAgent
./scripts/install-launchagent.sh --uninstall
```
*在后台常驻运行期间，你仍然可以随时打开浏览器访问 `http://127.0.0.1:9099` 查看所有服务的 Web 控制台和实时流式日志！*

---

### 方案 2：开发者首选 —— `tmux` 会话后台保持 【随时切回前台看彩色日志】

如果你既想让它后台常驻，又想在需要时随时回到终端看高亮彩色的实时滚屏日志，强烈推荐使用 `tmux`：

```bash
# 1. 后台新建会话并启动 ServiceHub
tmux new -d -s service-hub "service-hub --port 9099"

# 2. 随时切入终端前台查看全彩多色实时日志
tmux attach -t service-hub

# 3. 退出前台回到后台（不中断进程运行）：
#    键盘依次按下：Ctrl + B，然后按 D (Detach)
```

---

### 方案 3：轻量命令行后台 —— `nohup`

```bash
nohup service-hub --port 9099 > ~/.config/service-hub/logs/hub.log 2>&1 &
```

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
