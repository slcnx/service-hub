<div align="center">

# 🚀 ServiceHub

**专为 macOS 本地开发环境与云端设计的常驻服务管家**  
*前台终端多色聚合日志 · 原生 macOS LaunchAgent 身份封装 · Copytruncate 日志轮转 (默认保留3天) · 全功能 RESTful HTTP CRUD · 现代 Web 仪表盘 & Swagger · AI 友好 · 支持 DockerHub*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Platform macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://apple.com)
[![Docker slcnx/service-hub](https://img.shields.io/badge/docker-slcnx%2Fservice--hub-blue.svg)](https://hub.docker.com/r/slcnx/service-hub)

[English Documentation](README_EN.md) | [中文文档](README.md)

</div>

---

## 💡 为什么需要 ServiceHub？

在 macOS 本地做日常开发、微服务调试以及 AI Agent 智能体编排时，开发者往往需要常驻多个前台服务（如 Discord/Slack Agent、FastAPI 接口服务、本地代理、Celery Worker、n8n runner、后台爬虫等）。

传统方案痛点：
- **PM2**：默认纯后台运行，查看日志需反复敲 `pm2 logs`；自带的 `pm2 web` 仅提供只读监控 JSON，**无法通过 HTTP RESTful 动态注册、更新或注销服务**。
- **Supervisor (supervisord)**：基于老旧的 **XML-RPC 协议**，新增服务必须手动修改 `.ini` 配置文件再 reload，极度不便。
- **Process-Compose**：终端 TUI 体验优秀，但依赖静态 `process-compose.yaml` 文件，无法在运行时随心所欲通过 HTTP 接口注册新进程。
- **Foreman / Overmind**：多色终端日志体验极佳，但**完全没有 HTTP 接口**，无法通过工作流（如 n8n、自动化脚本、Webhook）联动。

**ServiceHub 将它们各自的优点合而为一：**
1. 🎨 **终端多色并流日志**：在当前终端前台聚合展示所有服务日志，独立分配色彩标签，直观清晰。
2. 🛡️ **类 systemd 守护与隔离**：支持 `auto_restart` 崩溃自动拉起，使用 macOS 原生进程组（`os.setsid` / `os.killpg`），退出与停止彻底干净，不留孤儿进程。
3. 📜 **Copytruncate 日志轮转 & 默认保留 3 天**：专为长期监控设计，`tail -f` 绝不断流；后台定时清理超期日志，Web 仪表盘默认自动滚动。
4. 🌐 **全功能 RESTful HTTP CRUD**：提供现代化的标准 JSON REST API，随时随地增删改查控制任意服务。
5. 🤖 **AI Agent 极简接入与自愈闭环**：标准 OpenAPI 规范，AI 无需敲终端命令，直接通过 Tool / Function Call 即可动态注册新服务并读取报错日志自愈。
6. 🖥️ **开箱即用 Web 仪表盘 & Swagger**：浏览器直连 `http://127.0.0.1:9099` 即可视化交互，同时提供 `/docs` 接口调试。
7. 🍎 **macOS 原生 App 身份识别**：封装 `ServiceHub.app` Bundle，在系统设置“后台与登录项”中显示明确身份，告别“bash / 身份不明”警告与沙盒权限拦截。
8. 🐳 **多端支持 (macOS 原生 / DockerHub 镜像)**：既可本地原生极速运行，亦可使用 `slcnx/service-hub:latest` 一键容器化托管。

---

## 📜 日志轮转机制与 3 天默认保留 (Log Retention)

在长时间运行的服务管理中，无节制增长的日志文件是磁盘爆满和内存泄漏的主要元凶。ServiceHub 专为生产级稳定设计了**全自动日志生命周期管理**：

### 1. 原生 Copytruncate 轮转：`tail -f` 永不断流
在 macOS / Linux 终端中，开发者常通过以下命令长期观察管家运行状态：
```bash
tail -f ~/.config/service-hub/logs/service-hub.log
```
传统日志轮转工具（如直接 `mv` 重命名）会导致正在运行的 `tail -f` 丢失文件句柄（Inode 转移），输出彻底停止。  
**ServiceHub 采用 Copytruncate 原理**：
1. 先将当前日志内容拷贝至带时间戳的历史归档文件（如 `service-hub.log.20260908_153000`）；
2. 随后在保持原有文件句柄和 Inode 不变的前提下，对当前文件执行原位截断（Truncate to 0 bytes）；
3. **效果**：终端中正在运行的 `tail -f` 能够瞬时感知到文件截断并重置偏移，**日志输出连续流动，绝不断线、绝不挂死！**

### 2. 默认只保留 3 天日志 (`retention_days = 3`)
- **自动清理**：ServiceHub 后台定时任务（每 30 分钟一次）自动扫描 `~/.config/service-hub/logs` 目录；
- **精准识别**：仅清理修改时间超过 3 天的历史归档文件（`*.log.*`），**当前的活跃日志文件绝不误删**；
- **配置与手动触发**：用户可在 Web 界面或通过 REST API 自定义保留天数，亦可一键手动触发清理。

### 3. 现代 Web 仪表盘：默认自动滚动 (Auto-scroll)
- 网页版日志查看器默认勾选 **“默认自动滚动 (Auto-scroll)”**，新日志流入时自动滚至最新输出；
- 点击右上角 **“📋 主管家日志”** 即可一键查看 `service-hub.log` 的 live SSE 实时数据流；
- 点击 **“⚙️ 日志与设置”** 可实时查看当前日志目录占用空间、各文件属性，并支持一键轮转与清理。

---

## 🐳 DockerHub 启动托管与宿主机目录识别深度解析

> **高频问题**：ServiceHub 可以通过 DockerHub 镜像启动托管，但容器启动后，宿主机目录不就不方便识别了吗？

这是一个非常关键且具有深度的工程架构问题！**答案是：确实如此。因此 ServiceHub 针对不同环境提供了清晰的选型指南：**

### 1. 为什么在 macOS 本地，强烈推荐 原生 LaunchAgent (`ServiceHub.app`)？
在 macOS 本地做开发时，如果将 ServiceHub 放进 Docker 容器运行，会面临以下根本性阻隔：
1. **跨架构与二进制壁垒**：macOS 本地环境是 Darwin 内核（Apple Silicon arm64 Mach-O 格式）。你在本地配置的 Python（如 `/opt/homebrew/...` 或 Conda 虚拟环境）、Homebrew CLI 工具、Node/Bun 脚本，**全都是 macOS 原生 Mach-O 二进制**。Docker 容器内部是 Linux 内核环境，即便通过 `-v` 把宿主机目录挂载进容器，Linux 容器**也根本无法运行 macOS Mach-O 二进制程序**！
2. **路径繁琐转换**：在原生模式下，ServiceHub 直接以当前用户身份执行，宿主机路径（`/Users/songliangcheng/...`）即写即用；容器模式下，每个新服务都必须配置映射后的容器内路径。
3. **系统权限与 BTM 身份**：Docker 容器无法与 macOS 苹果原生 Background Task Management 深度集成，无法在系统设置“登录项”中以原生 App 呈现。

**结论**：在 **macOS 个人电脑本地** 管理本地服务，**原生 LaunchAgent 是极致最佳体验** —— 零虚拟化损耗、原生识别所有宿主机路径、无缝调用本地所有开发环境与 Conda/Venv。

---

### 2. DockerHub 托管镜像（`slcnx/service-hub`）适用于什么场景？
DockerHub 镜像为以下场景而生：
- **Linux 云服务器 / VPS / NAS / Kubernetes**：在纯 Linux 生产环境下统一托管一组微服务、后台爬虫、任务进程；
- **自包含容器化工作流**：将服务及其运行环境直接封在同一个 Docker 容器或通过 `docker-compose` 编排；
- **隔离沙盒环境**：防止恶意代码或待测脚本直接操作宿主机敏感系统文件。

#### 🐳 Docker 快速启动命令
```bash
docker run -d \
  --name service-hub \
  --restart unless-stopped \
  -p 9099:9099 \
  -v ~/.config/service-hub:/root/.config/service-hub \
  -v /your/host/workspace:/app/workspace \
  slcnx/service-hub:latest
```

#### 📦 docker-compose.yml 示例
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
      # 持久化配置文件与 3 天轮转日志
      - ~/.config/service-hub:/root/.config/service-hub
      # 挂载你的代码或脚本目录
      - ./data:/app/data
    environment:
      - TZ=Asia/Shanghai
```

> **提示**：在 Docker 模式下注册服务时，命令中的可执行环境使用容器内置的环境（如容器内的 Python 3.11），工作目录配置为挂载点（如 `/app/data`）。

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

### 2. 报错自愈闭环 (Self-Healing Feedback Loop)
当 AI Agent 创建的服务因为端口冲突、依赖缺失或代码 Bug 崩溃时：
1. ServiceHub 自动记录崩溃状态（`status: "crashed"`）；
2. AI 调用 `GET /api/services/{name}/logs` 直接获取最后的错误堆栈；
3. AI 自行诊断原因（例如修改配置、重新安装依赖或更新启动命令），然后调用 `PUT /api/services/{name}` 完成热修复并自动重启。全流程无需人工介入！

---

## 🍎 macOS 下如何将前台服务转移到后台常驻？

虽然 ServiceHub 默认设计为在终端前台直观展示多色聚合日志，但当你需要它长期无感静默运行时，提供以下方案：

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
- 编译原生 arm64/x86_64 Mach-O C 二进制启动器并执行本地签名
- 注册 LaunchAgent 关联 Bundle Identifier，在 macOS 系统设置中正规显示 **ServiceHub**！

**一键安装：**
```bash
git clone https://github.com/slcnx/service-hub.git
cd service-hub
bash scripts/install-launchagent.sh
```

**管理与常用命令：**
```bash
# 查看实时日志 (由于 copytruncate 机制，长期运行绝不断线)
tail -f ~/.config/service-hub/logs/service-hub.log

# 启动 / 重启 LaunchAgent
launchctl kickstart -k gui/$(id -u)/com.slcnx.service-hub

# 卸载退出
bash scripts/install-launchagent.sh --uninstall
```

---

## 📡 HTTP RESTful CRUD API 规范

所有接口均返回标准 JSON，方便集成至自动化脚本、n8n 或前端应用。

### 1. 服务管理 (CRUD)
- **POST** `/api/services`：注册并启动新服务
- **GET** `/api/services`：获取所有服务状态与 CPU/内存占用
- **GET** `/api/services/{name}`：获取指定服务详情
- **PUT** `/api/services/{name}`：热更新服务配置（命令、目录、环境变量、重启策略等）
- **DELETE** `/api/services/{name}`：停止进程并从托管列表中永久删除
- **POST** `/api/services/{name}/start`：启动服务
- **POST** `/api/services/{name}/stop`：优雅停止
- **POST** `/api/services/{name}/restart`：重启服务
- **GET** `/api/services/{name}/logs`：获取历史日志
- **GET** `/api/services/{name}/logs/stream`：**SSE 实时日志流**

### 2. 日志与全局设置 (Settings & Logs)
- **GET** `/api/settings`：获取当前日志保留天数 (默认 3 天)、自动滚动状态及磁盘容量
- **PUT** `/api/settings`：更新配置 (`log_retention_days`, `max_log_file_size_mb`, `log_auto_scroll` 等)
- **POST** `/api/settings/logs/rotate`：手动触发对所有日志的原生 Copytruncate 轮转
- **POST** `/api/settings/logs/cleanup`：手动触发清理超过保留期限的历史日志
- **GET** `/api/logs/hub`：读取管家守护进程历史日志 (`service-hub.log`)
- **GET** `/api/logs/hub/stream`：**SSE 实时流式读取管家守护进程日志**

---

## 📊 方案横向对比

| 功能特性 | ServiceHub | PM2 | Supervisord | Process-Compose | Docker 容器 |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **宿主机路径零转换** | ✅ 深度原生识别 | ✅ 支持 | ✅ 支持 | ✅ 支持 | ❌ 需逐个 volume 挂载 |
| **执行 Mac 本地编译环境** | ✅ 原生 Mach-O | ✅ 原生 | ✅ 原生 | ✅ 原生 | ❌ 跨 Linux 内核无法执行 |
| **前台终端色彩日志** | ✅ 原生直显 | ❌ 需单独 logs | ❌ 需自行 tail | ✅ TUI 窗口 | ⚠️ docker logs |
| **Copytruncate 3天轮转** | ✅ 内置 + tail-f保活 | ⚠️ 需装插件 | ⚠️ 需配置 | ❌ 无 | ⚠️ 依赖宿主机 logrotate |
| **macOS BTM 签名身份** | ✅ 原生 App 标识 | ❌ 识别为 node | ❌ 识别为 python | ❌ 识别为 binary | ❌ Docker 图标 |
| **全功能 RESTful CRUD** | ✅ 完整 JSON CRUD | ❌ 仅只读 JSON | ❌ 老旧 XML-RPC | ⚠️ 依赖 YAML | ⚠️ 仅容器级 API |
| **开箱即用 Web 仪表盘** | ✅ 内置 + Swagger | ⚠️ 需商业版插件 | ⚠️ 极简 90s 界面 | ❌ 仅终端 TUI | ❌ 需额外容器 |

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源。
