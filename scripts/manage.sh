#!/bin/bash
# Docker 容器监控管理脚本 v3.3.0
cd "$(dirname "$0")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

if docker compose version &>/dev/null; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    echo -e "${RED}错误: 未找到 docker compose 或 docker-compose${NC}"
    exit 1
fi

show_menu() {
    clear
    cat << "EOF"
╔════════════════════════════════════════════════════╗
║                                                    ║
║       Docker 容器监控 - 管理菜单 v3.3.0            ║
║                                                    ║
╚════════════════════════════════════════════════════╝
EOF
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
    echo " 12) 清理状态数据库"
    echo ""
    echo -e "${CYAN}[系统操作]${NC}"
    echo " 13) 查看配置信息"
    echo " 14) 编辑监控容器列表"
    echo "  0) 退出"
    echo ""
    echo "════════════════════════════════════════════════════"
}

execute_action() {
    case $1 in
        1)
            echo -e "${BLUE}[操作] 启动服务...${NC}"
            $COMPOSE_CMD up -d && echo -e "${GREEN}✓ 服务已启动${NC}" || echo -e "${RED}✗ 启动失败${NC}"
            ;;
        2)
            echo -e "${BLUE}[操作] 停止服务...${NC}"
            $COMPOSE_CMD down && echo -e "${GREEN}✓ 服务已停止${NC}" || echo -e "${RED}✗ 停止失败${NC}"
            ;;
        3)
            echo -e "${BLUE}[操作] 重启服务...${NC}"
            $COMPOSE_CMD restart && echo -e "${GREEN}✓ 服务已重启${NC}" || echo -e "${RED}✗ 重启失败${NC}"
            ;;
        4)
            echo -e "${BLUE}[信息] 服务状态${NC}"
            echo ""
            $COMPOSE_CMD ps
            echo ""
            echo -e "${CYAN}健康状态:${NC}"
            docker inspect --format='{{.Name}}: {{if .State.Health}}{{.State.Health.Status}}{{else}}N/A{{end}}' watchtower watchtower-notifier 2>/dev/null | sed 's/\///g' || echo "无健康检查信息"
            ;;
        5)
            echo -e "${BLUE}[日志] 查看所有日志 (Ctrl+C 退出)${NC}"
            echo ""
            $COMPOSE_CMD logs -f
            ;;
        6)
            echo -e "${BLUE}[日志] 查看通知服务日志 (Ctrl+C 退出)${NC}"
            echo ""
            $COMPOSE_CMD logs -f watchtower-notifier
            ;;
        7)
            echo -e "${BLUE}[日志] 查看 Watchtower 日志 (Ctrl+C 退出)${NC}"
            echo ""
            $COMPOSE_CMD logs -f watchtower
            ;;
        8)
            echo -e "${BLUE}[操作] 更新服务镜像...${NC}"
            $COMPOSE_CMD pull && $COMPOSE_CMD up -d && echo -e "${GREEN}✓ 服务已更新${NC}" || echo -e "${RED}✗ 更新失败${NC}"
            ;;
        9)
            echo -e "${BLUE}[操作] 发送测试通知...${NC}"
            echo "将重启通知服务以触发启动通知"
            $COMPOSE_CMD restart watchtower-notifier
            echo -e "${GREEN}✓ 已触发重启，请稍候查看 Telegram${NC}"
            ;;
        10)
            echo -e "${BLUE}[信息] 详细健康检查${NC}"
            echo ""
            echo "═══ 容器运行状态 ═══"
            docker ps -a --filter "name=watchtower" --format "table {{.Names}}\t{{.Status}}\t{{.State}}"
            echo ""
            echo "═══ 健康检查结果 ═══"
            docker inspect --format='{{.Name}}: {{if .State.Health}}{{.State.Health.Status}}{{else}}N/A{{end}}' watchtower watchtower-notifier 2>/dev/null | sed 's/\///g'
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
            BACKUP_DIR="./backups/$(date +%Y%m%d_%H%M%S)"
            echo -e "${BLUE}[操作] 备份配置文件到 $BACKUP_DIR${NC}"
            mkdir -p "$BACKUP_DIR"
            cp docker-compose.yml "$BACKUP_DIR/" 2>/dev/null
            cp .env "$BACKUP_DIR/" 2>/dev/null
            cp monitor.sh "$BACKUP_DIR/" 2>/dev/null
            [ -f data/container_state.db ] && cp data/container_state.db "$BACKUP_DIR/"
            echo -e "${GREEN}✓ 配置已备份${NC}"
            ;;
        12)
            echo -e "${YELLOW}[警告] 这将清除容器状态历史记录${NC}"
            read -p "确认清理? (y/n): " confirm
            if [[ "$confirm" =~ ^[Yy]$ ]]; then
                rm -f data/container_state.db
                echo -e "${GREEN}✓ 状态数据库已清理${NC}"
            else
                echo "已取消"
            fi
            ;;
        13)
            echo -e "${BLUE}[信息] 当前配置${NC}"
            echo ""
            if [ -f .env ]; then
                echo "═══ 监控配置 ═══"
                grep -E "^(SERVER_NAME|POLL_INTERVAL|CLEANUP|ENABLE_ROLLBACK)=" .env | while read line; do
                    key=$(echo "$line" | cut -d= -f1)
                    val=$(echo "$line" | cut -d= -f2)
                    case $key in
                        POLL_INTERVAL)
                            mins=$((val / 60))
                            echo "检查间隔: ${mins} 分钟 (${val}秒)"
                            ;;
                        SERVER_NAME)
                            echo "服务器名称: ${val:-未设置}"
                            ;;
                        CLEANUP)
                            echo "自动清理: $val"
                            ;;
                        ENABLE_ROLLBACK)
                            echo "自动回滚: $val"
                            ;;
                    esac
                done
                echo ""
                echo "═══ 监控容器 ═══"
                if grep -q "command:" docker-compose.yml; then
                    echo "监控特定容器:"
                    grep -A 10 "command:" docker-compose.yml | grep "^      -" | sed 's/      - /  • /'
                else
                    echo "监控所有容器"
                fi
                echo ""
                echo "═══ 状态数据库 ═══"
                if [ -f data/container_state.db ]; then
                    local count=$(wc -l < data/container_state.db 2>/dev/null || echo 0)
                    echo "记录数: $count"
                else
                    echo "状态数据库: 未初始化"
                fi
            else
                echo -e "${RED}未找到配置文件${NC}"
            fi
            ;;
        14)
            echo -e "${BLUE}[操作] 编辑监控容器列表${NC}"
            echo ""
            echo "当前运行的容器:"
            docker ps --format "  • {{.Names}} [{{.Image}}]"
            echo ""
            echo "当前监控配置:"
            if grep -q "command:" docker-compose.yml; then
                grep -A 10 "command:" docker-compose.yml | grep "^      -" | sed 's/      - /  • /'
                echo ""
                echo "修改方法:"
                echo "1. 编辑 docker-compose.yml"
                echo "2. 找到 watchtower 服务的 command 部分"
                echo "3. 添加或删除容器名称"
                echo "4. 运行选项 3 (重启服务)"
            else
                echo "当前监控所有容器"
                echo ""
                echo "如需改为监控特定容器:"
                echo "1. 编辑 docker-compose.yml"
                echo "2. 在 watchtower 服务下添加:"
                echo "   command:"
                echo "     - 容器名1"
                echo "     - 容器名2"
                echo "3. 运行选项 3 (重启服务)"
            fi
            echo ""
            read -p "是否现在编辑配置文件? (y/n): " edit
            if [[ "$edit" =~ ^[Yy]$ ]]; then
                ${EDITOR:-vi} docker-compose.yml
                echo ""
                read -p "是否重启服务以应用更改? (y/n): " restart
                if [[ "$restart" =~ ^[Yy]$ ]]; then
                    $COMPOSE_CMD restart
                    echo -e "${GREEN}✓ 服务已重启${NC}"
                fi
            fi
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
                if [ "$2" = "notifier" ]; then
                    execute_action 6
                elif [ "$2" = "watchtower" ]; then
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
                echo "或运行 $0 进入交互式菜单"
                exit 1
                ;;
        esac
        exit 0
    fi
    
    while true; do
        show_menu
        read -p "请选择操作 [0-14]: " choice
        echo ""
        execute_action "$choice"
        echo ""
        read -p "按回车键继续..."
    done
}

main "$@"
