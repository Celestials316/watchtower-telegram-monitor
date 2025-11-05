# 安装指南 v3.4.0

本文档提供详细的安装步骤、配置说明和故障排查方法。v3.4.0 新增 Telegram 命令交互功能。

## 📋 目录

- [前置要求](#前置要求)
- [安装方式](#安装方式)
  - [方式 1: Docker Compose (推荐)](#方式-1-docker-compose-推荐)
  - [方式 2: Docker Run](#方式-2-docker-run)
  - [方式 3: 从源码构建](#方式-3-从源码构建)
- [获取 Telegram 凭证](#-获取-telegram-凭证)
- [配置说明](#配置说明)
- [验证安装](#验证安装)
- [故障排查](#-故障排查)
- [配置代理](#配置代理)

---

## 前置要求

### 系统要求

- **操作系统**: Linux (推荐 Ubuntu 20.04+, Debian 11+, CentOS 8+)
- **架构**: amd64, arm64, arm/v7
- **内存**: 最低 512MB，推荐 1GB+
- **磁盘**: 最低 100MB 可用空间

### 软件要求

1. **Docker**
   ```bash
   # 检查 Docker 版本（需要 20.10+）
   docker --version
   
   # 如果未安装，运行安装脚本
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   newgrp docker
   ```

2. **Docker Compose**
   ```bash
   # 检查版本（需要 v2.0+）
   docker compose version
   
   # 如果提示命令不存在，安装 Docker Compose
   sudo apt-get update
   sudo apt-get install docker-compose-plugin
   ```

3. **基础工具**
   ```bash
   # Ubuntu/Debian
   sudo apt-get install curl wget nano
   
   # CentOS/RHEL
   sudo yum install curl wget nano
   ```

---

## 安装方式

### 方式 1: Docker Compose (推荐)

这是最简单、最推荐的安装方式。

#### 步骤 1: 创建工作目录

```bash
# 创建目录
mkdir -p ~/watchtower && cd ~/watchtower

# 或使用自定义路径
mkdir -p /opt/watchtower && cd /opt/watchtower
```

#### 步骤 2: 下载配置文件

```bash
# 下载 docker-compose.yml
curl -o docker-compose.yml https://raw.githubusercontent.com/Celestials316/watchtower-telegram-monitor/main/docker/docker-compose.yml

# 下载监控脚本（v3.4.0 必需）
curl -o monitor.sh https://raw.githubusercontent.com/Celestials316/watchtower-telegram-monitor/main/scripts/monitor.sh

# 设置执行权限
chmod +x monitor.sh
```

#### 步骤 3: 编辑配置

```bash
# 编辑 docker-compose.yml
nano docker-compose.yml
```

**必须修改的配置：**
- `BOT_TOKEN`: 替换为你的 Telegram Bot Token
- `CHAT_ID`: 替换为你的 Telegram Chat ID

**可选修改的配置：**
- `SERVER_NAME`: 服务器名称（多服务器时用于区分）
- `POLL_INTERVAL`: 检查间隔（秒）
- `CLEANUP`: 是否自动清理旧镜像
- `ENABLE_ROLLBACK`: 是否启用自动回滚

保存文件: `Ctrl+O` → `Enter` → `Ctrl+X`

#### 步骤 4: 创建数据目录

```bash
mkdir -p data
```

#### 步骤 5: 启动服务

```bash
# 启动服务（后台运行）
docker compose up -d

# 查看启动日志
docker compose logs -f

# 看到启动成功信息后，按 Ctrl+C 退出日志查看
```

#### 步骤 6: 验证运行

```bash
# 检查容器状态
docker compose ps

# 应该看到两个容器都在运行:
# watchtower          running
# watchtower-notifier running

# 查看通知服务日志
docker compose logs watchtower-notifier | tail -30
```

**预期结果：**
- 启动后 10-30 秒内收到 Telegram 启动成功通知
- 日志中显示 "命令监听器已启动"
- 可以在 Telegram 中发送 `/help` 收到命令列表

---

### 方式 2: Docker Run

如果不想使用 Docker Compose，可以用传统的 `docker run` 命令。

#### 步骤 1: 准备文件

```bash
mkdir -p ~/watchtower/{data}
cd ~/watchtower

# 下载监控脚本
curl -o monitor.sh https://raw.githubusercontent.com/Celestials316/watchtower-telegram-monitor/main/scripts/monitor.sh
chmod +x monitor.sh
```

#### 步骤 2: 启动 Watchtower

```bash
docker run -d \
  --name watchtower \
  --restart unless-stopped \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /etc/localtime:/etc/localtime:ro \
  -e WATCHTOWER_CLEANUP=true \
  -e WATCHTOWER_POLL_INTERVAL=3600 \
  -e WATCHTOWER_NO_STARTUP_MESSAGE=true \
  -e TZ=Asia/Shanghai \
  --label com.centurylinklabs.watchtower.enable=false \
  containrrr/watchtower:latest
```

#### 步骤 3: 启动通知服务

```bash
docker run -d \
  --name watchtower-notifier \
  --restart unless-stopped \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v ~/watchtower/data:/data \
  -v ~/watchtower/monitor.sh:/app/monitor.sh:ro \
  -e BOT_TOKEN="你的_bot_token" \
  -e CHAT_ID="你的_chat_id" \
  -e SERVER_NAME="我的服务器" \
  -e POLL_INTERVAL=3600 \
  -e CLEANUP=true \
  -e ENABLE_ROLLBACK=true \
  -e TZ=Asia/Shanghai \
  --label com.centurylinklabs.watchtower.enable=false \
  Celestials316/watchtower-telegram-monitor:latest
```

**注意:** 记得替换 `BOT_TOKEN` 和 `CHAT_ID`

---

### 方式 3: 从源码构建

适合需要自定义修改的用户。

#### 步骤 1: 克隆仓库

```bash
git clone https://github.com/Celestials316/watchtower-telegram-monitor.git
cd watchtower-telegram-monitor
```

#### 步骤 2: 构建镜像

```bash
# 构建镜像
docker build -f docker/Dockerfile -t watchtower-monitor:local .

# 查看构建结果
docker images | grep watchtower-monitor
```

#### 步骤 3: 修改配置

```bash
# 修改 docker-compose.yml 中的镜像名
sed -i 's|Celestials316/watchtower-telegram-monitor:latest|watchtower-monitor:local|g' docker/docker-compose.yml

# 编辑配置
nano docker/docker-compose.yml
```

#### 步骤 4: 启动服务

```bash
docker compose -f docker/docker-compose.yml up -d
```

---

## 🎫 获取 Telegram 凭证

### 获取 Bot Token

1. **打开 Telegram**，搜索 `@BotFather`

2. **创建新机器人**
   ```
   /newbot
   ```

3. **设置机器人名称**
   ```
   Bot 显示名称: 容器监控助手
   Bot 用户名: my_docker_monitor_bot
   ```
   用户名必须以 `bot` 结尾

4. **获取 Token**
   
   BotFather 会返回类似这样的消息：
   ```
   Done! Congratulations on your new bot.
   ...
   Use this token to access the HTTP API:
   1234567890:ABCdefGHIjklMNOpqrsTUVwxyz1234567
   ```
   
   复制这个 Token

5. **测试 Token**
   ```bash
   curl "https://api.telegram.org/bot你的TOKEN/getMe"
   ```
   
   应该返回机器人信息

### 获取 Chat ID

有三种方法获取你的 Chat ID：

#### 方法 1: 使用 @userinfobot (最简单)

1. 在 Telegram 搜索 `@userinfobot`
2. 点击 Start
3. 机器人会显示你的 ID
   ```
   Your ID: 123456789
   ```

#### 方法 2: 发消息获取

1. **先给你的 Bot 发送任意消息**（这一步很重要！）
2. 访问以下网址（替换 TOKEN）:
   ```
   https://api.telegram.org/bot你的TOKEN/getUpdates
   ```

3. 在返回的 JSON 中找到 `chat.id`:
   ```json
   {
     "result": [
       {
         "update_id": 123456789,
         "message": {
           "chat": {
             "id": 987654321,  // ← 这是你的 Chat ID
             "type": "private"
           }
         }
       }
     ]
   }
   ```

#### 方法 3: 使用命令行工具

```bash
# 替换 YOUR_TOKEN
TOKEN="你的_bot_token"

# 先给 Bot 发送一条消息，然后运行:
curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates" | \
  grep -o '"chat":{"id":[0-9]*' | \
  grep -o '[0-9]*$'
```

### 测试凭证

```bash
# 测试发送消息
BOT_TOKEN="你的_token"
CHAT_ID="你的_chat_id"

curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  -d "chat_id=${CHAT_ID}" \
  -d "text=测试消息 - 如果收到这条消息说明配置正确"
```

如果收到消息，说明配置正确！

---

## 配置说明

### 环境变量详解

| 变量名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `BOT_TOKEN` | String | - | Telegram Bot Token，**必填** |
| `CHAT_ID` | String/Number | - | Telegram Chat ID，**必填** |
| `SERVER_NAME` | String | 空 | 服务器标识，显示在通知前缀 |
| `POLL_INTERVAL` | Number | 3600 | 检查间隔(秒) |
| `CLEANUP` | Boolean | true | 是否自动清理旧镜像 |
| `ENABLE_ROLLBACK` | Boolean | true | 是否启用自动回滚 |

**注意**: v3.4.0 支持通过 Telegram 命令动态修改部分配置！

### 检查间隔建议

| 间隔 | 秒数 | 适用场景 | Telegram 命令 |
|------|------|----------|---------------|
| 30 分钟 | 1800 | 开发环境 | `/interval 1800` |
| 1 小时 | 3600 | **推荐** | `/interval 3600` |
| 6 小时 | 21600 | 稳定环境 | `/interval 21600` |
| 12 小时 | 43200 | 低频更新 | `/interval 43200` |
| 24 小时 | 86400 | 极低频 | `/interval 86400` |

可以通过 Telegram 命令 `/interval <秒>` 动态修改！

### 监控特定容器

有两种方式设置监控范围：

**方式 1: 通过 Telegram 命令（推荐）**
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

### 排除容器监控

给不想监控的容器添加标签：

```yaml
services:
  my-container:
    image: xxx
    labels:
      - "com.centurylinklabs.watchtower.enable=false"
```

---

## 验证安装

### 1. 检查容器状态

```bash
# 查看容器运行状态
docker compose ps

# 预期输出:
# NAME                  IMAGE                                      STATUS
# watchtower            containrrr/watchtower:latest              Up 2 minutes (healthy)
# watchtower-notifier   Celestials316/watchtower-telegram-...     Up 2 minutes (healthy)
```

### 2. 检查日志

```bash
# 查看启动日志
docker compose logs watchtower-notifier | head -50

# 应该看到:
# ==========================================
# Docker 容器监控通知服务 v3.4.0
# 支持 Telegram 命令交互
# ==========================================
# ...
# 命令监听器已启动 (PID: xxx)
```

### 3. 检查 Telegram 通知

启动后 10-30 秒内应该收到启动成功通知。

### 4. 测试 Telegram 命令

在 Telegram 中给 Bot 发送：

```
/help
```

应该收到命令列表回复。如果收到，说明命令功能正常！

### 5. 测试状态查询

发送：
```
/status
```

应该收到服务状态信息。

### 6. 测试手动检查

发送：
```
/check
```

应该收到 "已触发检查" 的回复。

---

## 🔧 故障排查

### 问题 1: 收不到启动通知

#### 症状
- 容器正常运行
- 日志中没有错误
- 但不收到 Telegram 消息

#### 解决方法

**1. 验证 Bot Token 和 Chat ID**

```bash
# 检查配置
cd ~/watchtower
cat docker-compose.yml | grep -E "BOT_TOKEN|CHAT_ID"

# 手动测试 API
BOT_TOKEN="你的token"
CHAT_ID="你的chatid"

curl -s "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  -d "chat_id=${CHAT_ID}" \
  -d "text=手动测试消息"
```

**2. 确保给 Bot 发送过消息**

必须先在 Telegram 中给 Bot 发送至少一条消息（任意内容），Bot 才能主动发消息给你。

**3. 检查代理配置（国内服务器）**

如果在中国大陆，需要配置代理才能访问 Telegram。参见 [配置代理](#配置代理)。

**4. 查看详细日志**

```bash
docker logs watchtower-notifier 2>&1 | grep -A 5 "Telegram"
```

### 问题 2: 命令无响应

#### 症状
- 发送命令后没有任何回复
- 启动通知正常收到

#### 解决方法

**1. 检查命令监听器**

```bash
# 查看日志确认监听器已启动
docker logs watchtower-notifier | grep "命令监听器"

# 应该看到: 命令监听器已启动 (PID: xxx)
```

**2. 验证 Chat ID 权限**

```bash
# 查看是否有 "收到命令" 的日志
docker logs watchtower-notifier | tail -20

# 发送命令后应该看到:
# [10:30:15] 收到命令: /help (来自: 你的CHATID)
```

**3. 检查命令格式**

确保命令以 `/` 开头，例如：
- ✅ `/help`
- ✅ `/status`
- ❌ `help` (缺少 /)
- ❌ `/ help` (有空格)

**4. 重启服务**

```bash
cd ~/watchtower
docker compose restart watchtower-notifier

# 等待 10 秒后测试
sleep 10
# 发送 /help 测试
```

### 问题 3: 容器无法启动

#### 症状
```bash
docker compose ps
# 显示容器状态为 Exited 或 Restarting
```

#### 解决方法

**1. 查看详细错误**

```bash
docker compose logs watchtower-notifier --tail 100
```

**2. 检查 monitor.sh 文件**

```bash
# 确认文件存在
ls -la ~/watchtower/monitor.sh

# 如果不存在，重新下载
curl -o monitor.sh https://raw.githubusercontent.com/Celestials316/watchtower-telegram-monitor/main/scripts/monitor.sh
chmod +x monitor.sh
```

**3. 检查 Docker socket 权限**

```bash
# 检查权限
ls -la /var/run/docker.sock

# 输出应该类似:
# srw-rw---- 1 root docker 0 Nov 5 10:00 /var/run/docker.sock

# 如果没有权限，临时修复:
sudo chmod 666 /var/run/docker.sock
```

**4. 检查环境变量**

```bash
# 验证环境变量格式
docker compose config | grep -A 5 "BOT_TOKEN"

# 确保:
# - 没有多余的空格
# - 值正确
```

### 问题 4: 网络连接问题（国内必看）

#### 症状
```
✗ Curl 执行失败
net/http: TLS handshake timeout
EOF
```

#### 解决方法

这是因为无法访问 Telegram API，需要配置代理。参见下一节 [配置代理](#配置代理)。

### 问题 5: 配置修改未生效

#### 症状
通过 Telegram 命令修改了配置，但实际未生效。

#### 解决方法

某些配置需要重启服务：

```bash
# 检查间隔需要重启 watchtower
docker compose restart watchtower

# 监控容器列表需要修改 docker-compose.yml 后重启
docker compose restart
```

### 问题 6: 命令权限被拒绝

#### 症状
```
Bot: ⛔ 无权限执行命令
```

#### 解决方法

确保你的 Telegram User ID 和配置的 `CHAT_ID` 一致：

```bash
# 查看配置的 CHAT_ID
docker exec watchtower-notifier env | grep CHAT_ID

# 获取你的 User ID
# 1. 给 Bot 发送任意消息
# 2. 运行:
curl "https://api.telegram.org/bot你的TOKEN/getUpdates" | \
  jq '.result[-1].message.from.id'
```

---

## 配置代理

**国内服务器必须配置代理才能访问 Telegram！**

### 方法 1: 使用本地代理（推荐）

如果服务器上已运行代理软件（Clash, V2Ray 等）：

```yaml
# 编辑 docker-compose.yml
services:
  watchtower-notifier:
    environment:
      - HTTP_PROXY=http://127.0.0.1:7890   # 替换为实际端口
      - HTTPS_PROXY=http://127.0.0.1:7890
      - NO_PROXY=localhost,127.0.0.1
```

**常见代理端口：**
- Clash: 7890
- V2Ray: 1080, 10808
- Shadowsocks: 1080

**验证代理可用：**
```bash
# 测试代理
curl -x http://127.0.0.1:7890 https://api.telegram.org

# 应该返回 401 或 404（说明能连接）
# 如果超时，说明代理不可用
```

### 方法 2: 使用 Telegram 反向代理

修改 `monitor.sh` 文件：

```bash
# 编辑
nano ~/watchtower/monitor.sh

# 找到这行:
TELEGRAM_API="https://api.telegram.org/bot${BOT_TOKEN}"

# 替换为反向代理（选一个可用的）:
TELEGRAM_API="https://api.telegram.dog/bot${BOT_TOKEN}"
# 或
TELEGRAM_API="https://tg.dev.completely.work/bot${BOT_TOKEN}"
```

保存后重启服务：
```bash
docker compose restart watchtower-notifier
```

### 方法 3: 使用海外服务器中转

如果你有海外服务器，可以用它做中转：

```bash
# 在海外服务器上运行（使用 SSH 端口转发）
ssh -N -L 8081:api.telegram.org:443 user@your-overseas-server

# 然后在 docker-compose.yml 中配置
HTTP_PROXY=http://localhost:8081
```

### 验证代理配置

```bash
# 重启服务
docker compose restart watchtower-notifier

# 查看日志
docker logs watchtower-notifier -f

# 应该看到 "✓ Telegram 通知发送成功"
```

---

## 高级配置

### 多服务器统一管理

所有服务器可以共用一个 Telegram Bot：

```yaml
# 服务器 1
SERVER_NAME=生产服务器-Web
POLL_INTERVAL=3600

# 服务器 2
SERVER_NAME=生产服务器-DB
POLL_INTERVAL=3600

# 服务器 3
SERVER_NAME=测试环境
POLL_INTERVAL=1800
```

所有通知会带上服务器标识：
```
[生产服务器-Web] ✨ 容器更新成功
[测试环境] 📊 服务状态...
```

可以在同一个 Telegram 会话中管理所有服务器！

### 自定义通知格式

如果需要修改通知样式，可以编辑 `monitor.sh`：

```bash
nano ~/watchtower/monitor.sh

# 搜索 "startup_message" 或 "✨ 容器更新成功"
# 修改消息格式
```

### 配置日志轮转

```yaml
services:
  watchtower:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"    # 单个日志文件最大 10MB
        max-file: "3"      # 保留最近 3 个文件
```

---

## 下一步

- 📖 查看 [README.md](../README.md) 了解所有 Telegram 命令
- 🤖 查看 [COMMANDS.md](COMMANDS.md) 命令详细文档
- ❓ 查看 [FAQ.md](FAQ.md) 常见问题
- 💬 加入 [讨论区](https://github.com/Celestials316/watchtower-telegram-monitor/discussions)

---

**安装过程中遇到问题？**

- 🐛 [提交 Issue](https://github.com/Celestials316/watchtower-telegram-monitor/issues/new)
- 💬 [讨论区求助](https://github.com/Celestials316/watchtower-telegram-monitor/discussions)