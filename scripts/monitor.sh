#!/bin/sh
# Docker 容器监控通知服务 v3.5.1 - 多服务器统一管理
# 修复: /check 命令不再重启容器，精简命令列表

echo "正在安装依赖..."
apk add --no-cache curl docker-cli coreutils grep sed tzdata jq >/dev/null 2>&1

TELEGRAM_API="https://api.telegram.org/bot${BOT_TOKEN}"
STATE_FILE="/data/container_state.db"
CONFIG_FILE="/data/bot_config.conf"
LAST_UPDATE_ID_FILE="/data/last_update_id"
SERVER_REGISTRY_FILE="/data/servers.json"

# 生成服务器唯一 ID
generate_server_id() {
    if [ -n "$SERVER_NAME" ]; then
        echo "${SERVER_NAME}" | md5sum | cut -d' ' -f1 | head -c 8
    else
        hostname | md5sum | cut -d' ' -f1 | head -c 8
    fi
}

SERVER_ID=$(generate_server_id)
SERVER_DISPLAY_NAME="${SERVER_NAME:-未命名服务器-${SERVER_ID}}"

# 确保数据目录存在
mkdir -p /data

# 初始化配置文件
if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" << EOF
POLL_INTERVAL=${POLL_INTERVAL:-3600}
MONITORED_CONTAINERS=${MONITORED_CONTAINERS:-}
ENABLE_ROLLBACK=${ENABLE_ROLLBACK:-true}
SERVER_ID=${SERVER_ID}
EOF
else
    if ! grep -q "SERVER_ID=" "$CONFIG_FILE"; then
        echo "SERVER_ID=${SERVER_ID}" >> "$CONFIG_FILE"
    fi
fi

# 加载配置
load_config() {
    if [ -f "$CONFIG_FILE" ]; then
        . "$CONFIG_FILE"
    fi
}

# 保存配置
save_config() {
    cat > "$CONFIG_FILE" << EOF
POLL_INTERVAL=${POLL_INTERVAL}
MONITORED_CONTAINERS=${MONITORED_CONTAINERS}
ENABLE_ROLLBACK=${ENABLE_ROLLBACK}
SERVER_ID=${SERVER_ID}
EOF
}

# 注册服务器到共享注册表
register_server() {
    local temp_registry="/tmp/servers_temp.json"

    if [ ! -f "$SERVER_REGISTRY_FILE" ]; then
        echo '{"servers":{}}' > "$SERVER_REGISTRY_FILE"
    fi

    registry=$(cat "$SERVER_REGISTRY_FILE" 2>/dev/null || echo '{"servers":{}}')
    container_count=$(docker ps --format '{{.Names}}' | grep -vE '^watchtower|^watchtower-notifier$' | wc -l)

    echo "$registry" | jq --arg sid "$SERVER_ID" \
                           --arg name "$SERVER_DISPLAY_NAME" \
                           --arg time "$(date +%s)" \
                           --arg count "$container_count" \
                           '.servers[$sid] = {
                               "name": $name,
                               "last_seen": $time | tonumber,
                               "container_count": $count | tonumber,
                               "status": "online"
                           }' > "$temp_registry"

    mv "$temp_registry" "$SERVER_REGISTRY_FILE"
}

# 获取在线服务器列表
get_online_servers() {
    if [ ! -f "$SERVER_REGISTRY_FILE" ]; then
        echo "[]"
        return
    fi

    current_time=$(date +%s)
    timeout=300

    cat "$SERVER_REGISTRY_FILE" | jq --arg now "$current_time" \
                                      --arg timeout "$timeout" \
        '[.servers | to_entries[] | 
          select(($now | tonumber) - .value.last_seen < ($timeout | tonumber)) | 
          {id: .key, name: .value.name, container_count: .value.container_count}]'
}

# 发送带内联键盘的消息
send_telegram_with_keyboard() {
    message="$1"
    keyboard="$2"

    curl -s -X POST "$TELEGRAM_API/sendMessage" \
        -H "Content-Type: application/json" \
        -d "{
            \"chat_id\": \"${CHAT_ID}\",
            \"text\": \"${message}\",
            \"parse_mode\": \"HTML\",
            \"reply_markup\": ${keyboard}
        }" >/dev/null 2>&1
}

# 生成服务器选择键盘
generate_server_keyboard() {
    command="$1"
    servers=$(get_online_servers)

    server_count=$(echo "$servers" | jq 'length')
    if [ "$server_count" -le 1 ]; then
        echo ""
        return
    fi

    keyboard=$(echo "$servers" | jq -c --arg cmd "$command" '{
        inline_keyboard: [
            [.[] | {
                text: "\(.name) (\(.container_count)个容器)",
                callback_data: ($cmd + ":" + .id)
            }]
        ]
    }')

    echo "$keyboard"
}

# 发送普通消息
send_telegram() {
    message="$1"
    reply_to="${2:-}"
    max_retries=3
    retry=0
    wait_time=5

    prefixed_message="<b>[${SERVER_DISPLAY_NAME}]</b> ${message}"

    while [ $retry -lt $max_retries ]; do
        if [ -n "$reply_to" ]; then
            response=$(curl -s -w "\n%{http_code}" -X POST "$TELEGRAM_API/sendMessage" \
                --data-urlencode "chat_id=${CHAT_ID}" \
                --data-urlencode "text=${prefixed_message}" \
                --data-urlencode "parse_mode=HTML" \
                --data-urlencode "reply_to_message_id=${reply_to}" \
                --connect-timeout 10 --max-time 30 2>&1)
        else
            response=$(curl -s -w "\n%{http_code}" -X POST "$TELEGRAM_API/sendMessage" \
                --data-urlencode "chat_id=${CHAT_ID}" \
                --data-urlencode "text=${prefixed_message}" \
                --data-urlencode "parse_mode=HTML" \
                --connect-timeout 10 --max-time 30 2>&1)
        fi

        curl_exit_code=$?
        http_code=$(echo "$response" | tail -n1)
        body=$(echo "$response" | sed '$d')

        if [ $curl_exit_code -ne 0 ]; then
            echo "  ✗ Curl 执行失败 (退出码: $curl_exit_code)" >&2
        elif [ "$http_code" = "200" ]; then
            if echo "$body" | grep -q '"ok":true'; then
                echo "  ✓ Telegram 通知发送成功"
                return 0
            fi
        fi

        retry=$((retry + 1))
        if [ $retry -lt $max_retries ]; then
            sleep $wait_time
            wait_time=$((wait_time * 2))
        fi
    done

    return 1
}

get_time() { date '+%Y-%m-%d %H:%M:%S'; }
get_short_id() { echo "$1" | sed 's/sha256://' | head -c 12 || echo "unknown"; }

# 获取 Telegram 更新
get_updates() {
    last_update_id=0
    if [ -f "$LAST_UPDATE_ID_FILE" ]; then
        last_update_id=$(cat "$LAST_UPDATE_ID_FILE")
    fi

    offset=$((last_update_id + 1))
    updates=$(curl -s "$TELEGRAM_API/getUpdates?offset=$offset&timeout=5" 2>/dev/null)

    if [ $? -eq 0 ] && [ -n "$updates" ]; then
        echo "$updates"
    fi
}

# 回答回调查询
answer_callback() {
    callback_id="$1"
    text="${2:-已处理}"
    
    curl -s -X POST "$TELEGRAM_API/answerCallbackQuery" \
        -d "callback_query_id=${callback_id}" \
        -d "text=${text}" >/dev/null 2>&1
}

# 处理回调查询（按钮点击）
process_callback() {
    callback_id="$1"
    callback_data="$2"
    from_user="$3"

    if [ "$from_user" != "$CHAT_ID" ]; then
        answer_callback "$callback_id" "⛔ 无权限"
        return
    fi

    command=$(echo "$callback_data" | cut -d':' -f1)
    target_server_id=$(echo "$callback_data" | cut -d':' -f2)

    if [ "$target_server_id" != "$SERVER_ID" ]; then
        return
    fi

    answer_callback "$callback_id" "正在处理..."

    case "$command" in
        /check)
            execute_check_command ""
            ;;
        /status)
            execute_status_command ""
            ;;
        /list)
            execute_list_command ""
            ;;
    esac
}

# 执行 status 命令
execute_status_command() {
    msg_id="$1"
    load_config
    container_count=$(docker ps --format '{{.Names}}' | grep -vE '^watchtower|^watchtower-notifier$' | wc -l)
    watchtower_status=$(docker inspect -f '{{.State.Status}}' watchtower 2>/dev/null || echo "unknown")

    monitored_info="所有容器"
    if [ -n "$MONITORED_CONTAINERS" ]; then
        monitored_info="<code>$MONITORED_CONTAINERS</code>"
    fi

    status_msg="📊 <b>服务状态</b>

━━━━━━━━━━━━━━━━━━━━
🎯 <b>监控服务</b>
   状态: $([ "$watchtower_status" = "running" ] && echo "运行中 ✅" || echo "已停止 ❌")
   检查间隔: $((POLL_INTERVAL / 60)) 分钟

📦 <b>容器监控</b>
   容器数: $container_count
   监控范围: $monitored_info

🔄 <b>自动回滚</b>
   $([ "$ENABLE_ROLLBACK" = "true" ] && echo "已启用 ✅" || echo "已禁用 ❌")

🆔 <b>服务器</b>
   ID: <code>${SERVER_ID}</code>
   时间: <code>$(get_time)</code>
━━━━━━━━━━━━━━━━━━━━"
    send_telegram "$status_msg" "$msg_id"
}

# 执行 check 命令 - 修复版本，不重启容器
execute_check_command() {
    msg_id="$1"
    send_telegram "🔄 正在手动检查更新，请稍候..." "$msg_id"

    # 后台执行检查，避免阻塞
    (
        echo "[$(date '+%H:%M:%S')] 开始执行手动检查..."
        
        # 使用 --run-once 在新容器中执行检查
        check_output=$(timeout 300 docker run --rm \
            -v /var/run/docker.sock:/var/run/docker.sock \
            containrrr/watchtower:latest \
            --run-once \
            --cleanup \
            --include-restarting \
            --include-stopped=false \
            2>&1)
        
        check_exit=$?
        echo "[$(date '+%H:%M:%S')] 检查命令退出码: $check_exit"

        # 解析结果
        if [ $check_exit -eq 124 ]; then
            send_telegram "⚠️ 检查超时（5分钟）

可能网络较慢，请稍后再试" "$msg_id"
            return
        fi

        if [ $check_exit -ne 0 ]; then
            send_telegram "❌ 检查执行失败

退出码: $check_exit
请查看日志排查问题" "$msg_id"
            return
        fi

        updated=$(echo "$check_output" | grep -o "Updated=[0-9]*" | grep -o "[0-9]*" | head -1 || echo "0")
        failed=$(echo "$check_output" | grep -o "Failed=[0-9]*" | grep -o "[0-9]*" | head -1 || echo "0")
        scanned=$(echo "$check_output" | grep -o "Scanned=[0-9]*" | grep -o "[0-9]*" | head -1 || echo "0")

        echo "[$(date '+%H:%M:%S')] 检查结果: Scanned=$scanned, Updated=$updated, Failed=$failed"

        if [ "$updated" -gt 0 ]; then
            send_telegram "✅ 检查完成

📊 扫描: ${scanned} 个容器
✨ 更新: ${updated} 个容器
❌ 失败: ${failed} 个

请等待更新完成的详细通知..." "$msg_id"
        else
            send_telegram "✅ 检查完成

📊 扫描: ${scanned} 个容器
✨ 所有容器都是最新版本
❌ 失败: ${failed} 个" "$msg_id"
        fi
    ) &
    
    echo "[$(date '+%H:%M:%S')] 检查任务已在后台启动 (PID: $!)"
}

# 执行 list 命令
execute_list_command() {
    msg_id="$1"
    containers=$(docker ps --format '{{.Names}}|||{{.Image}}|||{{.Status}}' | grep -vE '^watchtower' | head -20)

    if [ -z "$containers" ]; then
        send_telegram "📦 当前没有运行中的容器" "$msg_id"
        return
    fi

    containers_msg="📦 <b>运行中的容器</b>

━━━━━━━━━━━━━━━━━━━━"

    echo "$containers" | while IFS='|||' read -r name image status; do
        short_image=$(echo "$image" | sed 's/:latest$//' | head -c 30)
        containers_msg="$containers_msg
🔹 <b>$name</b>
   <code>$short_image</code>
"
    done

    count=$(echo "$containers" | wc -l)
    containers_msg="$containers_msg
━━━━━━━━━━━━━━━━━━━━
共 <b>$count</b> 个容器"

    send_telegram "$containers_msg" "$msg_id"
}

# 执行 servers 命令
execute_servers_command() {
    msg_id="$1"
    servers=$(get_online_servers)
    server_count=$(echo "$servers" | jq 'length')

    if [ "$server_count" -eq 0 ]; then
        send_telegram "📡 当前没有在线服务器" "$msg_id"
        return
    fi

    servers_msg="🌐 <b>在线服务器</b>

━━━━━━━━━━━━━━━━━━━━"

    echo "$servers" | jq -r '.[] | "\(.name)|\(.id)|\(.container_count)"' | while IFS='|' read -r name sid count; do
        indicator=""
        if [ "$sid" = "$SERVER_ID" ]; then
            indicator=" 👈"
        fi
        servers_msg="$servers_msg
🖥️ <b>$name</b>$indicator
   <code>$sid</code> | $count 个容器
"
    done

    servers_msg="$servers_msg
━━━━━━━━━━━━━━━━━━━━
共 $server_count 台在线"

    send_telegram "$servers_msg" "$msg_id"
}

# 执行 monitor 命令
execute_monitor_command() {
    msg_id="$1"
    containers="$2"

    load_config

    if [ -z "$containers" ]; then
        # 显示当前监控列表
        if [ -n "$MONITORED_CONTAINERS" ]; then
            send_telegram "📦 <b>当前监控列表</b>

$MONITORED_CONTAINERS

💡 修改: /monitor 容器名
💡 清空: /monitor all" "$msg_id"
        else
            send_telegram "📦 当前监控所有容器

💡 指定监控: /monitor 容器名
   例如: /monitor nginx mysql" "$msg_id"
        fi
        return
    fi

    if [ "$containers" = "all" ]; then
        MONITORED_CONTAINERS=""
        save_config
        send_telegram "✅ 已设置为监控所有容器" "$msg_id"
    else
        MONITORED_CONTAINERS="$containers"
        save_config
        send_telegram "✅ 监控列表已更新

监控: <code>$containers</code>" "$msg_id"
    fi
}

# 处理命令
process_command() {
    cmd="$1"
    msg_id="$2"
    user_id="$3"

    if [ "$user_id" != "$CHAT_ID" ]; then
        send_telegram "⛔ 无权限" "$msg_id"
        return
    fi

    case "$cmd" in
        /start|/help)
            help_msg="🤖 <b>Docker 监控 Bot v3.5.1</b>

━━━━━━━━━━━━━━━━━━━━
<b>🔍 查询命令</b>

/status - 查看服务状态
/list - 查看运行中的容器
/servers - 查看所有在线服务器

<b>🔄 操作命令</b>

/check - 立即检查更新
/monitor - 查看/设置监控列表
/monitor all - 监控所有容器
/monitor 容器名 - 监控指定容器

━━━━━━━━━━━━━━━━━━━━
<b>💡 多服务器管理</b>

有多个服务器时，命令会显示
服务器选择按钮

<b>当前服务器:</b> ${SERVER_DISPLAY_NAME}
<b>服务器ID:</b> <code>${SERVER_ID}</code>"
            send_telegram "$help_msg" "$msg_id"
            ;;

        /servers)
            execute_servers_command "$msg_id"
            ;;

        /status|/check|/list)
            servers=$(get_online_servers)
            server_count=$(echo "$servers" | jq 'length')

            if [ "$server_count" -le 1 ]; then
                case "$cmd" in
                    /status) execute_status_command "$msg_id" ;;
                    /check) execute_check_command "$msg_id" ;;
                    /list) execute_list_command "$msg_id" ;;
                esac
            else
                keyboard=$(generate_server_keyboard "$cmd")
                cmd_name=$(echo "$cmd" | sed 's|/||')
                send_telegram_with_keyboard "🌐 请选择服务器执行 <b>${cmd_name}</b>:" "$keyboard"
            fi
            ;;

        /monitor*)
            containers=$(echo "$cmd" | sed 's/\/monitor\s*//')
            execute_monitor_command "$msg_id" "$containers"
            ;;

        *)
            send_telegram "❌ 未知命令

发送 /help 查看命令列表" "$msg_id"
            ;;
    esac
}

# 心跳任务
heartbeat_task() {
    while true; do
        register_server
        sleep 30
    done
}

# 命令监听后台任务
command_listener() {
    echo "启动命令监听器..."

    while true; do
        updates=$(get_updates)

        if [ -n "$updates" ] && echo "$updates" | grep -q '"ok":true'; then
            echo "$updates" | jq -r '.result[] | @base64' 2>/dev/null | while read -r update; do
                decoded=$(echo "$update" | base64 -d 2>/dev/null)

                update_id=$(echo "$decoded" | jq -r '.update_id // empty' 2>/dev/null)

                # 处理普通消息
                message=$(echo "$decoded" | jq -r '.message.text // empty' 2>/dev/null)
                msg_id=$(echo "$decoded" | jq -r '.message.message_id // empty' 2>/dev/null)
                user_id=$(echo "$decoded" | jq -r '.message.from.id // empty' 2>/dev/null)

                # 处理回调查询
                callback_query=$(echo "$decoded" | jq -r '.callback_query // empty' 2>/dev/null)

                if [ -n "$update_id" ]; then
                    echo "$update_id" > "$LAST_UPDATE_ID_FILE"
                fi

                if [ -n "$message" ] && echo "$message" | grep -q '^/'; then
                    echo "[$(date '+%H:%M:%S')] 收到命令: $message (来自: $user_id)"
                    process_command "$message" "$msg_id" "$user_id"
                elif [ "$callback_query" != "null" ] && [ -n "$callback_query" ]; then
                    callback_id=$(echo "$decoded" | jq -r '.callback_query.id' 2>/dev/null)
                    callback_data=$(echo "$decoded" | jq -r '.callback_query.data' 2>/dev/null)
                    from_user=$(echo "$decoded" | jq -r '.callback_query.from.id' 2>/dev/null)

                    echo "[$(date '+%H:%M:%S')] 收到回调: $callback_data"
                    process_callback "$callback_id" "$callback_data" "$from_user"
                fi
            done
        fi

        sleep 2
    done
}

# 获取 danmu 版本
get_danmu_version() {
    container_name="$1"
    check_running="${2:-true}"

    if ! echo "$container_name" | grep -qE "danmu-api|danmu_api"; then
        echo ""
        return
    fi

    version=""

    if [ "$check_running" = "true" ]; then
        for i in $(seq 1 30); do
            if docker exec "$container_name" test -f /app/danmu_api/configs/globals.js 2>/dev/null; then
                break
            fi
            sleep 1
        done
    fi

    version=$(docker exec "$container_name" cat /app/danmu_api/configs/globals.js 2>/dev/null | \
              grep -m 1 "VERSION:" | sed -E "s/.*VERSION: '([^']+)'.*/\1/" 2>/dev/null || echo "")

    echo "$version"
}

format_version() {
    img_tag="$1"
    img_id="$2"
    container_name="$3"

    tag=$(echo "$img_tag" | grep -oE ':[^:]+$' | sed 's/://' || echo "latest")
    id_short=$(get_short_id "$img_id")

    if echo "$container_name" | grep -qE "danmu-api|danmu_api"; then
        real_version=$(get_danmu_version "$container_name")
        if [ -n "$real_version" ]; then
            echo "v${real_version} (${id_short})"
            return
        fi
    fi

    echo "$tag ($id_short)"
}

save_container_state() {
    container="$1"
    image_tag="$2"
    image_id="$3"
    version_info="$4"

    if [ ! -f "$STATE_FILE" ]; then
        touch "$STATE_FILE"
    fi

    echo "$container|$image_tag|$image_id|$version_info|$(date +%s)" >> "$STATE_FILE"
}

get_container_state() {
    container="$1"

    if [ ! -f "$STATE_FILE" ]; then
        echo "unknown:tag|sha256:unknown|"
        return
    fi

    state=$(grep "^${container}|" "$STATE_FILE" 2>/dev/null | tail -n 1)
    if [ -z "$state" ]; then
        echo "unknown:tag|sha256:unknown|"
        return
    fi

    echo "$state" | cut -d'|' -f2,3,4
}

cleanup_old_states() {
    if [ ! -f "$STATE_FILE" ]; then
        return
    fi

    cutoff_time=$(( $(date +%s) - 604800 ))
    temp_file="${STATE_FILE}.tmp"

    : > "$temp_file"

    if [ -s "$STATE_FILE" ]; then
        while IFS='|' read -r container image_tag image_id version_info timestamp || [ -n "$container" ]; do
            [ -z "$container" ] && continue

            if echo "$timestamp" | grep -qE '^[0-9]+$' && [ "$timestamp" -ge "$cutoff_time" ]; then
                echo "$container|$image_tag|$image_id|$version_info|$timestamp" >> "$temp_file"
            fi
        done < "$STATE_FILE"
    fi

    if [ -f "$temp_file" ]; then
        mv "$temp_file" "$STATE_FILE" 2>/dev/null || rm -f "$temp_file"
    fi
}

echo "=========================================="
echo "Docker 容器监控通知服务 v3.5.1"
echo "多服务器统一管理版本"
echo "服务器: ${SERVER_DISPLAY_NAME}"
echo "服务器ID: ${SERVER_ID}"
echo "启动时间: $(get_time)"
echo "=========================================="
echo ""

load_config
cleanup_old_states

echo "正在等待 watchtower 容器完全启动..."
while true; do
    if docker inspect -f '{{.State.Running}}' watchtower 2>/dev/null | grep -q "true"; then
        echo "Watchtower 已启动，准备监控日志"
        break
    else
        sleep 2
    fi
done

echo "正在初始化容器状态数据库..."
for container in $(docker ps --format '{{.Names}}'); do
    if [ "$container" = "watchtower" ] || [ "$container" = "watchtower-notifier" ]; then
        continue
    fi

    image_tag=$(docker inspect --format='{{.Config.Image}}' "$container" 2>/dev/null || echo "unknown:tag")
    image_id=$(docker inspect --format='{{.Image}}' "$container" 2>/dev/null || echo "sha256:unknown")

    version_info=$(get_danmu_version "$container" "false")

    save_container_state "$container" "$image_tag" "$image_id" "$version_info"

    if [ -n "$version_info" ]; then
        echo "  → 已保存 $container 的状态到数据库 (版本: v${version_info})"
    else
        echo "  → 已保存 $container 的状态到数据库"
    fi
done

container_count=$(docker ps --format '{{.Names}}' | grep -vE '^watchtower|^watchtower-notifier$' | wc -l)
echo "初始化完成，已记录 ${container_count} 个容器状态"

register_server
echo "服务器已注册到注册表，ID: ${SERVER_ID}"

startup_message="🚀 <b>监控服务启动成功</b>

━━━━━━━━━━━━━━━━━━━━
📊 <b>服务信息</b>
   版本: v3.5.1
   服务器: ${SERVER_DISPLAY_NAME}
   ID: <code>${SERVER_ID}</code>

🎯 <b>监控状态</b>
   容器数: ${container_count}
   检查间隔: $((POLL_INTERVAL / 60))分钟

🤖 <b>交互命令</b>
   /help - 查看命令列表
   /check - 手动检查更新
   /status - 查看状态

⏰ <b>启动时间</b>
   $(get_time)
━━━━━━━━━━━━━━━━━━━━

✅ 服务正常运行中"

send_telegram "$startup_message"

heartbeat_task &
HEARTBEAT_PID=$!

command_listener &
LISTENER_PID=$!

echo "心跳任务已启动 (PID: $HEARTBEAT_PID)"
echo "命令监听器已启动 (PID: $LISTENER_PID)"
echo "开始监控 Watchtower 日志..."

cleanup() {
    echo "收到退出信号，正在清理..."

    if [ -f "$SERVER_REGISTRY_FILE" ]; then
        temp_registry="/tmp/servers_cleanup.json"
        cat "$SERVER_REGISTRY_FILE" | jq --arg sid "$SERVER_ID" \
            'if .servers[$sid] then .servers[$sid].status = "offline" else . end' \
            > "$temp_registry" 2>/dev/null
        mv "$temp_registry" "$SERVER_REGISTRY_FILE" 2>/dev/null
    fi

    kill $LISTENER_PID 2>/dev/null
    kill $HEARTBEAT_PID 2>/dev/null
    rm -f /tmp/session_data.txt

    echo "清理完成，服务已停止"
    exit 0
}

trap cleanup INT TERM

# 主循环 - 监控 Watchtower 日志
docker logs -f --tail 0 watchtower 2>&1 | while IFS= read -r line; do
    echo "[$(date '+%H:%M:%S')] $line"

    if echo "$line" | grep -q "Stopping /"; then
        container_name=$(echo "$line" | sed -n 's/.*Stopping \/\([^ ]*\).*/\1/p' | head -n1)
        if [ -n "$container_name" ]; then
            echo "[$(date '+%H:%M:%S')] → 捕获到停止: $container_name"

            old_state=$(get_container_state "$container_name")
            old_image_tag=$(echo "$old_state" | cut -d'|' -f1)
            old_image_id=$(echo "$old_state" | cut -d'|' -f2)
            old_version_info=$(echo "$old_state" | cut -d'|' -f3)

            echo "${container_name}|${old_image_tag}|${old_image_id}|${old_version_info}" >> /tmp/session_data.txt

            if [ -n "$old_version_info" ]; then
                echo "[$(date '+%H:%M:%S')]   → 已暂存旧信息: $old_image_tag ($old_image_id) v${old_version_info}"
            else
                echo "[$(date '+%H:%M:%S')]   → 已暂存旧信息: $old_image_tag ($old_image_id)"
            fi
        fi
    fi

    if echo "$line" | grep -q "Session done"; then
        updated=$(echo "$line" | grep -oP '(?<=Updated=)[0-9]+' || echo "0")

        echo "[$(date '+%H:%M:%S')] → Session 完成: Updated=$updated"

        if [ "$updated" -gt 0 ] && [ -f /tmp/session_data.txt ]; then
            echo "[$(date '+%H:%M:%S')] → 发现 ${updated} 处更新，立即处理..."

            while IFS='|' read -r container_name old_tag_full old_id_full old_version_info; do
                [ -z "$container_name" ] && continue

                echo "[$(date '+%H:%M:%S')] → 处理容器: $container_name"
                sleep 5

                for i in $(seq 1 60); do
                    status=$(docker inspect -f '{{.State.Running}}' "$container_name" 2>/dev/null || echo "false")
                    if [ "$status" = "true" ]; then
                        echo "[$(date '+%H:%M:%S')]   → 容器已启动"
                        sleep 5
                        break
                    fi
                    sleep 1
                done

                status=$(docker inspect -f '{{.State.Running}}' "$container_name" 2>/dev/null || echo "false")
                new_tag_full=$(docker inspect --format='{{.Config.Image}}' "$container_name" 2>/dev/null || echo "unknown:tag")
                new_id_full=$(docker inspect --format='{{.Image}}' "$container_name" 2>/dev/null || echo "sha256:unknown")

                new_version_info=""
                if echo "$container_name" | grep -qE "danmu-api|danmu_api"; then
                    if [ "$status" = "true" ]; then
                        new_version_info=$(get_danmu_version "$container_name" "true")
                    fi
                fi

                save_container_state "$container_name" "$new_tag_full" "$new_id_full" "$new_version_info"

                img_name=$(echo "$new_tag_full" | sed 's/:.*$//')
                time=$(get_time)

                old_tag=$(echo "$old_tag_full" | grep -oE ':[^:]+$' | sed 's/://' || echo "latest")
                new_tag=$(echo "$new_tag_full" | grep -oE ':[^:]+$' | sed 's/://' || echo "latest")
                old_id_short=$(echo "$old_id_full" | sed 's/sha256://' | head -c 12)
                new_id_short=$(echo "$new_id_full" | sed 's/sha256://' | head -c 12)

                if [ -n "$old_version_info" ]; then
                    old_ver_display="v${old_version_info} (${old_id_short})"
                else
                    old_ver_display="$old_tag ($old_id_short)"
                fi

                if [ -n "$new_version_info" ]; then
                    new_ver_display="v${new_version_info} (${new_id_short})"
                else
                    new_ver_display="$new_tag ($new_id_short)"
                fi

                if [ "$status" = "true" ]; then
                    message="✨ <b>容器更新成功</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器名称</b>
   <code>${container_name}</code>

🎯 <b>镜像信息</b>
   <code>${img_name}</code>

🔄 <b>版本变更</b>
   <code>${old_ver_display}</code>
   ➜
   <code>${new_ver_display}</code>

⏰ <b>更新时间</b>
   <code>${time}</code>
━━━━━━━━━━━━━━━━━━━━

✅ 容器已成功启动并运行正常"

                    echo "[$(date '+%H:%M:%S')]   → 发送成功通知..."
                else
                    message="❌ <b>容器启动失败</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器名称</b>
   <code>${container_name}</code>

🎯 <b>镜像信息</b>
   <code>${img_name}</code>

🔄 <b>版本变更</b>
   旧: <code>${old_ver_display}</code>
   新: <code>${new_ver_display}</code>

⏰ <b>更新时间</b>
   <code>${time}</code>
━━━━━━━━━━━━━━━━━━━━

⚠️ 更新后无法启动
💡 检查: <code>docker logs ${container_name}</code>"

                    echo "[$(date '+%H:%M:%S')]   → 发送失败通知..."
                fi

                send_telegram "$message"

            done < /tmp/session_data.txt

            rm -f /tmp/session_data.txt
            echo "[$(date '+%H:%M:%S')] → 所有通知已处理完成"

        elif [ "$updated" -eq 0 ]; then
            rm -f /tmp/session_data.txt 2>/dev/null
        fi
    fi

    if echo "$line" | grep -qiE "level=error.*fatal|level=fatal"; then
        if echo "$line" | grep -qiE "Skipping|Already up to date|No new images|connection refused.*timeout"; then
            continue
        fi

        container_name=$(echo "$line" | sed -n 's/.*container[=: ]\+\([a-zA-Z0-9_.\-]\+\).*/\1/p' | head -n1)

        error=$(echo "$line" | sed -n 's/.*msg="\([^"]*\)".*/\1/p' | head -c 200)
        [ -z "$error" ] && error=$(echo "$line" | grep -oE "error=.*" | head -c 200)
        [ -z "$error" ] && error=$(echo "$line" | head -c 200)

        if [ -n "$container_name" ] && [ "$container_name" != "watchtower" ] && [ "$container_name" != "watchtower-notifier" ]; then
            send_telegram "⚠️ <b>Watchtower 错误</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器</b>: <code>$container_name</code>
🔴 <b>错误</b>: <code>$error</code>
🕐 <b>时间</b>: <code>$(get_time)</code>
━━━━━━━━━━━━━━━━━━━━"
        fi
    fi
done

cleanup
