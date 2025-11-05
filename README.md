# Docker 容器监控系统

[![Docker Pulls](https://img.shields.io/docker/pulls/w254992/watchtower-telegram-monitor)](https://hub.docker.com/r/w254992/watchtower-telegram-monitor)
[![Docker Image Size](https://img.shields.io/docker/image-size/w254992/watchtower-telegram-monitor)](https://hub.docker.com/r/w254992/watchtower-telegram-monitor)
[![GitHub Stars](https://img.shields.io/github/stars/w254992/watchtower-telegram-monitor?style=social)](https://github.com/w254992/watchtower-telegram-monitor)

自动监控 Docker 容器更新并通过 Telegram 发送**中文通知**，支持版本追踪、自动回滚、状态持久化。

## ✨ 特性

- 🔔 **实时 Telegram 通知** - 容器更新成功/失败即时推送
- 📊 **版本追踪** - 记录容器镜像版本变化历史
- 🔄 **自动回滚** - 更新失败时自动恢复旧版本
- 💾 **状态持久化** - 数据库记录容器状态，重启不丢失
- 🎯 **灵活监控** - 支持监控所有容器或指定容器
- 🌐 **中文界面** - 通知消息完全中文化
- 🏷️ **服务器标识** - 多服务器环境下区分通知来源
- 📝 **详细日志** - 实时显示处理过程，方便调试

## 📸 效果预览

### 启动通知
```
🚀 监控服务启动成功

━━━━━━━━━━━━━━━━━━━━
📊 服务信息
   版本: v3.3.0

🎯 监控状态
   容器数: 4
   状态库: 已初始化

监控容器列表:
   • nginx
   • mysql
   • redis
   • app

🔄 功能配置
   自动回滚: true
   检查间隔: 60分钟

⏰ 启动时间
   2024-11-04 10:30:00
━━━━━━━━━━━━━━━━━━━━

✅ 服务正常运行中
```

### 更新成功通知
```
✨ 容器更新成功

━━━━━━━━━━━━━━━━━━━━
📦 容器名称
   nginx

🎯 镜像信息
   nginx

🔄 版本变更
   1.25.3 (a1b2c3d4e5f6)
   ➜
   1.25.4 (f6e5d4c3b2a1)

⏰ 更新时间
   2024-11-04 11:15:23
━━━━━━━━━━━━━━━━━━━━

✅ 容器已成功启动并运行正常
```

## 🚀 快速开始

### 前置要求

- Docker 20.10+
- Docker Compose v2.0+
- Telegram Bot Token 和 Chat ID

### 5 分钟快速部署

#### 1. 获取 Telegram 凭证

**Bot Token:**
1. 在 Telegram 搜索 `@BotFather`
2. 发送 `/newbot` 创建机器人
3. 获取 Token（格式：`123456789:ABCdefGHI...`）

**Chat ID:**
1. 搜索 `@userinfobot`
2. 点击 Start，获取你的 ID

详细步骤见 [INSTALL.md](docs/INSTALL.md#%EF%B8%8F-获取-telegram-凭证)

#### 2. 创建配置文件

```bash
# 创建工作目录
mkdir -p ~/watchtower && cd ~/watchtower

# 下载配置模板
curl -o docker-compose.yml https://raw.githubusercontent.com/Celestials316/watchtower-telegram-monitor/main/docker/docker-compose.yml

# 创建环境变量文件
cat > .env << 'EOF'
# Telegram 配置（必填）
BOT_TOKEN=你的_bot_token
CHAT_ID=你的_chat_id

# 服务器名称（可选，用于区分多台服务器）
SERVER_NAME=我的服务器

# 检查间隔（秒，默认 3600 = 1小时）
POLL_INTERVAL=3600

# 自动清理旧镜像（true/false）
CLEANUP=true

# 启用自动回滚（true/false）
ENABLE_ROLLBACK=true
EOF

# 编辑配置
nano .env
```

#### 3. 启动服务

```bash
# 创建数据目录
mkdir -p data

# 启动服务
docker compose up -d

# 查看日志
docker compose logs -f
```

#### 4. 验证运行

启动后几秒内，你应该会收到 Telegram 启动成功通知。

```bash
# 检查服务状态
docker compose ps

# 查看实时日志
docker compose logs -f watchtower-notifier
```

## 📋 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 | 必填 |
|--------|------|--------|------|
| `BOT_TOKEN` | Telegram Bot Token | - | ✅ |
| `CHAT_ID` | Telegram Chat ID | - | ✅ |
| `SERVER_NAME` | 服务器标识名称 | - | ❌ |
| `POLL_INTERVAL` | 检查间隔(秒) | 3600 | ❌ |
| `CLEANUP` | 自动清理旧镜像 | true | ❌ |
| `ENABLE_ROLLBACK` | 启用自动回滚 | true | ❌ |

### 监控特定容器

默认监控所有容器。如需监控特定容器，编辑 `docker-compose.yml`：

```yaml
services:
  watchtower:
    # ... 其他配置 ...
    command:
      - nginx        # 监控 nginx 容器
      - mysql        # 监控 mysql 容器
      - redis        # 监控 redis 容器
```

重启服务：
```bash
docker compose restart
```

## 🔧 管理命令

### 使用 Docker Compose

```bash
# 启动服务
docker compose up -d

# 停止服务
docker compose down

# 重启服务
docker compose restart

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f

# 更新镜像
docker compose pull
docker compose up -d
```

### 使用管理脚本（可选）

下载管理脚本以获得更友好的交互式管理：

```bash
cd ~/watchtower
curl -o manage.sh https://raw.githubusercontent.com/Celestials316/watchtower-telegram-monitor/main/scripts/manage.sh
chmod +x manage.sh

# 运行管理菜单
./manage.sh

# 或使用快捷命令
./manage.sh start      # 启动
./manage.sh stop       # 停止
./manage.sh restart    # 重启
./manage.sh logs       # 查看日志
./manage.sh status     # 查看状态
```

**设置全局命令（可选）：**

```bash
echo 'alias manage="cd ~/watchtower && ./manage.sh"' >> ~/.bashrc
source ~/.bashrc

# 现在可以在任意目录运行
manage
```

## 📖 详细文档

- [安装指南](docs/INSTALL.md) - 详细安装步骤和故障排查
- [配置说明](docs/CONFIGURATION.md) - 高级配置和自定义选项
- [常见问题](docs/FAQ.md) - 疑难解答

## 🔍 工作原理

```
┌─────────────────┐
│   Watchtower    │ ← 定期检查容器镜像更新
└────────┬────────┘
         │ 更新事件
         ↓
┌─────────────────┐
│  监控通知服务    │ ← 监听 Watchtower 日志
│  (本镜像)       │
└────────┬────────┘
         │
         ├─→ 记录容器状态到数据库
         │
         ├─→ 检测容器更新
         │
         ├─→ 验证更新结果
         │
         └─→ 发送 Telegram 通知
```

## 🛠️ 高级用法

### Docker Run 方式

```bash
# 先启动 Watchtower
docker run -d \
  --name watchtower \
  --restart unless-stopped \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e WATCHTOWER_CLEANUP=true \
  -e WATCHTOWER_POLL_INTERVAL=3600 \
  containrrr/watchtower:latest

# 再启动通知服务
docker run -d \
  --name watchtower-notifier \
  --restart unless-stopped \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v ~/watchtower/data:/data \
  -e BOT_TOKEN="your_bot_token" \
  -e CHAT_ID="your_chat_id" \
  -e SERVER_NAME="My Server" \
  -e POLL_INTERVAL=3600 \
  -e CLEANUP=true \
  -e ENABLE_ROLLBACK=true \
  w254992/watchtower-telegram-monitor:latest
```

### 多服务器部署

为每台服务器设置不同的 `SERVER_NAME`：

```bash
# 服务器 1
SERVER_NAME=生产服务器

# 服务器 2
SERVER_NAME=测试服务器

# 服务器 3
SERVER_NAME=开发服务器
```

通知消息会带上服务器标识：
```
[生产服务器] ✨ 容器更新成功
```

### 配置检查间隔

```bash
# 30 分钟检查一次
POLL_INTERVAL=1800

# 1 小时检查一次（推荐）
POLL_INTERVAL=3600

# 6 小时检查一次
POLL_INTERVAL=21600

# 每天检查一次
POLL_INTERVAL=86400
```

## 🐛 故障排查

### 收不到通知

1. **检查 Bot Token 和 Chat ID**
```bash
# 手动测试 Telegram API
curl "https://api.telegram.org/bot你的TOKEN/getMe"
```

2. **确保给 Bot 发送过消息**
   - 必须先在 Telegram 中给 Bot 发送任意消息
   - Bot 才能主动发送消息给你

3. **查看日志**
```bash
docker logs watchtower-notifier | grep -i error
```

### 容器无法启动

```bash
# 查看详细错误
docker logs watchtower-notifier --tail 50

# 检查配置文件
cat .env

# 检查 Docker socket 权限
ls -la /var/run/docker.sock
```

### 网络问题

如果看到 `TLS handshake timeout` 错误：

```bash
# 配置 Docker 镜像加速器
sudo tee /etc/docker/daemon.json <<-'EOF'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.mirrors.sjtug.sjtu.edu.cn"
  ]
}
EOF

sudo systemctl restart docker
cd ~/watchtower && docker compose restart
```

更多问题见 [故障排查文档](docs/INSTALL.md#-故障排查)

## 🔄 更新服务

```bash
cd ~/watchtower

# 拉取最新镜像
docker compose pull

# 重启服务
docker compose up -d

# 验证版本
docker exec watchtower-notifier sh -c 'grep "版本:" /app/monitor.sh | head -1'
```

## 🗑️ 卸载

```bash
cd ~/watchtower

# 停止并删除容器
docker compose down

# 删除数据（可选）
rm -rf data/

# 删除所有文件（可选）
cd .. && rm -rf watchtower/
```

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📝 更新日志

### v3.3.0 (2024-11-04)
- ✨ 重构核心逻辑，所有处理内联到主循环
- 🐛 修复管道子shell变量传递问题
- 📝 增强日志输出，实时显示处理步骤
- ⚡ 优化性能，简化架构

### v3.2.1
- 🔧 修复状态数据库写入问题
- 📊 改进版本信息读取逻辑

### v3.0.0
- 🎉 初始版本发布

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE)

## 💡 鸣谢

- [Watchtower](https://github.com/containrrr/watchtower) - 自动更新 Docker 容器
- [Telegram Bot API](https://core.telegram.org/bots/api) - 消息推送

## 📞 支持

- 🐛 [提交 Issue](https://github.com/Celestials316/watchtower-telegram-monitor/issues)
- 💬 [讨论区](https://github.com/Celestials316/watchtower-telegram-monitor/discussions)
- 📧 Email: your.email@example.com

---

**如果觉得有帮助，请给个 ⭐️ Star 支持一下！**