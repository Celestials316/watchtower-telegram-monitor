# 安装指南

本文档提供详细的安装步骤、配置说明和故障排查方法。

## 📋 目录

- [前置要求](#前置要求)
- [安装方式](#安装方式)
  - [方式 1: Docker Compose (推荐)](#方式-1-docker-compose-推荐)
  - [方式 2: Docker Run](#方式-2-docker-run)
  - [方式 3: 多服务器统一管理](#方式-3-多服务器统一管理)
- [获取 Telegram 凭证](#️-获取-telegram-凭证)
- [配置说明](#配置说明)
- [验证安装](#验证安装)
- [Telegram 交互命令](#telegram-交互命令)
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
      - WATCHTOWER_CLEANUP=true
      - WATCHTOWER_INCLUDE_RESTARTING=true
      - WATCHTOWER_INCLUDE_STOPPED=false
      - WATCHTOWER_NO_RESTART=false
      - WATCHTOWER_TIMEOUT=10s
      - WATCHTOWER_POLL_INTERVAL=3600
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
    image: celestials316/watchtower-telegram-monitor:latest
    container_name: watchtower-notifier
    restart: unless-stopped
    network_mode: host
    depends_on:
      watchtower:
        condition: service_started
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - monitor-data:/data
    environment:
      - TZ=Asia/Shanghai
      - BOT_TOKEN=your_bot_token_here
      - CHAT_ID=your_chat_id_here
      - SERVER_NAME=
      - POLL_INTERVAL=3600
      - CLEANUP=true
      - ENABLE_ROLLBACK=true
      - MONITORED_CONTAINERS=
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    healthcheck:
      test: ["CMD", "sh", "-c", "ps aux | grep -v grep | grep -q 'command_listener'"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 15s
    labels:
      - "com.centurylinklabs.watchtower.enable=false"

volumes:
  monitor-data:
```

将上述内容保存为 `docker-compose.yml`
</details>

#### 步骤 3: 配置环境变量

**v3.4.0+ 版本无需创建 .env 文件**，直接编辑 `docker-compose.yml` 中的环境变量：

```bash
# 编辑配置文件
nano docker-compose.yml
```

找到 `watchtower-notifier` 服务的 `environment` 部分，**必须修改**以下两项：

```yaml
- BOT_TOKEN=your_bot_token_here     # ← 替换为你的 Bot Token
- CHAT_ID=your_chat_id_here         # ← 替换为你的 Chat ID
```

**可选配置**（根据需要修改）：

```yaml
- SERVER_NAME=生产服务器             # 服务器标识名称
- POLL_INTERVAL=3600                # 检查间隔（秒）
- CLEANUP=true                      # 是否自动清理旧镜像
- ENABLE_ROLLBACK=true              # 是否启用自动回滚
- MONITORED_CONTAINERS=             # 监控的容器列表，留空监控所有
```

**代理配置**（国内服务器访问 Telegram 必需）：

如果你的服务器在中国大陆，需要配置代理才能访问 Telegram API：

```yaml
# 取消注释并替换为你的代理地址
- HTTP_PROXY=http://127.0.0.1:7890
- HTTPS_PROXY=http://127.0.0.1:7890
- NO_PROXY=localhost,127.0.0.1
```

保存文件: `Ctrl+O` → `Enter` → `Ctrl+X`

#### 步骤 4: 启动服务

```bash
# 启动服务（后台运行）
docker compose up -d

# 查看启动日志
docker compose logs -f watchtower-notifier

# 看到启动成功信息后，按 Ctrl+C 退出日志查看
```

#### 步骤 5: 验证运行

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
- 可以在 Telegram 中向 Bot 发送 `/status` 命令测试交互

---

### 方式 2: Docker Run

如果不想使用 Docker Compose，可以用传统的 `docker run` 命令。

#### 步骤 1: 创建数据卷

```bash
# 创建命名卷
docker volume create monitor-data
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
  -v monitor-data:/data \
  -e BOT_TOKEN="你的_bot_token" \
  -e CHAT_ID="你的_chat_id" \
  -e SERVER_NAME="我的服务器" \
  -e POLL_INTERVAL=3600 \
  -e CLEANUP=true \
  -e ENABLE_ROLLBACK=true \
  -e TZ=Asia/Shanghai \
  --label com.centurylinklabs.watchtower.enable=false \
  celestials316/watchtower-telegram-monitor:latest
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

### 方式 3: 多服务器统一管理

**v3.4.0+ 新特性**：支持多台服务器使用同一个 Bot Token，统一管理所有容器。

#### 工作原理

- 每台服务器自动生成唯一的 `SERVER_ID`
- 所有服务器共享 `/data` 目录（通过 NFS 或其他共享存储）
- 通过 Telegram 交互式选择要操作的服务器
- 心跳机制自动检测服务器在线状态

#### 部署步骤

**1. 准备共享存储**

使用 NFS、Ceph 或其他网络存储方案，让所有服务器都能访问同一个目录。

NFS 示例：

```bash
# 在 NFS 服务器上
sudo apt-get install nfs-kernel-server
sudo mkdir -p /nfs/watchtower-data
echo "/nfs/watchtower-data *(rw,sync,no_subtree_check,no_root_squash)" | sudo tee -a /etc/exports
sudo exportfs -ra

# 在各个客户端服务器上
sudo apt-get install nfs-common
sudo mkdir -p /mnt/watchtower-data
sudo mount nfs-server-ip:/nfs/watchtower-data /mnt/watchtower-data

# 开机自动挂载
echo "nfs-server-ip:/nfs/watchtower-data /mnt/watchtower-data nfs defaults 0 0" | sudo tee -a /etc/fstab
```

**2. 修改 docker-compose.yml**

在每台服务器上使用相同的配置，只需修改 `SERVER_NAME`：

```yaml
services:
  watchtower-notifier:
    volumes:
      - /mnt/watchtower-data:/data  # 挂载共享存储
    environment:
      - BOT_TOKEN=统一的token       # 所有服务器使用同一个
      - CHAT_ID=统一的chatid        # 所有服务器使用同一个
      - SERVER_NAME=服务器A          # ← 每台服务器不同
```

**3. 在每台服务器上启动**

```bash
# 服务器 A
cd ~/watchtower
nano docker-compose.yml  # 修改 SERVER_NAME=服务器A
docker compose up -d

# 服务器 B
cd ~/watchtower
nano docker-compose.yml  # 修改 SERVER_NAME=服务器B
docker compose up -d

# 服务器 C
cd ~/watchtower
nano docker-compose.yml  # 修改 SERVER_NAME=服务器C
docker compose up -d
```

**4. 使用交互式管理**

在 Telegram 中向 Bot 发送命令：

```
/status        # 显示服务器选择按钮
/restart       # 选择要重启的服务器
/logs          # 查看特定服务器日志
/servers       # 查看所有在线服务器
```

Bot 会显示内联键盘，让你选择要操作的服务器。

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
| `MONITORED_CONTAINERS` | String | 空 | 监控的容器列表（逗号分隔） |
| `HTTP_PROXY` | String | 空 | HTTP 代理地址 |
| `HTTPS_PROXY` | String | 空 | HTTPS 代理地址 |

### 检查间隔建议

| 间隔 | 秒数 | 适用场景 |
|------|------|----------|
| 30 分钟 | 1800 | 开发环境，频繁更新 |
| 1 小时 | 3600 | **推荐**，生产环境 |
| 6 小时 | 21600 | 稳定环境 |
| 12 小时 | 43200 | 低频更新 |
| 24 小时 | 86400 | 极低频更新 |

### 监控特定容器

有两种方式指定要监控的容器：

**方式 1: 通过环境变量**（推荐）

```yaml
environment:
  - MONITORED_CONTAINERS=nginx,mysql,redis  # 逗号分隔
```

**方式 2: 通过 Watchtower 命令**

编辑 `docker-compose.yml`，在 `watchtower` 服务下添加：

```yaml
services:
  watchtower:
    # ... 其他配置 ...
    command:
      - nginx
      - mysql
      - redis
```

重启服务：
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
# watchtower-notifier   celestials316/watchtower-telegram-...     Up 2 minutes (healthy)
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
# Docker 容器监控通知服务 v3.4.0
# 服务器: 我的服务器
# 服务器ID: abc123...
# 启动时间: 2024-11-06 10:30:00
# 回滚功能: true
# ==========================================
```

### 4. 检查 Telegram 通知

启动后 10-30 秒内应该收到启动成功通知。

如果没收到，检查日志中是否有错误：

```bash
docker compose logs watchtower-notifier | grep -i "error\|fail\|✗"
```

### 5. 测试交互功能

在 Telegram 中向 Bot 发送命令：

```
/status        # 查看容器状态
/logs          # 查看日志
/restart       # 重启容器（会显示选择按钮）
/servers       # 查看所有在线服务器（多服务器模式）
/help          # 查看帮助信息
```

Bot 应该立即响应并显示相应信息。

---

## Telegram 交互命令

v3.4.0+ 版本支持通过 Telegram 与机器人交互，管理 Docker 容器。

### 可用命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/start` | 启动机器人 | `/start` |
| `/help` | 显示帮助信息 | `/help` |
| `/status` | 查看容器状态 | `/status` |
| `/logs` | 查看容器日志 | `/logs nginx` |
| `/restart` | 重启容器 | `/restart nginx` |
| `/stop` | 停止容器 | `/stop redis` |
| `/start_container` | 启动容器 | `/start_container mysql` |
| `/servers` | 查看所有在线服务器 | `/servers` |

### 交互式操作

多服务器环境下，Bot 会显示服务器选择按钮：

```
你: /status

Bot: 请选择服务器:
     [服务器A] [服务器B] [服务器C]

（点击按钮后）

Bot: 
🖥️ 服务器: 服务器A
━━━━━━━━━━━━━━━━━━━━
📊 容器状态
正在运行: 5
已停止: 0
总计: 5
...
```

### 命令示例

```bash
# 查看状态
/status

# 查看特定容器日志（最近50行）
/logs nginx

# 重启容器
/restart mysql

# 停止容器
/stop redis

# 启动已停止的容器
/start_container app

# 查看所有在线服务器
/servers
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
docker compose config | grep -E "BOT_TOKEN|CHAT_ID"

# 手动测试 API
BOT_TOKEN="你的token"
CHAT_ID="你的chatid"

curl -s "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  -d "chat_id=${CHAT_ID}" \
  -d "text=手动测试消息"
```

**2. 确保给 Bot 发送过消息**

必须先在 Telegram 中给 Bot 发送至少一条消息（任意内容），Bot 才能主动发消息给你。

**3. 检查网络连接**

如果服务器在中国大陆，**必须配置代理**才能访问 Telegram：

```yaml
# 在 docker-compose.yml 中添加
environment:
  - HTTP_PROXY=http://127.0.0.1:7890
  - HTTPS_PROXY=http://127.0.0.1:7890
```

测试代理是否工作：

```bash
# 进入容器测试
docker exec -it watchtower-notifier sh

# 测试连接
apk add curl
curl -x http://127.0.0.1:7890 https://api.telegram.org/botYOUR_TOKEN/getMe

exit
```

**4. 查看详细日志**

```bash
# 查看发送失败的详细原因
docker logs watchtower-notifier 2>&1 | grep -A 5 "Telegram"
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
# 验证配置
docker compose config

# 确保 BOT_TOKEN 和 CHAT_ID 正确填写
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

### 问题 3: 交互命令无响应

#### 症状
- 在 Telegram 发送 `/status` 等命令
- Bot 没有任何响应

#### 解决方法

**1. 检查命令监听进程**

```bash
# 查看进程
docker exec watchtower-notifier ps aux | grep command_listener

# 应该看到类似:
# /app/monitor.sh command_listener
```

**2. 查看日志**

```bash
# 查看命令处理日志
docker logs watchtower-notifier | grep -i "command\|callback"
```

**3. 重启服务**

```bash
docker compose restart watchtower-notifier

# 等待10秒后测试
/status
```

**4. 检查 Telegram 更新**

```bash
# 手动检查是否收到命令
TOKEN="你的token"
curl "https://api.telegram.org/bot${TOKEN}/getUpdates"
```

### 问题 4: 多服务器选择按钮不显示

#### 症状
- 多服务器部署
- 发送命令后没有显示服务器选择按钮

#### 解决方法

**1. 检查共享存储**

```bash
# 在各个服务器上检查
ls -la /mnt/watchtower-data/servers.json

# 应该能看到服务器注册信息
cat /mnt/watchtower-data/servers.json
```

**2. 检查服务器在线状态**

在 Telegram 发送：
```
/servers
```

查看哪些服务器在线。如果某个服务器离线：

```bash
# 在该服务器上重启服务
docker compose restart watchtower-notifier

# 查看心跳日志
docker logs watchtower-notifier | grep "心跳"
```

**3. 手动清理注册表**

如果注册表损坏：

```bash
# 备份
cp /mnt/watchtower-data/servers.json /tmp/

# 删除（会自动重建）
rm /mnt/watchtower-data/servers.json

# 重启所有服务器的容器
# 在各个服务器上执行
docker compose restart watchtower-notifier
```

### 问题 5: 网络连接问题

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

编辑 `docker-compose.yml`，在 `watchtower` 服务的 `environment` 中修改：

```yaml
- WATCHTOWER_TIMEOUT=60s
- WATCHTOWER_HTTP_API_TIMEOUT=300
```

重启：
```bash
docker compose restart watchtower
```

**3. 测试网络连通性**

```bash
# 测试能否访问 Docker Hub
curl -I https://registry-1.docker.io/v2/

# 测试 DNS 解析
docker run --rm alpine nslookup registry-1.docker.io

# 测试拉取镜像
docker pull hello-world
```

### 问题 6: 数据持久化问题

#### 症状
```
✗ 无法创建状态文件
✗ 无法更新状态文件
```

#### 解决方法

**1. 检查数据卷**

```bash
# 查看卷
docker volume ls | grep monitor-data

# 检查卷详情
docker volume inspect monitor-data

# 查看挂载点
docker inspect watchtower-notifier | grep -A 10 "Mounts"
```

**2. 修复权限**

```bash
# 如果使用本地目录挂载
sudo chown -R $(id -u):$(id -g) ~/watchtower/data/
chmod 755 ~/watchtower/data/

# 如果使用命名卷
docker run --rm -v monitor-data:/data alpine sh -c "chmod 777 /data"
```

**3. 重新创建卷**

```bash
cd ~/watchtower
docker compose down -v  # 注意：会删除数据
docker volume create monitor-data
docker compose up -d
```

### 问题 7: 更新检测不工作

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

### 自定义检查间隔

针对不同容器设置不同的检查间隔：

```yaml
services:
  watchtower:
    environment:
      - WATCHTOWER_POLL_INTERVAL=3600  # 默认 1 小时
  
  # 对于频繁更新的容器，可以单独部署一个 Watchtower
  watchtower-dev:
    image: containrrr/watchtower:latest
    environment:
      - WATCHTOWER_POLL_INTERVAL=1800  # 30 分钟
    command:
      - dev-app  # 只监控开发环境应用
```

### 配置通知过滤

只接收特定类型的通知：

```bash
# 在 monitor.sh 中自定义（需要重新构建镜像）
# 或通过环境变量控制（如果脚本支持）
```

### 集成其他告警系统

除了 Telegram，还可以集成：

1. **企业微信**
   ```bash
   # 在发送通知函数中添加
   curl "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx" \
     -H 'Content-Type: application/json' \
     -d '{"msgtype": "text", "text": {"content": "容器更新通知"}}'
   ```

2. **钉钉**
   ```bash
   curl "https://oapi.dingtalk.com/robot/send?access_token=xxx" \
     -H 'Content-Type: application/json' \
     -d '{"msgtype": "text", "text": {"content": "容器更新通知"}}'
   ```

3. **邮件**
   ```bash
   echo "容器更新通知" | mail -s "Docker Monitor" user@example.com
   ```

### 监控策略优化

**按优先级分组监控**：

```yaml
# 生产环境 - 1小时检查
watchtower-prod:
  environment:
    - WATCHTOWER_POLL_INTERVAL=3600
  command:
    - nginx
    - mysql
    - redis

# 开发环境 - 30分钟检查
watchtower-dev:
  environment:
    - WATCHTOWER_POLL_INTERVAL=1800
  command:
    - dev-app
    - test-db
```

### 日志管理

**配置日志轮转**：

```yaml
services:
  watchtower-notifier:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"     # 单个文件最大 10MB
        max-file: "5"       # 保留 5 个文件
        compress: "true"    # 压缩旧日志
```

**导出日志到外部系统**：

```yaml
services:
  watchtower-notifier:
    logging:
      driver: "syslog"
      options:
        syslog-address: "tcp://192.168.1.100:514"
        tag: "watchtower-notifier"
```

### 安全加固

**1. 限制 Docker Socket 访问**：

```yaml
services:
  watchtower-notifier:
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro  # 只读
    security_opt:
      - no-new-privileges:true  # 禁止提权
    read_only: true  # 根文件系统只读
    tmpfs:
      - /tmp
```

**2. 使用 Docker Socket 代理**：

```bash
# 安装 docker-socket-proxy
docker run -d \
  --name docker-proxy \
  --privileged \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e CONTAINERS=1 \
  -e POST=0 \
  tecnativa/docker-socket-proxy

# 修改 watchtower-notifier 配置
# volumes:
#   - docker-proxy:2375  # 通过代理访问
```

### 性能优化

**1. 减少不必要的镜像拉取**：

```yaml
services:
  watchtower:
    environment:
      - WATCHTOWER_NO_PULL=false
      - WATCHTOWER_INCLUDE_STOPPED=false  # 不检查已停止的容器
      - WATCHTOWER_INCLUDE_RESTARTING=false
```

**2. 限制并发更新**：

```yaml
services:
  watchtower:
    environment:
      - WATCHTOWER_MAX_UPDATE_FAILURES=3
      - WATCHTOWER_PARALLEL_UPDATES=1  # 一次只更新一个容器
```

### 备份和恢复

**备份配置和数据**：

```bash
# 创建备份脚本
cat > ~/watchtower/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR=~/watchtower-backup-$(date +%Y%m%d)
mkdir -p $BACKUP_DIR

# 备份配置
cp ~/watchtower/docker-compose.yml $BACKUP_DIR/

# 备份数据
docker run --rm \
  -v monitor-data:/data \
  -v $BACKUP_DIR:/backup \
  alpine tar czf /backup/data.tar.gz -C /data .

echo "备份完成: $BACKUP_DIR"
EOF

chmod +x ~/watchtower/backup.sh

# 执行备份
~/watchtower/backup.sh
```

**恢复数据**：

```bash
# 恢复脚本
cat > ~/watchtower/restore.sh << 'EOF'
#!/bin/bash
BACKUP_FILE=$1

if [ -z "$BACKUP_FILE" ]; then
  echo "用法: $0 <备份文件路径>"
  exit 1
fi

docker run --rm \
  -v monitor-data:/data \
  -v $(dirname $BACKUP_FILE):/backup \
  alpine sh -c "cd /data && tar xzf /backup/$(basename $BACKUP_FILE)"

echo "恢复完成"
EOF

chmod +x ~/watchtower/restore.sh

# 恢复数据
~/watchtower/restore.sh ~/watchtower-backup-20241106/data.tar.gz
```

### 定时任务

**自动重启服务（可选）**：

```bash
# 添加 cron 任务，每天凌晨 3 点重启
(crontab -l 2>/dev/null; echo "0 3 * * * cd ~/watchtower && docker compose restart watchtower-notifier") | crontab -
```

**定期清理日志**：

```bash
# 每周清理一次旧日志
(crontab -l 2>/dev/null; echo "0 0 * * 0 docker system prune -f --filter 'until=168h'") | crontab -
```

---

## 监控和维护

### 健康检查

**查看服务健康状态**：

```bash
# 检查健康状态
docker inspect watchtower-notifier --format='{{.State.Health.Status}}'

# 查看健康检查日志
docker inspect watchtower-notifier --format='{{json .State.Health}}' | jq
```

### 性能监控

**查看资源使用**：

```bash
# 实时监控
docker stats watchtower watchtower-notifier

# 查看历史数据
docker stats --no-stream watchtower watchtower-notifier
```

### 日志分析

**统计更新记录**：

```bash
# 查看成功更新次数
docker logs watchtower-notifier | grep "容器更新成功" | wc -l

# 查看失败更新
docker logs watchtower-notifier | grep "容器更新失败"

# 导出日志用于分析
docker logs watchtower-notifier > /tmp/monitor.log
```

---

## 卸载

### 完全卸载

```bash
cd ~/watchtower

# 停止并删除容器
docker compose down

# 删除数据卷
docker volume rm monitor-data

# 删除镜像（可选）
docker rmi celestials316/watchtower-telegram-monitor:latest
docker rmi containrrr/watchtower:latest

# 删除配置文件（可选）
cd .. && rm -rf watchtower/
```

### 保留数据卸载

```bash
cd ~/watchtower

# 只停止容器，保留数据
docker compose down

# 镜像和卷保留，可以随时恢复
# docker compose up -d
```

---

## 常见问题 FAQ

### Q: 多服务器部署必须使用共享存储吗？

**A:** 是的。多服务器统一管理需要共享 `/data` 目录，可以使用：
- NFS
- Ceph
- GlusterFS
- 云存储服务（如 AWS EFS, Azure Files）

### Q: 代理配置支持 SOCKS5 吗？

**A:** 目前只支持 HTTP/HTTPS 代理。如果需要 SOCKS5，可以本地运行 privoxy 转换：

```bash
# 安装 privoxy
sudo apt-get install privoxy

# 配置转发到 SOCKS5
echo "forward-socks5 / 127.0.0.1:1080 ." | sudo tee -a /etc/privoxy/config

# 使用 HTTP 代理
HTTP_PROXY=http://127.0.0.1:8118
```

### Q: 可以同时监控多个 Docker 主机吗？

**A:** 可以。有两种方案：
1. 在每个主机上部署一套（推荐单服务器）
2. 使用 Docker Swarm/Kubernetes 集中管理

### Q: 如何查看历史更新记录？

**A:** 查看数据库文件：

```bash
# 查看状态数据库
docker run --rm \
  -v monitor-data:/data \
  alpine cat /data/container_states.db

# 或进入容器查看
docker exec watchtower-notifier cat /data/container_states.db
```

### Q: 能否自定义通知模板？

**A:** v3.4.0 版本通知格式固定在镜像中。如需自定义：
1. Fork 项目并修改 `monitor.sh`
2. 重新构建镜像
3. 使用自己的镜像

未来版本可能支持通过配置文件自定义。

### Q: 是否支持 Webhook 通知？

**A:** 当前版本不支持。可以通过修改 `send_telegram()` 函数添加 Webhook 调用。

### Q: 心跳机制的间隔是多少？

**A:** 默认 30 秒更新一次，5 分钟无响应标记为离线。

---

## 技术支持

### 获取帮助

- 🐛 **Bug 报告**: [GitHub Issues](https://github.com/Celestials316/watchtower-telegram-monitor/issues)
- 💬 **功能建议**: [GitHub Discussions](https://github.com/Celestials316/watchtower-telegram-monitor/discussions)
- 📖 **文档**: [项目 Wiki](https://github.com/Celestials316/watchtower-telegram-monitor/wiki)
- 📧 **Email**: your.email@example.com

### 贡献指南

欢迎提交 Pull Request！请遵循：
1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

### 反馈渠道

遇到问题时，请提供：
- 系统信息 (`uname -a`)
- Docker 版本 (`docker --version`)
- 完整错误日志 (`docker logs watchtower-notifier`)
- 配置文件（脱敏后）

---

## 下一步

- 📖 返回 [README.md](../README.md) 了解功能特性
- ⚙️ 查看 [CONFIGURATION.md](CONFIGURATION.md) 了解高级配置
- 🐛 遇到问题？查看 [FAQ.md](FAQ.md)
- 💬 加入 [讨论区](https://github.com/Celestials316/watchtower-telegram-monitor/discussions)

---

**安装成功后别忘了给项目点个 ⭐️ Star！**