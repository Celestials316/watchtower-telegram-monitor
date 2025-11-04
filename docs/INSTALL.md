# 安装指南

本文档提供详细的安装步骤、配置说明和故障排查方法。

## 📋 目录

- [前置要求](#前置要求)
- [安装方式](#安装方式)
  - [方式 1: Docker Compose (推荐)](#方式-1-docker-compose-推荐)
  - [方式 2: Docker Run](#方式-2-docker-run)
  - [方式 3: 从源码构建](#方式-3-从源码构建)
- [获取 Telegram 凭证](#️-获取-telegram-凭证)
- [配置说明](#配置说明)
- [验证安装](#验证安装)
- [故障排查](#-故障排查)
- [高级配置](#高级配置)

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
   # 方法 1: 使用 Docker 插件（推荐）
   sudo apt-get update
   sudo apt-get install docker-compose-plugin
   
   # 方法 2: 独立安装
   sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
   sudo chmod +x /usr/local/bin/docker-compose
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

# 如果 GitHub 访问较慢，使用代理或手动创建（见下方）
```

<details>
<summary>📄 手动创建 docker-compose.yml（点击展开）</summary>

```yaml
services:
  watchtower:
    image: containrrr/watchtower:latest
    container_name: watchtower
    restart: unless-stopped
    network_mode: host
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /etc/localtime:/etc/localtime:ro
      - /etc/timezone:/etc/timezone:ro
    environment:
      - WATCHTOWER_NOTIFICATIONS=
      - WATCHTOWER_NO_STARTUP_MESSAGE=true
      - TZ=Asia/Shanghai
      - WATCHTOWER_CLEANUP=${CLEANUP:-true}
      - WATCHTOWER_INCLUDE_RESTARTING=true
      - WATCHTOWER_INCLUDE_STOPPED=false
      - WATCHTOWER_NO_RESTART=false
      - WATCHTOWER_TIMEOUT=10s
      - WATCHTOWER_POLL_INTERVAL=${POLL_INTERVAL:-3600}
      - WATCHTOWER_DEBUG=false
      - WATCHTOWER_LOG_LEVEL=info
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    healthcheck:
      test: ["CMD", "sh", "-c", "ps aux | grep -v grep | grep -q watchtower"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    labels:
      - "com.centurylinklabs.watchtower.enable=false"

  watchtower-notifier:
    image: w254992/watchtower-telegram-monitor:latest
    container_name: watchtower-notifier
    restart: unless-stopped
    network_mode: host
    depends_on:
      watchtower:
        condition: service_started
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./data:/data
    env_file:
      - .env
    environment:
      - TZ=Asia/Shanghai
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    labels:
      - "com.centurylinklabs.watchtower.enable=false"
```

将上述内容保存为 `docker-compose.yml`
</details>

#### 步骤 3: 创建环境变量文件

```bash
# 创建 .env 文件
cat > .env << 'EOF'
# ========================================
# Docker 容器监控配置
# ========================================

# ----- Telegram 配置 (必填) -----
BOT_TOKEN=你的_bot_token_这里替换
CHAT_ID=你的_chat_id_这里替换

# ----- 服务器配置 (可选) -----
# 用于区分不同服务器的通知，会显示为 [服务器名] 前缀
SERVER_NAME=

# ----- 监控配置 -----
# 检查更新间隔(秒)
# 推荐值: 1800 (30分钟), 3600 (1小时), 21600 (6小时)
POLL_INTERVAL=3600

# 是否自动清理旧镜像 (true/false)
CLEANUP=true

# 是否启用自动回滚 (更新失败时恢复旧版本)
ENABLE_ROLLBACK=true

# ========================================
EOF

# 编辑配置文件
nano .env
```

**配置说明：**
- 必须填写 `BOT_TOKEN` 和 `CHAT_ID`
- 其他选项可以保持默认值
- 保存文件: `Ctrl+O` → `Enter` → `Ctrl+X`

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
docker compose logs watchtower-notifier | tail -20
```

**预期结果：**
- 启动后 10-30 秒内收到 Telegram 启动成功通知
- 日志中显示 "服务正常运行中"

---

### 方式 2: Docker Run

如果不想使用 Docker Compose，可以用传统的 `docker run` 命令。

#### 步骤 1: 创建数据目录

```bash
mkdir -p ~/watchtower/data
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
  -e BOT_TOKEN="你的_bot_token" \
  -e CHAT_ID="你的_chat_id" \
  -e SERVER_NAME="我的服务器" \
  -e POLL_INTERVAL=3600 \
  -e CLEANUP=true \
  -e ENABLE_ROLLBACK=true \
  -e TZ=Asia/Shanghai \
  --label com.centurylinklabs.watchtower.enable=false \
  w254992/watchtower-telegram-monitor:latest
```

**注意:** 记得替换 `BOT_TOKEN` 和 `CHAT_ID`

#### 验证运行

```bash
# 查看容器状态
docker ps | grep watchtower

# 查看日志
docker logs watchtower-notifier
```

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
# 复制配置模板
cp config/.env.example .env
nano .env

# 修改 docker-compose.yml 中的镜像名
sed -i 's|w254992/watchtower-telegram-monitor:latest|watchtower-monitor:local|g' docker/docker-compose.yml
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

1. 给你的 Bot 发送任意消息（必须先做这一步）
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

### 检查间隔建议

| 间隔 | 秒数 | 适用场景 |
|------|------|----------|
| 30 分钟 | 1800 | 开发环境，频繁更新 |
| 1 小时 | 3600 | **推荐**，生产环境 |
| 6 小时 | 21600 | 稳定环境 |
| 12 小时 | 43200 | 低频更新 |
| 24 小时 | 86400 | 极低频更新 |

### 监控特定容器

默认监控所有容器。如需监控特定容器：

1. 编辑 `docker-compose.yml`
2. 在 `watchtower` 服务下添加 `command` 部分：

```yaml
services:
  watchtower:
    # ... 其他配置 ...
    command:
      - nginx      # 只监控这些容器
      - mysql
      - redis
      - app
```

3. 重启服务：
```bash
docker compose restart
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
# watchtower-notifier   w254992/watchtower-telegram-monitor:...   Up 2 minutes (healthy)
```

### 2. 检查健康状态

```bash
# 查看健康检查结果
docker inspect watchtower | grep -A 5 "Health"
docker inspect watchtower-notifier | grep -A 5 "Health"

# 状态应该是 "healthy"
```

### 3. 查看日志

```bash
# 查看启动日志
docker compose logs watchtower-notifier | head -30

# 应该看到类似输出:
# ==========================================
# Docker 容器监控通知服务 v3.3.0
# 服务器: 我的服务器
# 启动时间: 2024-11-04 10:30:00
# 回滚功能: true
# ==========================================
```

### 4. 检查 Telegram 通知

启动后 10-30 秒内应该收到启动成功通知。

如果没收到，检查日志中是否有错误：

```bash
docker compose logs watchtower-notifier | grep -i "error\|fail\|✗"
```

### 5. 手动测试通知

重启通知服务会触发启动通知：

```bash
docker compose restart watchtower-notifier

# 等待 10 秒
sleep 10

# 查看日志确认
docker compose logs watchtower-notifier | tail -20
```

### 6. 测试容器更新检测

强制触发一次检查：

```bash
# 手动执行一次 Watchtower 检查
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  containrrr/watchtower:latest \
  --run-once \
  --debug

# 查看是否有更新通知
```

---

## 🔧 故障排查

### 问题 1: 收不到 Telegram 通知

#### 症状
- 容器正常运行
- 日志中没有错误
- 但不收到 Telegram 消息

#### 解决方法

**1. 验证 Bot Token 和 Chat ID**

```bash
# 检查配置
cd ~/watchtower
cat .env | grep -E "BOT_TOKEN|CHAT_ID"

# 手动测试 API
BOT_TOKEN="你的token"
CHAT_ID="你的chatid"

curl -s "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  -d "chat_id=${CHAT_ID}" \
  -d "text=手动测试消息"
```

**2. 确保给 Bot 发送过消息**

必须先在 Telegram 中给 Bot 发送至少一条消息（任意内容），Bot 才能主动发消息给你。

**3. 检查 Bot 是否被阻止**

```bash
# 获取 Bot 信息
curl "https://api.telegram.org/bot你的TOKEN/getMe"

# 检查 Chat 信息
curl "https://api.telegram.org/bot你的TOKEN/getChat?chat_id=你的CHATID"
```

**4. 查看详细日志**

```bash
# 查看发送失败的详细原因
docker logs watchtower-notifier 2>&1 | grep -A 5 "Telegram"
```

**5. 进入容器手动测试**

```bash
docker exec -it watchtower-notifier sh

# 在容器内测试
apk add curl
curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT_ID}" \
  --data-urlencode "text=容器内测试"

exit
```

### 问题 2: 容器无法启动

#### 症状
```bash
docker compose ps
# 显示容器状态为 Exited 或 Restarting
```

#### 解决方法

**1. 查看详细错误**

```bash
# 查看完整日志
docker compose logs watchtower-notifier

# 查看最近 50 行
docker logs watchtower-notifier --tail 50
```

**2. 检查 Docker socket 权限**

```bash
# 检查权限
ls -la /var/run/docker.sock

# 输出应该类似:
# srw-rw---- 1 root docker 0 Nov 4 10:00 /var/run/docker.sock

# 如果没有权限，临时修复:
sudo chmod 666 /var/run/docker.sock

# 永久解决（将当前用户加入 docker 组）:
sudo usermod -aG docker $USER
newgrp docker
```

**3. 检查环境变量**

```bash
# 验证 .env 文件格式
cat .env

# 确保:
# - 没有多余的空格
# - 没有引号包裹值（除非必要）
# - 每行一个变量
```

**4. 检查磁盘空间**

```bash
# 检查可用空间
df -h

# 清理 Docker 空间
docker system prune -a --volumes
```

**5. 重新创建容器**

```bash
cd ~/watchtower
docker compose down -v
docker compose up -d
```

### 问题 3: 网络连接问题

#### 症状
日志中出现：
```
TLS handshake timeout
Get "https://registry-1.docker.io/v2/": EOF
net/http: TLS handshake timeout
```

#### 解决方法

**1. 配置 Docker 镜像加速器（中国大陆必须）**

```bash
# 创建或编辑 Docker 配置
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<-'EOF'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.mirrors.sjtug.sjtu.edu.cn",
    "https://registry.docker-cn.com",
    "https://hub-mirror.c.163.com"
  ],
  "dns": ["8.8.8.8", "8.8.4.4"],
  "max-concurrent-downloads": 10
}
EOF

# 重启 Docker
sudo systemctl daemon-reload
sudo systemctl restart docker

# 验证配置
docker info | grep -A 5 "Registry Mirrors"

# 重启监控服务
cd ~/watchtower
docker compose restart
```

**2. 增加超时时间**

编辑 `docker-compose.yml`，在 `watchtower` 服务的 `environment` 中添加：

```yaml
- WATCHTOWER_TIMEOUT=60s
- WATCHTOWER_HTTP_API_TIMEOUT=300
```

重启：
```bash
docker compose restart watchtower
```

**3. 配置代理（如果有）**

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/http-proxy.conf <<-EOF
[Service]
Environment="HTTP_PROXY=http://proxy.example.com:8080"
Environment="HTTPS_PROXY=http://proxy.example.com:8080"
Environment="NO_PROXY=localhost,127.0.0.1"
EOF

sudo systemctl daemon-reload
sudo systemctl restart docker
```

**4. 测试网络连通性**

```bash
# 测试能否访问 Docker Hub
curl -I https://registry-1.docker.io/v2/

# 测试 DNS 解析
docker run --rm alpine nslookup registry-1.docker.io

# 测试拉取镜像
docker pull hello-world
```

### 问题 4: 数据库权限问题

#### 症状
```
✗ 无法创建状态文件
✗ 无法更新状态文件
```

#### 解决方法

```bash
# 检查数据目录权限
ls -la ~/watchtower/data/

# 修复权限
sudo chown -R $(id -u):$(id -g) ~/watchtower/data/
chmod 755 ~/watchtower/data/

# 重启服务
cd ~/watchtower
docker compose restart watchtower-notifier
```

### 问题 5: 端口冲突（使用 host 网络）

#### 症状
```
Error starting userland proxy: listen tcp 0.0.0.0:7768: bind: address already in use
```

#### 解决方法

```bash
# 查看端口占用
sudo netstat -tulpn | grep :7768
# 或
sudo lsof -i :7768

# 停止占用端口的服务
sudo systemctl stop 服务名

# 或杀死进程
sudo kill -9 进程PID
```

### 问题 6: 更新检测不工作

#### 症状
- 容器有更新但没有检测到
- 日志显示 `Updated=0`

#### 解决方法

**1. 手动触发检查**

```bash
# 强制检查一次
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  containrrr/watchtower:latest \
  --run-once \
  --debug

# 查看输出，确认能否检测到更新
```

**2. 检查容器标签**

确保要监控的容器没有被排除：

```bash
# 查看容器标签
docker inspect 容器名 | grep -i watchtower

# 如果看到 "watchtower.enable=false"，需要移除该标签
```

**3. 验证镜像更新**

```bash
# 手动拉取最新镜像
docker pull 镜像名:标签

# 查看是否有新版本
docker images | grep 镜像名
```

**4. 检查 Watchtower 配置**

```bash
# 查看 Watchtower 环境变量
docker inspect watchtower | grep -A 20 "Env"

# 确认监控范围
docker exec watchtower ps aux | grep watchtower
```

---

## 高级配置

### 多服务器部署

为每台服务器创建不同的配置：

```bash
# 服务器 1 (生产环境)
SERVER_NAME=生产服务器
POLL_INTERVAL=3600
ENABLE_ROLLBACK=true

# 服务器 2 (测试环境)
SERVER_NAME=测试服务器
POLL_INTERVAL=1800
ENABLE_ROLLBACK=false

# 服务器 3 (开发环境)
SERVER_NAME=开发环境
POLL_INTERVAL=900
ENABLE_ROLLBACK=false
```

### 自定义通知格式

如果需要修改通知样式，可以挂载自定义 `monitor.sh`：

```yaml
services:
  watchtower-notifier:
    volumes:
      - ./custom-monitor.sh:/app/monitor.sh:ro
      # ... 其他配置
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

### 使用外部数据库

如果需要将状态存储到外部数据库（如 MySQL/PostgreSQL），需要修改 `monitor.sh`。

### 集成告警系统

除了 Telegram，还可以集成其他告警方式：

- Email
- Slack
- 企业微信
- 钉钉

需要修改 `send_telegram()` 函数添加额外的通知渠道。

---

## 下一步

- 📖 查看 [README.md](../README.md) 了解功能特性
- ⚙️ 查看 [CONFIGURATION.md](CONFIGURATION.md) 了解高级配置
- 🐛 遇到问题？查看 [FAQ.md](FAQ.md)
- 💬 加入 [讨论区](https://github.com/Celestials316/watchtower-telegram-monitor/discussions)

---

**安装过程中遇到问题？**

- 🐛 [提交 Issue](https://github.com/Celestials316/watchtower-telegram-monitor/issues/new)
- 💬 [讨论区求助](https://github.com/Celestials316/watchtower-telegram-monitor/discussions)