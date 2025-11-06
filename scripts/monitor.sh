#!/bin/sh
# Docker 容器监控通知服务 v3.3.0
# 监控 Watchtower 日志并发送 Telegram 通知

echo "正在安装依赖..."
apk add --no-cache curl docker-cli coreutils grep sed tzdata jq >/dev/null 2>&1

TELEGRAM_API="https://api.telegram.org/bot${BOT_TOKEN}/sendMessage"
STATE_FILE="/data/container_state.db"
TEMP_LOG="/tmp/watchtower_events.log"

# 确保数据目录存在
mkdir -p /data

if [ -n "$SERVER_NAME" ]; then
    SERVER_TAG="<b>[${SERVER_NAME}]</b> "
else
    SERVER_TAG=""
fi

send_telegram() {
    message="$1"
    max_retries=3
    retry=0
    wait_time=5

    while [ $retry -lt $max_retries ]; do
        response=$(curl -s -w "\n%{http_code}" -X POST "$TELEGRAM_API" \
            --data-urlencode "chat_id=${CHAT_ID}" \
            --data-urlencode "text=${SERVER_TAG}${message}" \
            --data-urlencode "parse_mode=HTML" \
            --connect-timeout 10 --max-time 30 2>&1)
        
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
                
                if echo "$error_desc" | grep -qiE "chat not found|bot was blocked|user is deactivated"; then
                    echo "  ✗ 致命错误，停止重试" >&2
                    return 1
                fi
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

get_time() { date '+%Y-%m-%d %H:%M:%S'; }
get_image_name() { echo "$1" | sed 's/:.*$//'; }
get_short_id() { echo "$1" | sed 's/sha256://' | head -c 12 || echo "unknown"; }

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

rollback_container() {
    container="$1"
    old_tag="$2"
    old_id="$3"

    echo "  → 正在执行回滚操作..."

    config=$(docker inspect "$container" 2>/dev/null)
    if [ -z "$config" ]; then
        echo "  ✗ 无法获取容器配置，回滚失败"
        return 1
    fi

    docker stop "$container" >/dev/null 2>&1 || true
    docker rm "$container" >/dev/null 2>&1 || true

    echo "  → 尝试使用旧镜像 $old_id 重启容器..."

    docker tag "$old_id" "${old_tag}-rollback" 2>/dev/null || {
        echo "  ✗ 旧镜像不存在，无法回滚"
        return 1
    }

    echo "  ✓ 回滚操作已触发，请手动检查容器状态"
    return 0
}

cleanup_old_states() {
    if [ ! -f "$STATE_FILE" ]; then
        return
    fi

    cutoff_time=$(( $(date +%s) - 604800 ))  # 7天前
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
        mv "$temp_file" "$STATE_FILE" 2>/dev/null || {
            echo "  ✗ 无法更新状态文件" >&2
            rm -f "$temp_file"
        }
    fi
}

echo "=========================================="
echo "Docker 容器监控通知服务 v3.3.0"
echo "服务器: ${SERVER_NAME:-N/A}"
echo "启动时间: $(get_time)"
echo "回滚功能: ${ENABLE_ROLLBACK:-false}"
echo "=========================================="
echo ""

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
    monitor_list="<b>监控容器列表:</b>"
    for c in $monitored_containers; do
        monitor_list="$monitor_list
   • <code>$c</code>"
    done
else
    container_count=$(docker ps --format '{{.Names}}' | grep -vE "^watchtower$|^watchtower-notifier$" | wc -l)
    monitor_list="<b>监控范围:</b> 全部容器"
fi

startup_message="🚀 <b>监控服务启动成功</b>

━━━━━━━━━━━━━━━━━━━━
📊 <b>服务信息</b>
   版本: <code>v3.3.0</code>

🎯 <b>监控状态</b>
   容器数: <code>${container_count}</code>
   状态库: <code>已初始化</code>

${monitor_list}

🔄 <b>功能配置</b>
   自动回滚: <code>${ENABLE_ROLLBACK:-禁用}</code>
   检查间隔: <code>$((POLL_INTERVAL / 60))分钟</code>

⏰ <b>启动时间</b>
   <code>$(get_time)</code>
━━━━━━━━━━━━━━━━━━━━

✅ 服务正常运行中"

send_telegram "$startup_message"

echo "开始监控 Watchtower 日志..."

cleanup() {
    echo "收到退出信号，正在清理..."
    rm -f /tmp/session_data.txt
    exit 0
}

trap cleanup INT TERM

# 主循环 - 直接处理，不使用管道
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
            
            echo "[$(date '+%H:%M:%S')] → 会话数据:"
            while IFS='|' read -r c_name old_tag old_id old_ver; do
                echo "[$(date '+%H:%M:%S')]     $c_name | $old_tag"
            done < /tmp/session_data.txt
            
            while IFS='|' read -r container_name old_tag_full old_id_full old_version_info; do
                [ -z "$container_name" ] && continue
                
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
            send_telegram "⚠️ <b>Watchtower 严重错误</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器</b>: <code>$container_name</code>
🔴 <b>错误</b>: <code>$error</code>
🕐 <b>时间</b>: <code>$(get_time)</code>
━━━━━━━━━━━━━━━━━━━━"
        fi
    fi
done

cleanup
