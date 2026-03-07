#!/bin/bash
# Watchtower 监控服务管理脚本

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CURRENT_DIR="$(pwd)"

if [ -n "${WATCHTOWER_DEPLOY_DIR:-}" ] && [ -f "${WATCHTOWER_DEPLOY_DIR}/docker-compose.yml" ]; then
    DEPLOY_DIR="$WATCHTOWER_DEPLOY_DIR"
elif [ -f "$CURRENT_DIR/docker-compose.yml" ]; then
    DEPLOY_DIR="$CURRENT_DIR"
elif [ -f "$REPO_DIR/docker/docker-compose.yml" ]; then
    DEPLOY_DIR="$REPO_DIR/docker"
else
    echo -e "${RED}错误: 未找到 docker-compose.yml${NC}"
    exit 1
fi

if [ -f "$DEPLOY_DIR/.env" ]; then
    ENV_FILE="$DEPLOY_DIR/.env"
elif [ -f "$REPO_DIR/config/.env" ]; then
    ENV_FILE="$REPO_DIR/config/.env"
else
    ENV_FILE="$DEPLOY_DIR/.env"
fi

COMPOSE_FILE="$DEPLOY_DIR/docker-compose.yml"
DATA_DIR="$DEPLOY_DIR/data"
BACKUP_ROOT="$DEPLOY_DIR/backups"

if docker compose version &>/dev/null; then
    COMPOSE_MODE="docker-compose-v2"
elif command -v docker-compose &>/dev/null; then
    COMPOSE_MODE="docker-compose-v1"
else
    echo -e "${RED}错误: 未找到 docker compose 或 docker-compose${NC}"
    exit 1
fi

compose() {
    if [ "$COMPOSE_MODE" = "docker-compose-v2" ]; then
        if [ -f "$ENV_FILE" ]; then
            docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
        else
            docker compose -f "$COMPOSE_FILE" "$@"
        fi
    else
        (
            if [ -f "$ENV_FILE" ]; then
                set -a
                . "$ENV_FILE"
                set +a
            fi
            docker-compose -f "$COMPOSE_FILE" "$@"
        )
    fi
}

show_menu() {
    clear
    cat << "MENU"
╔════════════════════════════════════════════════════╗
║                                                    ║
║        Watchtower 监控服务管理菜单                 ║
║                                                    ║
╚════════════════════════════════════════════════════╝
MENU
    echo ""
    echo -e "${CYAN}[服务管理]${NC}"
    echo "  1) 启动服务"
    echo "  2) 停止服务"
    echo "  3) 重启服务"
    echo "  4) 查看状态"
    echo ""
    echo -e "${CYAN}[日志查看]${NC}"
    echo "  5) 查看所有日志"
    echo "  6) 查看通知服务日志"
    echo "  7) 查看 Watchtower 日志"
    echo ""
    echo -e "${CYAN}[维护操作]${NC}"
    echo "  8) 更新服务镜像"
    echo "  9) 发送测试通知"
    echo " 10) 详细健康检查"
    echo " 11) 备份配置文件"
    echo " 12) 清理状态文件"
    echo ""
    echo -e "${CYAN}[系统操作]${NC}"
    echo " 13) 查看配置信息"
    echo " 14) 编辑配置文件"
    echo "  0) 退出"
    echo ""
    echo "════════════════════════════════════════════════════"
}

print_monitor_mode() {
    if [ -f "$ENV_FILE" ] && grep -q '^MONITORED_CONTAINERS=' "$ENV_FILE"; then
        monitored=$(grep '^MONITORED_CONTAINERS=' "$ENV_FILE" | cut -d= -f2-)
        if [ -n "$monitored" ]; then
            echo "监控模式: 固定名单"
            echo "固定名单: $monitored"
            return
        fi
    fi
    echo "监控模式: 默认监控全部容器"
}

backup_files() {
    BACKUP_DIR="$BACKUP_ROOT/$(date +%Y%m%d_%H%M%S)"
    echo -e "${BLUE}[操作] 备份配置文件到 $BACKUP_DIR${NC}"
    mkdir -p "$BACKUP_DIR"
    [ -f "$COMPOSE_FILE" ] && cp "$COMPOSE_FILE" "$BACKUP_DIR/"
    [ -f "$ENV_FILE" ] && cp "$ENV_FILE" "$BACKUP_DIR/"
    [ -f "$SCRIPT_DIR/manage.sh" ] && cp "$SCRIPT_DIR/manage.sh" "$BACKUP_DIR/"
    [ -f "$DATA_DIR/monitor_config.json" ] && cp "$DATA_DIR/monitor_config.json" "$BACKUP_DIR/"
    [ -f "$DATA_DIR/server_registry.json" ] && cp "$DATA_DIR/server_registry.json" "$BACKUP_DIR/"
    [ -f "$DATA_DIR/health_status.json" ] && cp "$DATA_DIR/health_status.json" "$BACKUP_DIR/"
    echo -e "${GREEN}✓ 配置已备份${NC}"
}

clean_state_files() {
    echo -e "${YELLOW}[警告] 这将清理 monitor_config.json、server_registry.json、health_status.json${NC}"
    read -r -p "确认清理? (y/n): " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        rm -f "$DATA_DIR/monitor_config.json" "$DATA_DIR/server_registry.json" "$DATA_DIR/health_status.json"
        echo -e "${GREEN}✓ 状态文件已清理${NC}"
    else
        echo "已取消"
    fi
}

show_config() {
    echo -e "${BLUE}[信息] 当前配置${NC}"
    echo ""
    echo "部署目录: $DEPLOY_DIR"
    echo "Compose 文件: $COMPOSE_FILE"
    echo "环境文件: $ENV_FILE"
    echo ""

    if [ -f "$ENV_FILE" ]; then
        echo "═══ 监控配置 ═══"
        grep -E '^(SERVER_NAME|PRIMARY_SERVER|POLL_INTERVAL|CLEANUP|ENABLE_ROLLBACK|MONITORED_CONTAINERS)=' "$ENV_FILE" || true
        echo ""
        print_monitor_mode
        echo ""
    else
        echo -e "${YELLOW}未找到环境变量文件${NC}"
        echo ""
    fi

    echo "═══ 状态文件 ═══"
    for file in monitor_config.json server_registry.json health_status.json; do
        if [ -f "$DATA_DIR/$file" ]; then
            size=$(du -h "$DATA_DIR/$file" | cut -f1)
            echo "$file: $size"
        else
            echo "$file: 未初始化"
        fi
    done
}

edit_config() {
    echo -e "${BLUE}[操作] 编辑配置文件${NC}"
    echo "1. Compose: $COMPOSE_FILE"
    echo "2. 环境变量: $ENV_FILE"
    read -r -p "编辑 Compose 还是 .env? (c/e): " target
    case "$target" in
        c|C)
            ${EDITOR:-vi} "$COMPOSE_FILE"
            ;;
        e|E)
            ${EDITOR:-vi} "$ENV_FILE"
            ;;
        *)
            echo "已取消"
            return
            ;;
    esac

    echo ""
    read -r -p "是否重启服务以应用更改? (y/n): " restart
    if [[ "$restart" =~ ^[Yy]$ ]]; then
        compose restart
        echo -e "${GREEN}✓ 服务已重启${NC}"
    fi
}

execute_action() {
    case "$1" in
        1)
            echo -e "${BLUE}[操作] 启动服务...${NC}"
            compose up -d && echo -e "${GREEN}✓ 服务已启动${NC}" || echo -e "${RED}✗ 启动失败${NC}"
            ;;
        2)
            echo -e "${BLUE}[操作] 停止服务...${NC}"
            compose down && echo -e "${GREEN}✓ 服务已停止${NC}" || echo -e "${RED}✗ 停止失败${NC}"
            ;;
        3)
            echo -e "${BLUE}[操作] 重启服务...${NC}"
            compose restart && echo -e "${GREEN}✓ 服务已重启${NC}" || echo -e "${RED}✗ 重启失败${NC}"
            ;;
        4)
            echo -e "${BLUE}[信息] 服务状态${NC}"
            echo ""
            compose ps
            echo ""
            echo -e "${CYAN}健康状态:${NC}"
            docker inspect --format='{{.Name}}: {{if .State.Health}}{{.State.Health.Status}}{{else}}N/A{{end}}' watchtower watchtower-notifier 2>/dev/null | sed 's#^/##' || echo "无健康检查信息"
            ;;
        5)
            echo -e "${BLUE}[日志] 查看所有日志 (Ctrl+C 退出)${NC}"
            echo ""
            compose logs -f
            ;;
        6)
            echo -e "${BLUE}[日志] 查看通知服务日志 (Ctrl+C 退出)${NC}"
            echo ""
            compose logs -f watchtower-notifier
            ;;
        7)
            echo -e "${BLUE}[日志] 查看 Watchtower 日志 (Ctrl+C 退出)${NC}"
            echo ""
            compose logs -f watchtower
            ;;
        8)
            echo -e "${BLUE}[操作] 更新服务镜像...${NC}"
            compose pull && compose up -d && echo -e "${GREEN}✓ 服务已更新${NC}" || echo -e "${RED}✗ 更新失败${NC}"
            ;;
        9)
            echo -e "${BLUE}[操作] 发送测试通知...${NC}"
            compose restart watchtower-notifier
            echo -e "${GREEN}✓ 已触发重启，请稍候查看 Telegram${NC}"
            ;;
        10)
            echo -e "${BLUE}[信息] 详细健康检查${NC}"
            echo ""
            echo "═══ 容器运行状态 ═══"
            docker ps -a --filter "name=watchtower" --format "table {{.Names}}\t{{.Status}}\t{{.State}}"
            echo ""
            echo "═══ 健康检查结果 ═══"
            docker inspect --format='{{.Name}}: {{if .State.Health}}{{.State.Health.Status}}{{else}}N/A{{end}}' watchtower watchtower-notifier 2>/dev/null | sed 's#^/##'
            echo ""
            echo "═══ 资源使用情况 ═══"
            docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" watchtower watchtower-notifier
            echo ""
            echo "═══ 最近日志 (最后20行) ═══"
            echo -e "${CYAN}Watchtower:${NC}"
            docker logs --tail 20 watchtower 2>&1 | tail -10
            echo ""
            echo -e "${CYAN}Notifier:${NC}"
            docker logs --tail 20 watchtower-notifier 2>&1 | tail -10
            ;;
        11)
            backup_files
            ;;
        12)
            clean_state_files
            ;;
        13)
            show_config
            ;;
        14)
            edit_config
            ;;
        0)
            echo "退出管理菜单"
            exit 0
            ;;
        *)
            echo -e "${RED}无效选项${NC}"
            ;;
    esac
}

main() {
    if [ $# -gt 0 ]; then
        case "$1" in
            start)   execute_action 1 ;;
            stop)    execute_action 2 ;;
            restart) execute_action 3 ;;
            status)  execute_action 4 ;;
            logs)
                if [ "${2:-}" = "notifier" ]; then
                    execute_action 6
                elif [ "${2:-}" = "watchtower" ]; then
                    execute_action 7
                else
                    execute_action 5
                fi
                ;;
            update)  execute_action 8 ;;
            test)    execute_action 9 ;;
            health)  execute_action 10 ;;
            backup)  execute_action 11 ;;
            clean)   execute_action 12 ;;
            config)  execute_action 13 ;;
            edit)    execute_action 14 ;;
            *)
                echo "用法: $0 {start|stop|restart|status|logs|update|test|health|backup|clean|config|edit}"
                exit 1
                ;;
        esac
        exit 0
    fi

    while true; do
        show_menu
        read -r -p "请选择操作 [0-14]: " choice
        echo ""
        execute_action "$choice"
        echo ""
        read -r -p "按回车键继续..."
    done
}

main "$@"
