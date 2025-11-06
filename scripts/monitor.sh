#!/bin/sh
# Docker 容器监控通知服务 v4.0.1
# 监控 Watchtower 日志并发送 Telegram 通知 + 机器人交互管理 (多服务器支持)

echo "正在安装依赖..."
apk add --no-cache curl docker-cli coreutils grep sed tzdata jq >/dev/null 2>&1

TELEGRAM_API="https://api.telegram.org/bot${BOT_TOKEN}"
STATE_FILE="/data/container_state.db"
MONITOR_CONFIG="/data/monitor_config.json"
TEMP_LOG="/tmp/watchtower_events.log"
BOT_PID_FILE="/tmp/bot_handler.pid"

# 确保数据目录和配置文件存在
mkdir -p /data
[ ! -f "$MONITOR_CONFIG" ] && echo '{}' > "$MONITOR_CONFIG"

# 验证 SERVER_NAME 是否设置
if [ -z "$SERVER_NAME" ]; then
    echo "错误: 必须设置 SERVER_NAME 环境变量"
    exit 1
fi

SERVER_TAG="<b>[${SERVER_NAME}]</b> "

# ==================== 通用函数 ====================

send_telegram() {
    message="$1"
    reply_markup="$2"
    max_retries=3
    retry=0
    wait_time=5

    while [ $retry -lt $max_retries ]; do
        if [ -n "$reply_markup" ]; then
            response=$(curl -s -w "\n%{http_code}" -X POST "$TELEGRAM_API/sendMessage" \
                --data-urlencode "chat_id=${CHAT_ID}" \
                --data-urlencode "text=${SERVER_TAG}${message}" \
                --data-urlencode "parse_mode=HTML" \
                --data-urlencode "reply_markup=${reply_markup}" \
                --connect-timeout 10 --max-time 30 2>&1)
        else
            response=$(curl -s -w "\n%{http_code}" -X POST "$TELEGRAM_API/sendMessage" \
                --data-urlencode "chat_id=${CHAT_ID}" \
                --data-urlencode "text=${SERVER_TAG}${message}" \
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
            else
                error_desc=$(echo "$body" | sed -n 's/.*"description":"\([^"]*\)".*/\1/p')
                echo "  ✗ Telegram API 错误: ${error_desc:-未知错误}" >&2
            fi
        else
            echo "  ✗ HTTP 请求失败 (状态码: $http_code)" >&2
        fi

        retry=$((retry + 1))
        if [ $retry -lt $max_retries ]; then
            echo "  ↻ ${wait_time}秒后重试 ($retry/$max_retries)..." >&2
            sleep $wait_time
            wait_time=$((wait_time * 2))
        fi
    done

    echo "  ✗ Telegram 通知最终失败 (已重试 $max_retries 次)" >&2
    return 1
}

answer_callback() {
    callback_query_id="$1"
    text="$2"

    curl -s -X POST "$TELEGRAM_API/answerCallbackQuery" \
        --data-urlencode "callback_query_id=${callback_query_id}" \
        --data-urlencode "text=${text}" \
        --connect-timeout 5 --max-time 10 >/dev/null 2>&1
}

edit_message() {
    chat_id="$1"
    message_id="$2"
    new_text="$3"
    reply_markup="$4"

    if [ -n "$reply_markup" ]; then
        curl -s -X POST "$TELEGRAM_API/editMessageText" \
            --data-urlencode "chat_id=${chat_id}" \
            --data-urlencode "message_id=${message_id}" \
            --data-urlencode "text=${new_text}" \
            --data-urlencode "parse_mode=HTML" \
            --data-urlencode "reply_markup=${reply_markup}" \
            --connect-timeout 10 --max-time 30 >/dev/null 2>&1
    else
        curl -s -X POST "$TELEGRAM_API/editMessageText" \
            --data-urlencode "chat_id=${chat_id}" \
            --data-urlencode "message_id=${message_id}" \
            --data-urlencode "text=${new_text}" \
            --data-urlencode "parse_mode=HTML" \
            --connect-timeout 10 --max-time 30 >/dev/null 2>&1
    fi
}

get_time() { date '+%Y-%m-%d %H:%M:%S'; }
get_image_name() { echo "$1" | sed 's/:.*$//'; }
get_short_id() { echo "$1" | sed 's/sha256://' | head -c 12 || echo "unknown"; }

# ==================== 多服务器管理 ====================

get_all_servers() {
    jq -r 'keys[]' "$MONITOR_CONFIG" 2>/dev/null || echo "$SERVER_NAME"
}

get_server_count() {
    get_all_servers | wc -l
}

# ==================== 容器管理函数 ====================

get_all_containers() {
    docker ps --format '{{.Names}}' | grep -vE '^watchtower$|^watchtower-notifier$' || true
}

is_container_monitored() {
    container="$1"
    excluded=$(jq -r --arg srv "$SERVER_NAME" --arg cnt "$container" \
        '.[$srv].excluded[]? | select(. == $cnt)' "$MONITOR_CONFIG" 2>/dev/null)

    if [ -n "$excluded" ]; then
        return 1
    else
        return 0
    fi
}

add_to_excluded() {
    container="$1"
    server="${2:-$SERVER_NAME}"
    jq --arg srv "$server" --arg cnt "$container" \
        '.[$srv].excluded = ((.[$srv].excluded // []) + [$cnt] | unique)' \
        "$MONITOR_CONFIG" > "${MONITOR_CONFIG}.tmp" && \
        mv "${MONITOR_CONFIG}.tmp" "$MONITOR_CONFIG"
}

remove_from_excluded() {
    container="$1"
    server="${2:-$SERVER_NAME}"
    jq --arg srv "$server" --arg cnt "$container" \
        '.[$srv].excluded = ((.[$srv].excluded // []) - [$cnt])' \
        "$MONITOR_CONFIG" > "${MONITOR_CONFIG}.tmp" && \
        mv "${MONITOR_CONFIG}.tmp" "$MONITOR_CONFIG"
}

get_monitored_containers() {
    for container in $(get_all_containers); do
        if is_container_monitored "$container"; then
            echo "$container"
        fi
    done
}

get_excluded_containers() {
    server="${1:-$SERVER_NAME}"
    jq -r --arg srv "$server" '.[$srv].excluded[]?' "$MONITOR_CONFIG" 2>/dev/null || true
}

# ==================== 版本管理函数 ====================

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

    if [ -z "$version" ]; then
        image_id=$(docker inspect --format='{{.Image}}' "$container_name" 2>/dev/null)
        if [ -n "$image_id" ] && [ "$image_id" != "sha256:unknown" ]; then
            version=$(docker run --rm --entrypoint cat "$image_id" \
                      /app/danmu_api/configs/globals.js 2>/dev/null | \
                      grep -m 1 "VERSION:" | sed -E "s/.*VERSION: '([^']+)'.*/\1/" 2>/dev/null || echo "")
        fi
    fi

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

# ==================== 状态管理函数 ====================

save_container_state() {
    container="$1"
    image_tag="$2"
    image_id="$3"
    version_info="$4"

    if [ ! -f "$STATE_FILE" ]; then
        touch "$STATE_FILE" || {
            echo "  ✗ 无法创建状态文件" >&2
            return 1
        }
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

# ==================== 机器人命令处理 ====================

handle_status_command() {
    chat_id="$1"
    specified_server="$2"

    # 如果指定了服务器，只显示该服务器
    if [ -n "$specified_server" ]; then
        if [ "$specified_server" != "$SERVER_NAME" ]; then
            return  # 不是当前服务器，忽略
        fi
        
        monitored=$(get_monitored_containers | wc -l)
        excluded=$(get_excluded_containers | wc -l)
        total=$(get_all_containers | wc -l)

        status_msg="📊 <b>服务器状态</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ <b>服务器信息</b>
   名称: <code>${SERVER_NAME}</code>
   时间: <code>$(get_time)</code>

📦 <b>容器统计</b>
   总计: <code>${total}</code>
   监控中: <code>${monitored}</code>
   已排除: <code>${excluded}</code>

🔍 <b>监控列表</b>"

        if [ "$monitored" -eq 0 ]; then
            status_msg="$status_msg
   <i>暂无监控容器</i>"
        else
            for container in $(get_monitored_containers); do
                status=$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || echo "false")
                if [ "$status" = "true" ]; then
                    status_icon="✅"
                else
                    status_icon="❌"
                fi

                image_tag=$(docker inspect --format='{{.Config.Image}}' "$container" 2>/dev/null | sed 's/.*://')
                status_msg="$status_msg
   $status_icon <code>$container</code> [$image_tag]"
            done
        fi

        if [ "$excluded" -gt 0 ]; then
            status_msg="$status_msg

🚫 <b>排除列表</b>"
            for container in $(get_excluded_containers); do
                status_msg="$status_msg
   • <code>$container</code>"
            done
        fi

        status_msg="$status_msg
━━━━━━━━━━━━━━━━━━━━"

        send_telegram "$status_msg"
        return
    fi

    # 没有指定服务器，显示所有服务器概览
    server_count=$(get_server_count)
    
    if [ "$server_count" -eq 1 ]; then
        # 单服务器直接显示详情
        handle_status_command "$chat_id" "$SERVER_NAME"
    else
        # 多服务器显示选择按钮
        buttons='{"inline_keyboard":['
        first=true
        for server in $(get_all_servers); do
            if [ "$first" = true ]; then
                first=false
            else
                buttons="$buttons,"
            fi
            buttons="$buttons[{\"text\":\"🖥️ $server\",\"callback_data\":\"status:$server\"}]"
        done
        buttons="$buttons"']}'

        send_telegram "请选择要查看的服务器：" "$buttons"
    fi
}

handle_update_command() {
    chat_id="$1"
    message_id="$2"
    server_param="$3"
    container_param="$4"

    server_count=$(get_server_count)

    # 情况1: /update container_name (单服务器或当前服务器)
    if [ -n "$server_param" ] && [ -z "$container_param" ]; then
        # 检查 server_param 是否是容器名
        if docker ps -a --format '{{.Names}}' | grep -q "^${server_param}$"; then
            container_param="$server_param"
            server_param="$SERVER_NAME"
        fi
    fi

    # 情况2: /update server_name container_name
    if [ -n "$server_param" ] && [ -n "$container_param" ]; then
        if [ "$server_param" != "$SERVER_NAME" ]; then
            return  # 不是当前服务器，忽略
        fi

        # 验证容器是否存在
        if ! docker ps -a --format '{{.Names}}' | grep -q "^${container_param}$"; then
            send_telegram "❌ 容器 <code>$container_param</code> 不存在"
            return
        fi

        # 直接执行更新确认流程
        confirm_msg="⚠️ <b>确认更新</b>

━━━━━━━━━━━━━━━━━━━━
📦 容器: <code>$container_param</code>

⚠️ 此操作将：
   1. 拉取最新镜像
   2. 停止当前容器
   3. 启动新版本容器

是否继续？
━━━━━━━━━━━━━━━━━━━━"

        buttons='{"inline_keyboard":['
        buttons="$buttons"'[{"text":"✅ 确认更新","callback_data":"confirm_update:'"$SERVER_NAME"':'"$container_param"'"}],'
        buttons="$buttons"'[{"text":"❌ 取消","callback_data":"cancel"}]'
        buttons="$buttons"']}'

        send_telegram "$confirm_msg" "$buttons"
        return
    fi

    # 情况3: /update (显示服务器选择)
    if [ "$server_count" -eq 1 ]; then
        # 单服务器直接显示容器列表
        containers=$(get_monitored_containers)

        if [ -z "$containers" ]; then
            send_telegram "⚠️ 当前没有可更新的容器"
            return
        fi

        buttons='{"inline_keyboard":['
        first=true
        for container in $containers; do
            if [ "$first" = true ]; then
                first=false
            else
                buttons="$buttons,"
            fi
            buttons="$buttons[{\"text\":\"📦 $container\",\"callback_data\":\"update:$SERVER_NAME:$container\"}]"
        done
        buttons="$buttons"']}'

        send_telegram "请选择要更新的容器：" "$buttons"
    else
        # 多服务器显示服务器选择
        buttons='{"inline_keyboard":['
        first=true
        for server in $(get_all_servers); do
            if [ "$first" = true ]; then
                first=false
            else
                buttons="$buttons,"
            fi
            buttons="$buttons[{\"text\":\"🖥️ $server\",\"callback_data\":\"update_server:$server\"}]"
        done
        buttons="$buttons"']}'

        send_telegram "请选择要操作的服务器：" "$buttons"
    fi
}

handle_restart_command() {
    chat_id="$1"
    message_id="$2"
    server_param="$3"
    container_param="$4"

    server_count=$(get_server_count)

    # 情况1: /restart container_name (单服务器或当前服务器)
    if [ -n "$server_param" ] && [ -z "$container_param" ]; then
        # 检查 server_param 是否是容器名
        if docker ps -a --format '{{.Names}}' | grep -q "^${server_param}$"; then
            container_param="$server_param"
            server_param="$SERVER_NAME"
        fi
    fi

    # 情况2: /restart server_name container_name
    if [ -n "$server_param" ] && [ -n "$container_param" ]; then
        if [ "$server_param" != "$SERVER_NAME" ]; then
            return  # 不是当前服务器，忽略
        fi

        # 验证容器是否存在
        if ! docker ps -a --format '{{.Names}}' | grep -q "^${container_param}$"; then
            send_telegram "❌ 容器 <code>$container_param</code> 不存在"
            return
        fi

        # 直接执行重启
        send_telegram "⏳ 正在重启容器 <code>$container_param</code>..."

        if docker restart "$container_param" >/dev/null 2>&1; then
            result_msg="✅ <b>重启成功</b>

━━━━━━━━━━━━━━━━━━━━
📦 容器: <code>$container_param</code>
⏰ 时间: <code>$(get_time)</code>
━━━━━━━━━━━━━━━━━━━━"
        else
            result_msg="❌ <b>重启失败</b>

━━━━━━━━━━━━━━━━━━━━
📦 容器: <code>$container_param</code>

请检查容器状态
━━━━━━━━━━━━━━━━━━━━"
        fi

        send_telegram "$result_msg"
        return
    fi

    # 情况3: /restart (显示服务器选择)
    if [ "$server_count" -eq 1 ]; then
        # 单服务器直接显示容器列表
        containers=$(get_all_containers)

        if [ -z "$containers" ]; then
            send_telegram "⚠️ 当前没有可重启的容器"
            return
        fi

        buttons='{"inline_keyboard":['
        first=true
        for container in $containers; do
            if [ "$first" = true ]; then
                first=false
            else
                buttons="$buttons,"
            fi
            buttons="$buttons[{\"text\":\"🔄 $container\",\"callback_data\":\"restart:$SERVER_NAME:$container\"}]"
        done
        buttons="$buttons"']}'

        send_telegram "请选择要重启的容器：" "$buttons"
    else
        # 多服务器显示服务器选择
        buttons='{"inline_keyboard":['
        first=true
        for server in $(get_all_servers); do
            if [ "$first" = true ]; then
                first=false
            else
                buttons="$buttons,"
            fi
            buttons="$buttons[{\"text\":\"🖥️ $server\",\"callback_data\":\"restart_server:$server\"}]"
        done
        buttons="$buttons"']}'

        send_telegram "请选择要操作的服务器：" "$buttons"
    fi
}

handle_monitor_command() {
    chat_id="$1"
    server_param="$2"

    server_count=$(get_server_count)

    # 如果指定了服务器
    if [ -n "$server_param" ]; then
        if [ "$server_param" != "$SERVER_NAME" ]; then
            return  # 不是当前服务器，忽略
        fi

        buttons='{"inline_keyboard":['
        buttons="$buttons"'[{"text":"➕ 添加监控","callback_data":"monitor:add:'"$SERVER_NAME"'"}],'
        buttons="$buttons"'[{"text":"➖ 移除监控","callback_data":"monitor:remove:'"$SERVER_NAME"'"}],'
        buttons="$buttons"'[{"text":"📋 查看列表","callback_data":"status:'"$SERVER_NAME"'"}]'
        buttons="$buttons"']}'

        send_telegram "📡 <b>监控管理 - ${SERVER_NAME}</b>\n\n请选择操作：" "$buttons"
        return
    fi

    # 没有指定服务器
    if [ "$server_count" -eq 1 ]; then
        # 单服务器直接显示操作选项
        handle_monitor_command "$chat_id" "$SERVER_NAME"
    else
        # 多服务器显示服务器选择
        buttons='{"inline_keyboard":['
        first=true
        for server in $(get_all_servers); do
            if [ "$first" = true ]; then
                first=false
            else
                buttons="$buttons,"
            fi
            buttons="$buttons[{\"text\":\"🖥️ $server\",\"callback_data\":\"monitor_server:$server\"}]"
        done
        buttons="$buttons"']}'

        send_telegram "请选择要管理的服务器：" "$buttons"
    fi
}

handle_help_command() {
    help_msg="📖 <b>命令帮助</b>

━━━━━━━━━━━━━━━━━━━━
<b>可用命令：</b>

/status [服务器名]
  查看服务器状态和容器列表

/update [服务器名] [容器名]
  更新容器镜像

/restart [服务器名] [容器名]
  重启容器

/monitor [服务器名]
  监控管理（添加/移除监控容器）

/help
  显示此帮助信息
━━━━━━━━━━━━━━━━━━━━

💡 <b>使用提示：</b>
• 单服务器环境：
  /restart nginx
  /update nginx

• 多服务器环境：
  /restart server1 nginx
  /update server1 nginx
  
• 不带参数则显示选择菜单：
  /restart  (显示服务器选择)
  /update   (显示服务器选择)

• 所有危险操作都需要二次确认

• 排除监控的容器不会收到自动更新通知
━━━━━━━━━━━━━━━━━━━━"

    send_telegram "$help_msg"
}

# ==================== 回调处理 ====================

handle_callback() {
    callback_data="$1"
    callback_query_id="$2"
    chat_id="$3"
    message_id="$4"

    action=$(echo "$callback_data" | cut -d: -f1)
    param1=$(echo "$callback_data" | cut -d: -f2)
    param2=$(echo "$callback_data" | cut -d: -f3)

    case "$action" in
        status)
            # param1 是服务器名
            if [ "$param1" = "$SERVER_NAME" ]; then
                answer_callback "$callback_query_id" "正在获取状态..."
                handle_status_command "$chat_id" "$param1"
            fi
            ;;

        update_server)
            # param1 是服务器名，显示该服务器的容器列表
            if [ "$param1" != "$SERVER_NAME" ]; then
                return
            fi

            answer_callback "$callback_query_id" "正在加载容器列表..."

            containers=$(get_monitored_containers)
            if [ -z "$containers" ]; then
                edit_message "$chat_id" "$message_id" "⚠️ 服务器 <code>$param1</code> 没有可更新的容器"
                return
            fi

            buttons='{"inline_keyboard":['
            first=true
            for container in $containers; do
                if [ "$first" = true ]; then
                    first=false
                else
                    buttons="$buttons,"
                fi
                buttons="$buttons[{\"text\":\"📦 $container\",\"callback_data\":\"update:$param1:$container\"}]"
            done
            buttons="$buttons"']}'

            edit_message "$chat_id" "$message_id" "服务器: <code>$param1</code>\n\n请选择要更新的容器：" "$buttons"
            ;;

        update)
            # param1 是服务器名，param2 是容器名
            if [ "$param1" != "$SERVER_NAME" ]; then
                return
            fi

            answer_callback "$callback_query_id" "正在准备更新..."

            confirm_msg="⚠️ <b>确认更新</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>$param1</code>
📦 容器: <code>$param2</code>

⚠️ 此操作将：
   1. 拉取最新镜像
   2. 停止当前容器
   3. 启动新版本容器

是否继续？
━━━━━━━━━━━━━━━━━━━━"

            buttons='{"inline_keyboard":['
            buttons="${buttons}[{\"text\":\"✅ 确认更新\",\"callback_data\":\"confirm_update:${param1}:${param2}\"}],"
            buttons="${buttons}[{\"text\":\"❌ 取消\",\"callback_data\":\"cancel\"}]"
            buttons="${buttons}]}"

            edit_message "$chat_id" "$message_id" "$confirm_msg" "$buttons"
            ;;

        confirm_update)
            if [ "$param1" != "$SERVER_NAME" ]; then
                return
            fi

            answer_callback "$callback_query_id" "开始更新容器..."
            edit_message "$chat_id" "$message_id" "⏳ 正在更新容器 <code>$param2</code>，请稍候..."

            # 执行更新
            (
                sleep 2
                old_id=$(docker inspect --format='{{.Image}}' "$param2" 2>/dev/null)
                docker pull $(docker inspect --format='{{.Config.Image}}' "$param2" 2>/dev/null) >/dev/null 2>&1
                docker stop "$param2" >/dev/null 2>&1
                docker rm "$param2" >/dev/null 2>&1

                result_msg="✅ <b>更新完成</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>$param1</code>
📦 容器: <code>$param2</code>

⚠️ 注意：
容器已停止，请使用原启动命令重新创建容器

💡 建议使用 docker-compose 或保存启动脚本
━━━━━━━━━━━━━━━━━━━━"

                edit_message "$chat_id" "$message_id" "$result_msg"
            ) &
            ;;

        restart_server)
            # param1 是服务器名，显示该服务器的容器列表
            if [ "$param1" != "$SERVER_NAME" ]; then
                return
            fi

            answer_callback "$callback_query_id" "正在加载容器列表..."

            containers=$(get_all_containers)
            if [ -z "$containers" ]; then
                edit_message "$chat_id" "$message_id" "⚠️ 服务器 <code>$param1</code> 没有可重启的容器"
                return
            fi

            buttons='{"inline_keyboard":['
            first=true
            for container in $containers; do
                if [ "$first" = true ]; then
                    first=false
                else
                    buttons="$buttons,"
                fi
                buttons="$buttons[{\"text\":\"🔄 $container\",\"callback_data\":\"restart:$param1:$container\"}]"
            done
            buttons="$buttons"']}'

            edit_message "$chat_id" "$message_id" "服务器: <code>$param1</code>\n\n请选择要重启的容器：" "$buttons"
            ;;

        restart)
            # param1 是服务器名，param2 是容器名
            if [ "$param1" != "$SERVER_NAME" ]; then
                return
            fi

            answer_callback "$callback_query_id" "正在准备重启..."

            confirm_msg="⚠️ <b>确认重启</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>$param1</code>
📦 容器: <code>$param2</code>

是否继续？
━━━━━━━━━━━━━━━━━━━━"

            buttons='{"inline_keyboard":['
            buttons="${buttons}[{\"text\":\"✅ 确认重启\",\"callback_data\":\"confirm_restart:${param1}:${param2}\"}],"
            buttons="${buttons}[{\"text\":\"❌ 取消\",\"callback_data\":\"cancel\"}]"
            buttons="${buttons}]}"

            edit_message "$chat_id" "$message_id" "$confirm_msg" "$buttons"
            ;;

        confirm_restart)
            if [ "$param1" != "$SERVER_NAME" ]; then
                return
            fi

            answer_callback "$callback_query_id" "开始重启容器..."
            edit_message "$chat_id" "$message_id" "⏳ 正在重启容器 <code>$param2</code>..."

            if docker restart "$param2" >/dev/null 2>&1; then
                result_msg="✅ <b>重启成功</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>$param1</code>
📦 容器: <code>$param2</code>
⏰ 时间: <code>$(get_time)</code>
━━━━━━━━━━━━━━━━━━━━"
            else
                result_msg="❌ <b>重启失败</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>$param1</code>
📦 容器: <code>$param2</code>

请检查容器状态
━━━━━━━━━━━━━━━━━━━━"
            fi

            edit_message "$chat_id" "$message_id" "$result_msg"
            ;;

        monitor_server)
            # param1 是服务器名
            if [ "$param1" != "$SERVER_NAME" ]; then
                return
            fi

            answer_callback "$callback_query_id" "正在加载监控选项..."

            buttons='{"inline_keyboard":['
            buttons="$buttons"'[{"text":"➕ 添加监控","callback_data":"monitor:add:'"$param1"'"}],'
            buttons="$buttons"'[{"text":"➖ 移除监控","callback_data":"monitor:remove:'"$param1"'"}],'
            buttons="$buttons"'[{"text":"📋 查看列表","callback_data":"status:'"$param1"'"}]'
            buttons="$buttons"']}'

            edit_message "$chat_id" "$message_id" "📡 <b>监控管理 - $param1</b>\n\n请选择操作：" "$buttons"
            ;;

        "monitor:add")
            # param1 是服务器名
            if [ "$param1" != "$SERVER_NAME" ]; then
                return
            fi

            answer_callback "$callback_query_id" "选择要添加监控的容器"

            excluded=$(get_excluded_containers "$param1")
            if [ -z "$excluded" ]; then
                edit_message "$chat_id" "$message_id" "✅ 服务器 <code>$param1</code> 的所有容器都已在监控中"
                return
            fi

            buttons='{"inline_keyboard":['
            first=true
            for container in $excluded; do
                if [ "$first" = true ]; then
                    first=false
                else
                    buttons="$buttons,"
                fi
                buttons="$buttons[{\"text\":\"➕ $container\",\"callback_data\":\"add_monitor:$param1:$container\"}]"
            done
            buttons="$buttons"']}'

            edit_message "$chat_id" "$message_id" "服务器: <code>$param1</code>\n\n请选择要添加监控的容器：" "$buttons"
            ;;

        add_monitor)
            # param1 是服务器名，param2 是容器名
            if [ "$param1" != "$SERVER_NAME" ]; then
                return
            fi

            remove_from_excluded "$param2" "$param1"
            answer_callback "$callback_query_id" "已添加到监控列表"
            edit_message "$chat_id" "$message_id" "✅ 已将 <code>$param2</code> 添加到 <code>$param1</code> 的监控列表"
            ;;

        "monitor:remove")
            # param1 是服务器名
            if [ "$param1" != "$SERVER_NAME" ]; then
                return
            fi

            answer_callback "$callback_query_id" "选择要移除监控的容器"

            monitored=$(get_monitored_containers)
            if [ -z "$monitored" ]; then
                edit_message "$chat_id" "$message_id" "⚠️ 服务器 <code>$param1</code> 当前没有监控中的容器"
                return
            fi

            buttons='{"inline_keyboard":['
            first=true
            for container in $monitored; do
                if [ "$first" = true ]; then
                    first=false
                else
                    buttons="$buttons,"
                fi
                buttons="$buttons[{\"text\":\"➖ $container\",\"callback_data\":\"remove_monitor:$param1:$container\"}]"
            done
            buttons="$buttons"']}'

            edit_message "$chat_id" "$message_id" "服务器: <code>$param1</code>\n\n请选择要移除监控的容器：" "$buttons"
            ;;

        remove_monitor)
            # param1 是服务器名，param2 是容器名
            if [ "$param1" != "$SERVER_NAME" ]; then
                return
            fi

            add_to_excluded "$param2" "$param1"
            answer_callback "$callback_query_id" "已从监控列表移除"
            edit_message "$chat_id" "$message_id" "✅ 已将 <code>$param2</code> 从 <code>$param1</code> 的监控列表移除"
            ;;

        cancel)
            answer_callback "$callback_query_id" "已取消操作"
            edit_message "$chat_id" "$message_id" "❌ 操作已取消"
            ;;
    esac
}

# ==================== 机器人消息处理循环 ====================

bot_handler() {
    last_update_id=0

    while true; do
        updates=$(curl -s -X POST "$TELEGRAM_API/getUpdates" \
            --data-urlencode "offset=$((last_update_id + 1))" \
            --data-urlencode "timeout=30" \
            --connect-timeout 35 --max-time 40 2>/dev/null)

        if [ -z "$updates" ] || ! echo "$updates" | grep -q '"ok":true'; then
            sleep 5
            continue
        fi

        result_count=$(echo "$updates" | jq '.result | length' 2>/dev/null || echo "0")

        if [ "$result_count" -eq 0 ]; then
            continue
        fi

        i=0
        while [ $i -lt "$result_count" ]; do
            update=$(echo "$updates" | jq ".result[$i]" 2>/dev/null)
            update_id=$(echo "$update" | jq -r '.update_id' 2>/dev/null)

            if [ -n "$update_id" ] && [ "$update_id" != "null" ]; then
                last_update_id=$update_id
            fi

            # 处理命令消息
            message=$(echo "$update" | jq -r '.message.text' 2>/dev/null)
            chat_id=$(echo "$update" | jq -r '.message.chat.id' 2>/dev/null)
            message_id=$(echo "$update" | jq -r '.message.message_id' 2>/dev/null)

            if [ -n "$message" ] && [ "$message" != "null" ] && [ "$chat_id" = "$CHAT_ID" ]; then
                # 提取命令和参数
                cmd=$(echo "$message" | awk '{print $1}')
                param1=""
                param2=""

                # 检查是否有参数
                if echo "$message" | grep -q " "; then
                    # 提取第一个参数
                    param1=$(echo "$message" | awk '{print $2}')
                    # 提取第二个参数（如果存在）
                    if echo "$message" | awk '{print $3}' | grep -q "."; then
                        param2=$(echo "$message" | awk '{print $3}')
                    fi
                fi

                case "$cmd" in
                    /status) handle_status_command "$chat_id" "$param1" ;;
                    /update) handle_update_command "$chat_id" "$message_id" "$param1" "$param2" ;;
                    /restart) handle_restart_command "$chat_id" "$message_id" "$param1" "$param2" ;;
                    /monitor) handle_monitor_command "$chat_id" "$param1" ;;
                    /help) handle_help_command ;;
                    /start) handle_help_command ;;
                esac
            fi

            # 处理回调
            callback_query=$(echo "$update" | jq -r '.callback_query' 2>/dev/null)
            if [ -n "$callback_query" ] && [ "$callback_query" != "null" ]; then
                callback_data=$(echo "$callback_query" | jq -r '.data' 2>/dev/null)
                callback_query_id=$(echo "$callback_query" | jq -r '.id' 2>/dev/null)
                callback_chat_id=$(echo "$callback_query" | jq -r '.message.chat.id' 2>/dev/null)
                callback_message_id=$(echo "$callback_query" | jq -r '.message.message_id' 2>/dev/null)

                if [ "$callback_chat_id" = "$CHAT_ID" ]; then
                    handle_callback "$callback_data" "$callback_query_id" "$callback_chat_id" "$callback_message_id"
                fi
            fi

            i=$((i + 1))
        done

        sleep 1
    done
}

# ==================== 主程序 ====================

echo "=========================================="
echo "Docker 容器监控通知服务 v4.0.1"
echo "服务器: ${SERVER_NAME}"
echo "启动时间: $(get_time)"
echo "机器人: 已启用"
echo "=========================================="
echo ""

cleanup_old_states

# 启动机器人处理程序
echo "正在启动 Telegram 机器人..."
bot_handler &
BOT_PID=$!
echo $BOT_PID > "$BOT_PID_FILE"
echo "机器人已启动 (PID: $BOT_PID)"
echo ""

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

monitored_count=$(get_monitored_containers | wc -l)
excluded_count=$(get_excluded_containers | wc -l)
total_count=$(get_all_containers | wc -l)

echo "初始化完成，总计 ${total_count} 个容器 (监控: ${monitored_count}, 排除: ${excluded_count})"

sleep 3

monitored_containers=$(docker exec watchtower ps aux 2>/dev/null | \
    grep "watchtower" | \
    grep -v "grep" | \
    sed 's/.*watchtower//' | \
    tr ' ' '\n' | \
    grep -v "^$" | \
    grep -v "^--" | \
    tail -n +2 || true)

if [ -z "$monitored_containers" ]; then
    monitored_containers=$(docker container inspect watchtower --format='{{range .Args}}{{println .}}{{end}}' 2>/dev/null | \
        grep -v "^--" | \
        grep -v "^$" || true)
fi

if [ -n "$monitored_containers" ]; then
    container_count=$(echo "$monitored_containers" | wc -l)
    monitor_list="<b>Watchtower 监控:</b>"
    for c in $monitored_containers; do
        monitor_list="$monitor_list
   • <code>$c</code>"
    done
else
    container_count=$(docker ps --format '{{.Names}}' | grep -vE "^watchtower$|^watchtower-notifier$" | wc -l)
    monitor_list="<b>Watchtower 监控:</b> 全部容器"
fi

startup_message="🚀 <b>监控服务启动成功</b>

━━━━━━━━━━━━━━━━━━━━
📊 <b>服务信息</b>
   版本: <code>v4.0.1</code>
   服务器: <code>${SERVER_NAME}</code>

🎯 <b>监控状态</b>
   总容器: <code>${total_count}</code>
   监控中: <code>${monitored_count}</code>
   已排除: <code>${excluded_count}</code>

${monitor_list}

🤖 <b>机器人功能</b>
   /status [服务器] - 查看状态
   /update [服务器] [容器] - 更新
   /restart [服务器] [容器] - 重启
   /monitor [服务器] - 监控管理
   /help - 显示帮助

⏰ <b>启动时间</b>
   <code>$(get_time)</code>
━━━━━━━━━━━━━━━━━━━━

✅ 服务正常运行中"

send_telegram "$startup_message"

echo "开始监控 Watchtower 日志..."

cleanup() {
    echo "收到退出信号，正在清理..."
    if [ -f "$BOT_PID_FILE" ]; then
        bot_pid=$(cat "$BOT_PID_FILE")
        kill $bot_pid 2>/dev/null || true
        rm -f "$BOT_PID_FILE"
    fi
    rm -f /tmp/session_data.txt
    exit 0
}

trap cleanup INT TERM

# 主循环 - 监控 Watchtower 日志
docker logs -f --tail 0 watchtower 2>&1 | while IFS= read -r line; do
    echo "[$(date '+%H:%M:%S')] $line"

    if echo "$line" | grep -q "Stopping /"; then
        container_name=$(echo "$line" | sed -n 's/.*Stopping \/\([^ ]*\).*/\1/p' | head -n1)
        if [ -n "$container_name" ]; then
            # 检查容器是否在监控列表中
            if ! is_container_monitored "$container_name"; then
                echo "[$(date '+%H:%M:%S')] → $container_name 已被排除，跳过通知"
                continue
            fi

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

            echo "[$(date '+%H:%M:%S')] → 会话数据:"
            while IFS='|' read -r c_name old_tag old_id old_ver; do
                echo "[$(date '+%H:%M:%S')]     $c_name | $old_tag"
            done < /tmp/session_data.txt

            while IFS='|' read -r container_name old_tag_full old_id_full old_version_info; do
                [ -z "$container_name" ] && continue

                # 再次检查是否在监控列表中
                if ! is_container_monitored "$container_name"; then
                    echo "[$(date '+%H:%M:%S')] → $container_name 已被排除，跳过处理"
                    continue
                fi

                echo "[$(date '+%H:%M:%S')] → 处理容器: $container_name"
                echo "[$(date '+%H:%M:%S')]   → 等待容器更新完成..."
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
                        echo "[$(date '+%H:%M:%S')]   → 读取 danmu-api 版本..."
                        for retry in 1 2; do
                            for i in $(seq 1 30); do
                                if docker exec "$container_name" test -f /app/danmu_api/configs/globals.js 2>/dev/null; then
                                    break
                                fi
                                sleep 1
                            done

                            new_version_info=$(docker exec "$container_name" cat /app/danmu_api/configs/globals.js 2>/dev/null | \
                                             grep -m 1 "VERSION:" | sed -E "s/.*VERSION: '([^']+)'.*/\1/" 2>/dev/null || echo "")

                            if [ -n "$new_version_info" ]; then
                                echo "[$(date '+%H:%M:%S')]   → 检测到版本: v${new_version_info}"
                                break
                            elif [ $retry -eq 1 ]; then
                                echo "[$(date '+%H:%M:%S')]   → 首次读取失败，5秒后重试..."
                                sleep 5
                            fi
                        done
                    fi
                fi

                echo "$container_name|$new_tag_full|$new_id_full|$new_version_info|$(date +%s)" >> "$STATE_FILE"

                img_name=$(echo "$new_tag_full" | sed 's/:.*$//')
                time=$(date '+%Y-%m-%d %H:%M:%S')

                old_tag=$(echo "$old_tag_full" | grep -oE ':[^:]+ | sed 's/://' || echo "latest")
                new_tag=$(echo "$new_tag_full" | grep -oE ':[^:]+ | sed 's/://' || echo "latest")
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
            # 检查容器是否在监控列表中
            if is_container_monitored "$container_name"; then
                send_telegram "⚠️ <b>Watchtower 严重错误</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器</b>: <code>$container_name</code>
🔴 <b>错误</b>: <code>$error</code>
🕐 <b>时间</b>: <code>$(get_time)</code>
━━━━━━━━━━━━━━━━━━━━"
            fi
        fi
    fi
done

cleanup