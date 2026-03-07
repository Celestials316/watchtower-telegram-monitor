# Docker 容器监控系统

[![Docker Pulls](https://img.shields.io/docker/pulls/w254992/watchtower-telegram-monitor)](https://hub.docker.com/r/w254992/watchtower-telegram-monitor)
[![Docker Image Size](https://img.shields.io/docker/image-size/w254992/watchtower-telegram-monitor)](https://hub.docker.com/r/w254992/watchtower-telegram-monitor)
[![GitHub Stars](https://img.shields.io/github/stars/Celestials316/watchtower-telegram-monitor?style=social)](https://github.com/Celestials316/watchtower-telegram-monitor)

自动监控 Docker 容器更新并通过 Telegram 发送中文通知，支持多服务器统一管理。

## ✨ 核心特性

- 🔔 **实时通知** - 容器更新/失败即时推送 Telegram
- 🌐 **多服务器管理** - 一个 Bot 统一管理多台服务器
- 🤖 **交互式命令** - 通过 Telegram 直接查询和管理
- 📊 **版本追踪** - 自动记录镜像版本变化历史
- 🔄 **自动回滚** - 更新失败自动恢复旧版本
- 💾 **状态持久化** - 数据库记录，重启不丢失
- 🎯 **灵活监控** - 支持全部或指定容器监控

## 📸 效果预览

### 启动通知
```
🚀 监控服务启动成功

━━━━━━━━━━━━━━━━━━━━
📊 服务信息
   版本: v3.6.0
   服务器: 京东云
   容器数: 8

🤖 命令帮助
   /status - 查看状态
   /servers - 服务器列表
   /help - 完整帮助

⏰ 启动时间: 2025-11-06 10:30:00
━━━━━━━━━━━━━━━━━━━━
```

### 多服务器管理
```
🌐 在线服务器 (3)

🖥️ 京东云 (8个容器)
   最后心跳: 刚刚

🖥️ 云服务V2 (5个容器)
   最后心跳: 30秒前

🖥️ 云服务器V4 (3个容器)
   最后心跳: 1分钟前
```

## 🚀 快速开始

### 前置要求

- Docker 20.10+
- Docker Compose v2.0+
- Telegram Bot Token 和 Chat ID（[获取方法](docs/INSTALL.md#获取-telegram-凭证)）

### 单服务器部署（5分钟）

```bash
# 1. 创建目录
mkdir -p ~/watchtower && cd ~/watchtower

# 2. 下载配置
curl -o docker-compose.yml https://raw.githubusercontent.com/Celestials316/watchtower-telegram-monitor/main/docker/docker-compose.yml

# 3. 创建环境变量
cat > .env << 'EOF'
BOT_TOKEN=你的_bot_token
CHAT_ID=你的_chat_id
SERVER_NAME=我的服务器
POLL_INTERVAL=3600
EOF

nano .env  # 修改配置

# 4. 启动服务
mkdir -p data
docker compose up -d

# 5. 查看日志
docker compose logs -f
```

启动后 10-30 秒内会收到 Telegram 通知。

### 多服务器部署

支持两种方式实现多服务器数据共享：

#### 方式一：Tailscale 虚拟局域网（推荐）

**优点：** 简单、安全、无需配置防火墙

```bash
# 1. 每台服务器安装 Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# 2. 查看分配的 IP
tailscale ip -4
# 例如: 100.64.1.10

# 3. 在主服务器上配置 NFS
sudo apt-get install -y nfs-kernel-server
sudo mkdir -p /srv/watchtower-shared
sudo chmod 777 /srv/watchtower-shared

sudo nano /etc/exports
# 添加: /srv/watchtower-shared *(rw,sync,no_subtree_check,no_root_squash)

sudo exportfs -ra
sudo systemctl restart nfs-kernel-server

# 4. 其他服务器把 notifier 的 `/data` 改为 NFS 挂载
services:
  watchtower-notifier:
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - nfs-data:/data

volumes:
  nfs-data:
    driver: local
    driver_opts:
      type: nfs
      o: addr=100.64.1.10,rw,nfsvers=4  # Tailscale IP
      device: ":/srv/watchtower-shared"

# 5. 每台服务器使用不同的 SERVER_NAME 启动
docker compose up -d
```

#### 方式二：公网 NFS

**适用场景：** 主服务器有公网 IP，其他服务器可访问

```bash
# 1. 主服务器配置 NFS（同上）
# 2. 开放防火墙端口 2049 和 111
# 3. 其他服务器挂载时使用公网 IP

services:
  watchtower-notifier:
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - nfs-data:/data

volumes:
  nfs-data:
    driver: local
    driver_opts:
      type: nfs
      o: addr=主服务器公网IP,rw,nfsvers=4,insecure
      device: ":/srv/watchtower-shared"
```

**详细步骤见** [INSTALL.md - 多服务器部署](docs/INSTALL.md#多服务器部署)

## 🎮 交互式命令

在 Telegram 中给 Bot 发送命令：

```bash
# 状态查询
/status      # 查看当前服务器状态
/servers     # 列出所有在线服务器

# 操作命令
/update      # 选择并更新容器
/restart     # 选择并重启容器
/monitor     # 打开监控管理菜单

# 其他
/help        # 完整帮助
/start       # 显示帮助并唤醒 Bot
```

## 📋 配置说明

### 环境变量

| 变量 | 说明 | 默认值 | 必填 |
|------|------|--------|------|
| `BOT_TOKEN` | Telegram Bot Token | - | ✅ |
| `CHAT_ID` | Telegram Chat ID | - | ✅ |
| `SERVER_NAME` | 服务器标识（多服务器时建议设置） | 空 | ❌ |
| `POLL_INTERVAL` | 检查间隔（秒） | 3600 | ❌ |
| `CLEANUP` | 自动清理旧镜像 | true | ❌ |
| `ENABLE_ROLLBACK` | 更新失败时自动回滚 | true | ❌ |
| `MONITORED_CONTAINERS` | 固定监控名单，逗号或空格分隔 | 空 | ❌ |
| `HEALTHCHECK_MAX_AGE` | 健康检查允许的最大心跳延迟（秒） | 120 | ❌ |

### 监控特定容器

```bash
# 方式 1: 环境变量
MONITORED_CONTAINERS=nginx,mysql,redis

# 方式 2: Telegram 交互菜单
/monitor
# 然后在 Bot 按钮里添加或移除监控
```

### 代理配置（国内必需）

```yaml
environment:
  - HTTP_PROXY=http://127.0.0.1:7890
  - HTTPS_PROXY=http://127.0.0.1:7890
```

## 🔧 管理命令

```bash
# 查看状态
docker compose ps

# 查看日志
docker compose logs -f watchtower-notifier

# 重启服务
docker compose restart

# 更新镜像
docker compose pull
docker compose up -d

# 停止服务
docker compose down
```

## 📖 文档

- [安装指南](docs/INSTALL.md) - 详细安装步骤和多服务器配置

## 🔍 工作原理

```
┌─────────────────┐
│   Watchtower    │ ← 定期检查容器更新
└────────┬────────┘
         │
         ↓
┌─────────────────┐
│  监控通知服务    │ ← 监听日志 + 处理命令
└────────┬────────┘
         │
         ├─→ 发送 Telegram 通知
         ├─→ 处理交互命令
         ├─→ 记录共享状态文件
         └─→ 多服务器心跳同步
```

## 🐛 故障排查

### 收不到通知

```bash
# 1. 检查配置
cat .env

# 2. 测试 API
curl "https://api.telegram.org/bot你的TOKEN/getMe"

# 3. 查看日志
docker logs watchtower-notifier | grep -i error

# 4. 必须先给 Bot 发送过消息
```

### 容器无法启动

```bash
# 查看错误
docker logs watchtower-notifier --tail 50

# 检查权限
ls -la /var/run/docker.sock

# 检查磁盘
df -h
```

### 多服务器数据不同步

```bash
# 测试 NFS 连接
showmount -e NFS服务器IP

# 检查挂载
docker exec watchtower-notifier ls -la /data

# 查看心跳文件
docker exec watchtower-notifier sh -c "cat /data/server_registry.json && echo && cat /data/health_status.json"
```

更多问题见 [故障排查文档](docs/INSTALL.md#故障排查)

## 🔄 更新日志

### v5.3.3 (2026-03-07)
- ✨ 健康检查改为内部心跳机制，修复 `watchtower` 与 `watchtower-notifier` 误报 `unhealthy`
- 🛡️ 修复多服务器配置与注册表并发写入问题，增强共享状态文件可靠性
- 🔄 手动更新支持失败自动回滚，并在成功后定向清理旧镜像残留
- 🎯 新增 `MONITORED_CONTAINERS` 固定监控名单与 `HEALTHCHECK_MAX_AGE` 配置项
- 🧰 重构 `scripts/manage.sh`，修正部署路径、状态文件和配置展示逻辑
- 📝 同步更新 README 与安装文档中的推荐 `yml`、命令说明和排障示例

### v3.5.0 (2025-11-06)
- ✨ 支持多服务器统一管理
- 🌐 `/servers` 命令查看所有在线服务器
- 💓 服务器心跳机制
- 🎯 交互命令支持服务器选择

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE)

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📞 支持

- 🐛 [提交 Issue](https://github.com/Celestials316/watchtower-telegram-monitor/issues)
- 💬 [讨论区](https://github.com/Celestials316/watchtower-telegram-monitor/discussions)

---

**觉得有帮助？请给个 ⭐️ Star 支持一下！**