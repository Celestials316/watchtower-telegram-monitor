# Docker 容器监控系统

[![Docker Pulls](https://img.shields.io/docker/pulls/w254992/watchtower-telegram-monitor)](https://hub.docker.com/r/w254992/watchtower-telegram-monitor)
[![Docker Image Size](https://img.shields.io/docker/image-size/w254992/watchtower-telegram-monitor)](https://hub.docker.com/r/w254992/watchtower-telegram-monitor)
[![GitHub Stars](https://img.shields.io/github/stars/Celestials316/watchtower-telegram-monitor?style=social)](https://github.com/Celestials316/watchtower-telegram-monitor)

独立监控 Docker 容器更新并通过 Telegram 发送中文通知，默认不依赖 watchtower；检测到现有 watchtower 部署时会自动兼容旧模式，避免冲突。

## ✨ 核心特性

- 🛰️ **独立检测** - 默认由自身定期检查镜像更新，不再强依赖 watchtower
- 🔁 **自动兼容旧模式** - 检测到现有 watchtower 部署时自动切回旧版日志监控模式
- 🔔 **精准通知** - 仅在真正发现新版本、更新成功或更新失败时通知，避免重复刷屏
- 🌐 **多服务器管理** - 一个 Bot 统一管理多台服务器
- 🔐 **SSH + Tailscale 优先** - 主服务器可通过 SSH 直连远程监控容器，避免 NFS 卡死拖垮整套链路
- 🤖 **交互式命令** - 通过 Telegram 直接查询和管理
- 🔄 **自动回滚** - 更新失败自动恢复旧版本
- 💾 **状态持久化** - 本地状态文件记录检查结果、失败重试和通知去重，旧 NFS 共享模式仍可保留
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
PRIMARY_SERVER=false
ENABLE_BOT_POLLING=true
POLL_INTERVAL=1800
UPDATE_SOURCE=auto
AUTO_UPDATE=true
ENABLE_ROLLBACK=true
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

当前支持两种多服务器控制方案：

#### 方式一：SSH + Tailscale（推荐）

**适用场景：** 主服务器统一收 Telegram 命令，远程服务器各自本地保存 `/data`，通过 SSH 实时执行 `/status`、`/update`、`/restart`、`/monitor`。

```bash
# 1. 每台服务器安装 Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# 2. 记录每台服务器的 Tailscale IP
tailscale ip -4
# 例如: 100.64.1.10

# 3. 在主服务器生成 SSH 密钥，并分发到远程服务器
mkdir -p ssh
ssh-keygen -t ed25519 -f ./ssh/id_ed25519 -N ''
ssh-copy-id -i ./ssh/id_ed25519.pub root@100.64.1.20

# 4. 主服务器 .env 增加远程控制配置
cat >> .env <<'EOF'
REMOTE_CONTROL_MODE=auto
REMOTE_SERVERS_JSON=[{"name":"云服务V2","transport":"ssh","host":"100.64.1.20","user":"root","port":22,"monitor_container":"watchtower-notifier"}]
EOF

# 5. 所有服务器继续使用本地 ./data，各自启动监控容器
mkdir -p data ssh
docker compose up -d
```

`docker/docker-compose.yml` 已预留 `./ssh:/ssh:ro` 挂载，并会把 `REMOTE_CONTROL_MODE` / `REMOTE_SERVERS_JSON` 传入容器。推荐主服务器开启 `PRIMARY_SERVER=true`、远程服务器关闭 `ENABLE_BOT_POLLING`，只保留本地更新监控和 SSH RPC 能力。

#### 方式二：NFS 共享状态（兼容旧模式）

**适用场景：** 需要兼容旧的共享 `server_registry.json` / `update_state.json` / `command_queue.json` 工作流。保留该模式，但不再作为默认推荐，因为当 NFS 卡死时会直接阻塞健康检查和远程任务。

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
| `PRIMARY_SERVER` | 是否主服务器 | false | ❌ |
| `POLL_INTERVAL` | 检查间隔（秒） | 1800 | ❌ |
| `INITIAL_CHECK_DELAY` | 启动后首次检查延迟（秒） | 15 | ❌ |
| `UPDATE_SOURCE` | 更新来源模式：`auto`/`independent`/`watchtower` | auto | ❌ |
| `AUTO_UPDATE` | 发现更新后是否自动更新容器 | true | ❌ |
| `NOTIFY_ON_AVAILABLE_UPDATE` | 仅通知模式下是否发送“发现更新”通知 | true | ❌ |
| `UPDATE_RETRY_BACKOFF` | 自动更新失败后的重试退避（秒） | 1800 | ❌ |
| `CLEANUP` | 自动更新成功后清理旧镜像 | true | ❌ |
| `ENABLE_ROLLBACK` | 更新失败时自动回滚 | true | ❌ |
| `MONITORED_CONTAINERS` | 固定监控名单，逗号或空格分隔 | 空 | ❌ |
| `REMOTE_CONTROL_MODE` | 远程控制模式：`auto`/`ssh`/`queue` | auto | ❌ |
| `REMOTE_SERVERS_JSON` | 主服务器远程节点配置，JSON 数组 | `[]` | ❌ |
| `SSH_CONNECT_TIMEOUT` | SSH 建连超时（秒） | 15 | ❌ |
| `SSH_COMMAND_TIMEOUT` | SSH 远程命令超时（秒） | 300 | ❌ |
| `REMOTE_CACHE_TTL` | 远程状态缓存时间（秒） | 15 | ❌ |
| `HEALTHCHECK_MAX_AGE` | 健康检查允许的最大心跳延迟（秒） | 120 | ❌ |

### `REMOTE_SERVERS_JSON` 示例

```json
[
  {
    "name": "154VPS",
    "transport": "ssh",
    "host": "100.64.1.20",
    "user": "root",
    "port": 22,
    "monitor_container": "watchtower-notifier-154",
    "identity_file": "/ssh/id_ed25519"
  },
  {
    "name": "旧NFS节点",
    "transport": "queue"
  }
]
```

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
│  更新检测调度器   │ ← 定期拉取镜像并比较当前容器镜像 ID
└────────┬────────┘
         │
         ├─→ 本地记录 update_state / health_status
         ├─→ 远程优先通过 SSH + Tailscale 实时获取 inventory / 执行 RPC
         ├─→ 未配置 SSH 时回退到共享 server_registry / command_queue
         ├─→ 发现新版本后按策略自动更新或仅通知
         └─→ 通过 Telegram 发送去重后的状态通知
```

如宿主机已存在 `watchtower`，程序会自动切换到旧版兼容模式，继续监听 `watchtower` 日志，不会与现有部署冲突。

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

### 多服务器数据不同步 / 远程任务卡住

```bash
# 优先检查 SSH 直连
docker exec watchtower-notifier ssh -o BatchMode=yes -i /ssh/id_ed25519 root@远程TailscaleIP "docker ps --format '{{.Names}}'"

# 查看远程 inventory RPC
docker exec watchtower-notifier python3 /app/monitor.py rpc inventory

# 如果仍在使用旧 NFS 模式，再检查 NFS
showmount -e NFS服务器IP

# 检查旧模式挂载
docker exec watchtower-notifier ls -la /data

# 查看旧模式共享状态文件
docker exec watchtower-notifier sh -c "cat /data/server_registry.json && echo && cat /data/update_state.json && echo && ls -la /data/health_status.*.json"
```

更多问题见 [故障排查文档](docs/INSTALL.md#故障排查)

## 🔄 更新日志

### v5.3.3 (2026-03-07)
- 🚀 默认切换为独立更新检测模式，不再强依赖 watchtower，检测到旧部署时自动兼容旧模式
- 🔕 新增更新状态持久化与去重逻辑，解决“无更新也通知”和“每次检查都重复通知”问题
- 🛡️ 修复多服务器配置、注册表和健康文件并发/覆盖问题，按服务器拆分健康状态文件
- 🔄 自动更新支持失败回滚、失败退避重试与成功后清理旧镜像残留
- 🎯 新增 `AUTO_UPDATE`、`UPDATE_SOURCE`、`UPDATE_RETRY_BACKOFF`、`INITIAL_CHECK_DELAY` 等配置项
- 📝 同步更新 README 与安装文档中的推荐 `yml`、命令说明和排障示例

### v5.3.4 (2026-04-06)
- 🔐 新增 `SSH + Tailscale` 远程控制链路，主服务器可直连远程监控容器读取实时状态并执行更新/重启
- ♻️ 保留旧 `NFS + 共享队列` 兼容模式，可通过 `REMOTE_CONTROL_MODE=queue` 或 `REMOTE_SERVERS_JSON` 中 `transport=queue` 继续使用
- 🧰 镜像内置 `openssh-client`，Compose 默认挂载 `./ssh:/ssh:ro`
- 🧪 新增远程配置解析、SSH 分发和 RPC 子命令相关测试

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
