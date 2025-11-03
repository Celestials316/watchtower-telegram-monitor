# 🔔 Docker 容器智能通知与回滚助手 (v3.3.0)

本项目是一个高度定制的 **Watchtower 增强工具**，专用于 **实时监控 Docker 容器更新** 并提供高级通知和故障处理功能。

### 核心价值

1.  **自动回滚：** 容器更新后如果启动失败，系统将**自动尝试回滚**到旧镜像，保障服务连续性。
2.  **精准通知：** 发送 **全中文、结构化** 的 Telegram 通知，明确告知更新成功或失败。
3.  **日志告警：** 实时捕获 Watchtower 运行中的**严重错误**并发送警报。
4.  **智能解析：** 支持解析特定应用的**真实版本号**，而非仅是镜像标签。

### 🚀 快速开始

本项目使用 Docker Compose 部署。

### 📦 仓库结构速览

-   `docker/`: 包含 Dockerfile 和 docker-compose.yml 部署文件。
-   `scripts/`: 包含 monitor.sh (核心逻辑) 和 manage.sh (管理脚本)。
-   `config/`: 包含 .env.example 环境变量示例。

### 🔗 详细文档

- [安装指南](docs/INSTALL.md)
- [配置说明](docs/CONFIGURATION.md)