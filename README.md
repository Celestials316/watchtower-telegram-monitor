# Docker 容器监控系统

自动监控 Docker 容器更新并通过 Telegram 发送中文通知

[![Docker Pulls](https://img.shields.io/docker/pulls/yourusername/watchtower-telegram-monitor)](https://hub.docker.com/r/yourusername/watchtower-telegram-monitor)
[![GitHub Stars](https://img.shields.io/github/stars/yourusername/watchtower-telegram-monitor)](https://github.com/yourusername/watchtower-telegram-monitor)

## 📁 项目结构

```
watchtower-telegram-monitor/
├── .github/
│   └── workflows/
│       └── docker-publish.yml          # GitHub Actions 自动构建配置
├── docker/
│   ├── Dockerfile                      # 主 Dockerfile
│   └── docker-compose.yml              # Docker Compose 配置模板
├── scripts/
│   ├── monitor.sh                      # 监控脚本
│   └── manage.sh                       # 管理脚本
├── config/
│   └── .env.example                    # 环境变量示例
├── docs/
│   ├── INSTALL.md                      # 安装文档
│   └── CONFIGURATION.md                # 配置说明
├── .gitignore
├── README.md
└── LICENSE


## 📋 环境变量

| 变量名 | 说明 | 默认值 | 必填 |
|--------|------|--------|------|
| `BOT_TOKEN` | Telegram Bot Token | - | ✅ |
| `CHAT_ID` | Telegram Chat ID | - | ✅ |
| `SERVER_NAME` | 服务器标识名称 | - | ❌ |
| `POLL_INTERVAL` | 检查间隔(秒) | 3600 | ❌ |
| `CLEANUP` | 自动清理旧镜像 | true | ❌ |
| `ENABLE_ROLLBACK` | 启用自动回滚 | true | ❌ |
| `MONITORED_CONTAINERS` | 监控容器列表(空=全部) | - | ❌ |





## 📖 详细文档

- [安装指南](docs/INSTALL.md)
- [配置说明](docs/CONFIGURATION.md)

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License
```
