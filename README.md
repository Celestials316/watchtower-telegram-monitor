# Docker 容器监控系统 v3.4.0

[![Docker Pulls](https://img.shields.io/docker/pulls/Celestials316/watchtower-telegram-monitor)](https://hub.docker.com/r/Celestials316/watchtower-telegram-monitor)
[![Docker Image Size](https://img.shields.io/docker/image-size/Celestials316/watchtower-telegram-monitor)](https://hub.docker.com/r/Celestials316/watchtower-telegram-monitor)
[![GitHub Stars](https://img.shields.io/github/stars/Celestials316/watchtower-telegram-monitor?style=social)](https://github.com/Celestials316/watchtower-telegram-monitor)

自动监控 Docker 容器更新并通过 Telegram 发送**中文通知**，支持版本追踪、自动回滚、**Telegram 命令交互**。

## ✨ 特性

- 🤖 **Telegram 命令交互** - 直接在 Telegram 中管理和查询 (v3.4.0 新增)
- 🔔 **实时通知** - 容器更新成功/失败即时推送
- 📊 **版本追踪** - 记录容器镜像版本变化历史
- 🔄 **自动回滚** - 更新失败时自动恢复旧版本
- 💾 **状态持久化** - 数据库记录容器状态，重启不丢失
- 🎯 **灵活监控** - 支持监控所有容器或指定容器
- 🌐 **中文界面** - 通知消息完全中文化
- 🏷️ **服务器标识** - 多服务器环境下区分通知来源

## 🆕 v3.4.0 新功能

### Telegram 命令交互

现在可以直接在 Telegram 中控制监控服务！

**基础命令**
- `/help` - 显示命令列表
- `/status` - 查看服务状态
- `/containers` - 列出所有容器
- `/config` - 查看当前配置

**操作命令**
- `/check` - 立即检查更新
- `/pause` - 暂停自动检查
- `/resume` - 恢复自动检查
- `/logs` - 查看最近日志

**配置命令**
- `/interval <秒>` - 设置检查间隔
- `/monitor <容器名>` - 设置监控容器
- `/rollback on|off` - 开关自动回滚

## 📸 效果预览

### 启动通知
```
🚀 监控服务启动成功

━━━━━━━━━━━━━━━━━━━━
📊 服务信息
   版本: v3.4.0 (支持命令)

🎯 监控状态
   容器数: 4
   检查间隔: 30分钟

🤖 交互命令
   发送 /help 查看命令列表
   发送 /status 查看状态

⏰ 启动时间
   2024-11-05 10:30:00
━━━━━━━━━━━━━━━━━━━━

✅ 服务正常运行中
```

### 命令交互示例
```
你: /status

Bot: 📊 服务状态
━━━━━━━━━━━━━━━━━━━━
🎯 监控服务
   状态: 运行中 ✅
   自动检查: 已启用 ✅

📦 容器监控
   容器数: 5
   检查间隔: 30 分钟
━━━━━━━━━━━━━━━━━━━━
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
   2024-11-05 11:15:23
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

详细步骤见 [INSTALL.md](docs/INSTALL.md#-获取-telegram-凭证)

#### 2. 创建配置文件

```bash
# 创建工作目录
mkdir -p ~/watchtower && cd ~/watchtower

# 下载配置模板
curl -o docker-compose.yml https://raw.githubusercontent.com/Celestials316/watchtower-telegram-monitor/main/docker/docker-compose.yml

# 下载监控脚本
curl -o monitor.sh https://raw.githubusercontent.com/Celestials316/watchtower-telegram-monitor/main/scripts/monitor.sh
chmod +x monitor.sh

# 编辑配置
nano docker-compose.yml
# 修改 BOT_TOKEN 和 CHAT_ID
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

启动后几秒内，你应该会收到 Telegram 启动成功通知，并可以发送 `/help` 查看命令列表。

```bash
# 检查服务状态
docker compose ps

# 在 Telegram 中测试
# 发送: /status
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

**注意**: 可以通过 Telegram 命令动态修改部分配置！

### 监控特定容器

默认监控所有容器。有两种方式设置监控范围：

**方式 1: 通过 Telegram 命令**
```
/monitor nginx mysql redis
/monitor all  (监控所有)
```

**方式 2: 编辑 docker-compose.yml**
```yaml
services:
  watchtower:
    command:
      - nginx
      - mysql
      - redis
```

## 🤖 Telegram 命令详解

### 状态查询命令

#### `/status` - 查看服务状态
显示监控服务运行状态、容器数量、检查间隔等信息。

```
你: /status

Bot: 📊 服务状态
━━━━━━━━━━━━━━━━━━━━
🎯 监控服务
   状态: 运行中 ✅
   自动检查: 已启用 ✅

📦 容器监控
   容器数: 5
   检查间隔: 30 分钟

🔄 功能状态
   自动回滚: 已启用 ✅
   自动清理: 已启用 ✅
...
```

#### `/containers` - 列出所有容器
显示当前运行的所有容器及其镜像、状态。

```
你: /containers

Bot: 📦 运行中的容器
━━━━━━━━━━━━━━━━━━━━
🔹 nginx
   镜像: nginx:latest
   状态: Up 2 hours

🔹 mysql
   镜像: mysql:8.0
   状态: Up 3 hours
...
```

#### `/config` - 查看当前配置
显示检查间隔、监控范围、功能开关等配置。

#### `/logs` - 查看最近日志
显示 Watchtower 的最近 10 行日志。

### 操作命令

#### `/check` - 立即检查更新
手动触发一次容器更新检查，不等待自动检查间隔。

```
你: /check

Bot: 🔄 正在手动检查更新...
Bot: ✅ 已触发检查，请稍候查看结果
```

#### `/pause` - 暂停自动检查
暂停自动更新检查，但仍可使用 `/check` 手动检查。

```
你: /pause

Bot: ⏸️ 自动检查已暂停
使用 /resume 恢复自动检查
使用 /check 可手动触发检查
```

#### `/resume` - 恢复自动检查
恢复自动更新检查。

### 配置命令

#### `/interval <秒>` - 设置检查间隔
动态修改容器更新检查间隔。

```
你: /interval 3600

Bot: ✅ 检查间隔已更新
旧值: 30 分钟
新值: 60 分钟

⚠️ 注意: 需要重启服务才能生效
命令: docker compose restart
```

**常用间隔：**
- 1800 秒 (30 分钟)
- 3600 秒 (1 小时) - 推荐
- 21600 秒 (6 小时)
- 86400 秒 (24 小时)

#### `/monitor <容器名>` - 设置监控容器
指定要监控的容器列表。

```
你: /monitor nginx mysql redis

Bot: ✅ 监控容器已更新
监控列表: nginx mysql redis

⚠️ 需要重启服务才能生效
```

监控所有容器：
```
你: /monitor all

Bot: ✅ 已设置为监控所有容器
```

#### `/rollback on|off` - 开关自动回滚
启用或禁用容器更新失败时的自动回滚功能。

```
你: /rollback on

Bot: ✅ 自动回滚已启用
更新失败时将自动恢复旧版本
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

### 使用 Telegram 命令

大部分操作都可以通过 Telegram 命令完成，无需 SSH 登录服务器！

## 📖 详细文档

- [安装指南](docs/INSTALL.md) - 详细安装步骤和故障排查
- [配置说明](docs/CONFIGURATION.md) - 高级配置和自定义选项
- [命令参考](docs/COMMANDS.md) - Telegram 命令完整参考
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
│  (本镜像)       │    + 处理 Telegram 命令
└────────┬────────┘
         │
         ├─→ 记录容器状态到数据库
         │
         ├─→ 检测容器更新
         │
         ├─→ 验证更新结果
         │
         ├─→ 发送 Telegram 通知
         │
         └─→ 监听并响应 Telegram 命令
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
  -v ~/watchtower/monitor.sh:/app/monitor.sh:ro \
  -e BOT_TOKEN="your_bot_token" \
  -e CHAT_ID="your_chat_id" \
  -e SERVER_NAME="My Server" \
  -e POLL_INTERVAL=3600 \
  -e CLEANUP=true \
  -e ENABLE_ROLLBACK=true \
  Celestials316/watchtower-telegram-monitor:latest
```

### 多服务器部署

为每台服务器设置不同的 `SERVER_NAME`：

```yaml
# 服务器 1 - 生产环境
SERVER_NAME=生产服务器

# 服务器 2 - 测试环境
SERVER_NAME=测试服务器

# 服务器 3 - 开发环境
SERVER_NAME=开发服务器
```

所有服务器可以共用同一个 Telegram Bot，通过服务器名称区分：
```
[生产服务器] ✨ 容器更新成功
[测试服务器] 📊 服务状态...
```

### 配置代理（国内服务器必需）

如果在中国大陆使用，需要配置代理访问 Telegram：

```yaml
services:
  watchtower-notifier:
    environment:
      - HTTP_PROXY=http://127.0.0.1:7890
      - HTTPS_PROXY=http://127.0.0.1:7890
      - NO_PROXY=localhost,127.0.0.1
```

详见 [代理配置文档](docs/INSTALL.md#配置代理)

## 🐛 故障排查

### 收不到通知

1. **检查 Bot Token 和 Chat ID**
```bash
# 手动测试 Telegram API
curl "https://api.telegram.org/bot你的TOKEN/sendMessage?chat_id=你的CHATID&text=test"
```

2. **确保给 Bot 发送过消息**
   - 必须先在 Telegram 中给 Bot 发送任意消息
   - Bot 才能主动发送消息给你

3. **查看日志**
```bash
docker logs watchtower-notifier | grep -i error
```

### 命令无响应

1. **检查命令监听器**
```bash
# 查看日志
docker logs watchtower-notifier | grep "命令监听器"

# 确认监听器运行
docker exec watchtower-notifier ps aux | grep command_listener
```

2. **验证 Chat ID**
```bash
# 发送测试命令后查看日志
docker logs watchtower-notifier | grep "收到命令"
```

### 容器无法启动

```bash
# 查看详细错误
docker logs watchtower-notifier --tail 50

# 检查 monitor.sh 是否存在
ls -la ~/watchtower/monitor.sh

# 检查 Docker socket 权限
ls -la /var/run/docker.sock
```

更多问题见 [故障排查文档](docs/INSTALL.md#-故障排查)

## 🔄 更新服务

```bash
cd ~/watchtower

# 拉取最新镜像
docker compose pull

# 下载最新脚本（如果有更新）
curl -o monitor.sh https://raw.githubusercontent.com/Celestials316/watchtower-telegram-monitor/main/scripts/monitor.sh
chmod +x monitor.sh

# 重启服务
docker compose up -d

# 验证版本
docker logs watchtower-notifier | grep "版本:"
```

或在 Telegram 中发送 `/status` 查看版本。

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

### v3.4.0 (2024-11-05)
- 🤖 **新增** Telegram 命令交互功能
- ✨ 支持 `/status`, `/check`, `/interval` 等命令
- 🔧 支持动态修改配置（检查间隔、监控容器等）
- 📊 增强状态查询和日志查看功能
- 🔒 添加命令权限验证

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
- [Telegram Bot API](https://core.telegram.org/bots/api) - 消息推送和命令交互

## 📞 支持

- 🐛 [提交 Issue](https://github.com/Celestials316/watchtower-telegram-monitor/issues)
- 💬 [讨论区](https://github.com/Celestials316/watchtower-telegram-monitor/discussions)
- 📧 Email: your.email@example.com

## 🌟 Star History

如果觉得有帮助，请给个 ⭐️ Star 支持一下！

---

**Made with ❤️ for Docker enthusiasts**