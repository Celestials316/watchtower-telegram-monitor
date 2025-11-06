#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Docker 容器监控通知服务 v5.0.0
支持多服务器管理、Telegram Bot 交互
"""

import os
import sys
import json
import time
import signal
import subprocess
import threading
import logging
from datetime import datetime
from typing import Dict, List, Optional, Set
import requests
from pathlib import Path

# ==================== 配置和常量 ====================

VERSION = "5.0.0"
TELEGRAM_API = f"https://api.telegram.org/bot{os.getenv('BOT_TOKEN')}"
CHAT_ID = os.getenv('CHAT_ID')
SERVER_NAME = os.getenv('SERVER_NAME')

# 文件路径
DATA_DIR = Path("/data")
STATE_FILE = DATA_DIR / "container_state.json"
MONITOR_CONFIG = DATA_DIR / "monitor_config.json"
SERVER_REGISTRY = DATA_DIR / "server_registry.json"

# 确保数据目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# 全局变量
shutdown_flag = threading.Event()


# ==================== 工具类 ====================

class TelegramBot:
    """Telegram Bot API 封装"""
    
    def __init__(self, token: str, chat_id: str, server_name: str):
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.server_tag = f"<b>[{server_name}]</b> "
        self.session = requests.Session()
        self.session.headers.update({'Connection': 'keep-alive'})
    
    def send_message(self, text: str, reply_markup: Optional[Dict] = None, 
                     max_retries: int = 3) -> bool:
        """发送 Telegram 消息"""
        for attempt in range(max_retries):
            try:
                payload = {
                    'chat_id': self.chat_id,
                    'text': self.server_tag + text,
                    'parse_mode': 'HTML'
                }
                if reply_markup:
                    payload['reply_markup'] = json.dumps(reply_markup)
                
                response = self.session.post(
                    f"{self.api_url}/sendMessage",
                    json=payload,
                    timeout=30
                )
                
                if response.status_code == 200 and response.json().get('ok'):
                    logger.info("✓ Telegram 消息发送成功")
                    return True
                else:
                    error_desc = response.json().get('description', '未知错误')
                    logger.error(f"✗ Telegram API 错误: {error_desc}")
                    
            except Exception as e:
                logger.error(f"✗ 发送失败: {e}")
            
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 5
                logger.info(f"↻ {wait_time}秒后重试 ({attempt + 1}/{max_retries})...")
                time.sleep(wait_time)
        
        logger.error(f"✗ Telegram 消息最终失败 (已重试 {max_retries} 次)")
        return False
    
    def edit_message(self, chat_id: str, message_id: str, text: str, 
                     reply_markup: Optional[Dict] = None) -> bool:
        """编辑消息"""
        try:
            payload = {
                'chat_id': chat_id,
                'message_id': message_id,
                'text': text,
                'parse_mode': 'HTML'
            }
            if reply_markup:
                payload['reply_markup'] = json.dumps(reply_markup)
            
            response = self.session.post(
                f"{self.api_url}/editMessageText",
                json=payload,
                timeout=30
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"编辑消息失败: {e}")
            return False
    
    def answer_callback(self, callback_query_id: str, text: str) -> bool:
        """回应回调查询"""
        try:
            response = self.session.post(
                f"{self.api_url}/answerCallbackQuery",
                json={'callback_query_id': callback_query_id, 'text': text},
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"回应回调失败: {e}")
            return False
    
    def get_updates(self, offset: int = 0, timeout: int = 30) -> Optional[List]:
        """获取更新"""
        try:
            response = self.session.post(
                f"{self.api_url}/getUpdates",
                json={'offset': offset, 'timeout': timeout},
                timeout=timeout + 10
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    return data.get('result', [])
        except Exception as e:
            logger.debug(f"获取更新失败: {e}")
        return None


class DockerManager:
    """Docker 容器管理"""
    
    @staticmethod
    def get_all_containers() -> List[str]:
        """获取所有容器（排除监控相关容器）"""
        try:
            result = subprocess.run(
                ['docker', 'ps', '--format', '{{.Names}}'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                containers = result.stdout.strip().split('\n')
                return [c for c in containers 
                       if c and c not in ['watchtower', 'watchtower-notifier']]
        except Exception as e:
            logger.error(f"获取容器列表失败: {e}")
        return []
    
    @staticmethod
    def get_container_info(container: str) -> Dict:
        """获取容器详细信息"""
        try:
            result = subprocess.run(
                ['docker', 'inspect', container],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if data:
                    info = data[0]
                    return {
                        'name': container,
                        'running': info['State']['Running'],
                        'image': info['Config']['Image'],
                        'image_id': info['Image'],
                        'created': info['Created']
                    }
        except Exception as e:
            logger.error(f"获取容器 {container} 信息失败: {e}")
        return {}
    
    @staticmethod
    def restart_container(container: str) -> bool:
        """重启容器"""
        try:
            result = subprocess.run(
                ['docker', 'restart', container],
                capture_output=True, text=True, timeout=60
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"重启容器 {container} 失败: {e}")
            return False
    
    @staticmethod
    def get_danmu_version(container: str) -> Optional[str]:
        """获取 danmu-api 版本"""
        if 'danmu' not in container.lower():
            return None
        
        try:
            # 等待容器就绪
            for _ in range(30):
                check = subprocess.run(
                    ['docker', 'exec', container, 'test', '-f', 
                     '/app/danmu_api/configs/globals.js'],
                    capture_output=True, timeout=5
                )
                if check.returncode == 0:
                    break
                time.sleep(1)
            
            # 读取版本
            result = subprocess.run(
                ['docker', 'exec', container, 'cat', 
                 '/app/danmu_api/configs/globals.js'],
                capture_output=True, text=True, timeout=10
            )
            
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'VERSION:' in line:
                        import re
                        match = re.search(r"VERSION:\s*['\"]([^'\"]+)['\"]", line)
                        if match:
                            return match.group(1)
        except Exception as e:
            logger.debug(f"获取 danmu 版本失败: {e}")
        
        return None


class ConfigManager:
    """配置管理器"""
    
    def __init__(self, config_file: Path, server_name: str):
        self.config_file = config_file
        self.server_name = server_name
        self.config = self._load_config()
    
    def _load_config(self) -> Dict:
        """加载配置"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载配置失败: {e}")
        return {}
    
    def _save_config(self):
        """保存配置"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
    
    def get_excluded_containers(self, server: Optional[str] = None) -> Set[str]:
        """获取排除的容器列表"""
        server = server or self.server_name
        return set(self.config.get(server, {}).get('excluded', []))
    
    def add_excluded(self, container: str, server: Optional[str] = None):
        """添加到排除列表"""
        server = server or self.server_name
        if server not in self.config:
            self.config[server] = {'excluded': []}
        
        excluded = set(self.config[server].get('excluded', []))
        excluded.add(container)
        self.config[server]['excluded'] = sorted(list(excluded))
        self._save_config()
    
    def remove_excluded(self, container: str, server: Optional[str] = None):
        """从排除列表移除"""
        server = server or self.server_name
        if server in self.config:
            excluded = set(self.config[server].get('excluded', []))
            excluded.discard(container)
            self.config[server]['excluded'] = sorted(list(excluded))
            self._save_config()
    
    def is_monitored(self, container: str, server: Optional[str] = None) -> bool:
        """检查容器是否被监控"""
        return container not in self.get_excluded_containers(server)


class ServerRegistry:
    """服务器注册中心 - 使用文件实现服务发现"""
    
    def __init__(self, registry_file: Path, server_name: str):
        self.registry_file = registry_file
        self.server_name = server_name
        self.heartbeat_interval = 30  # 心跳间隔（秒）
        self.timeout = 90  # 超时时间（秒）
    
    def register(self):
        """注册当前服务器"""
        registry = self._load_registry()
        registry[self.server_name] = {
            'last_heartbeat': time.time(),
            'version': VERSION
        }
        self._save_registry(registry)
        logger.info(f"服务器已注册: {self.server_name}")
    
    def heartbeat(self):
        """发送心跳"""
        registry = self._load_registry()
        if self.server_name in registry:
            registry[self.server_name]['last_heartbeat'] = time.time()
            self._save_registry(registry)
    
    def get_active_servers(self) -> List[str]:
        """获取活跃的服务器列表"""
        registry = self._load_registry()
        current_time = time.time()
        active_servers = []
        
        for server, info in registry.items():
            if current_time - info.get('last_heartbeat', 0) < self.timeout:
                active_servers.append(server)
        
        return sorted(active_servers)
    
    def _load_registry(self) -> Dict:
        """加载注册表"""
        if self.registry_file.exists():
            try:
                with open(self.registry_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载注册表失败: {e}")
        return {}
    
    def _save_registry(self, registry: Dict):
        """保存注册表"""
        try:
            with open(self.registry_file, 'w', encoding='utf-8') as f:
                json.dump(registry, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存注册表失败: {e}")


# ==================== 命令处理器 ====================

class CommandHandler:
    """命令处理器"""
    
    def __init__(self, bot: TelegramBot, docker: DockerManager, 
                 config: ConfigManager, registry: ServerRegistry):
        self.bot = bot
        self.docker = docker
        self.config = config
        self.registry = registry
    
    def handle_status(self, chat_id: str):
        """处理 /status 命令"""
        all_containers = self.docker.get_all_containers()
        monitored = [c for c in all_containers if self.config.is_monitored(c)]
        excluded = self.config.get_excluded_containers()
        
        status_msg = f"""📊 <b>服务器状态</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ <b>服务器信息</b>
   名称: <code>{SERVER_NAME}</code>
   时间: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
   版本: <code>v{VERSION}</code>

📦 <b>容器统计</b>
   总计: <code>{len(all_containers)}</code>
   监控中: <code>{len(monitored)}</code>
   已排除: <code>{len(excluded)}</code>

🔍 <b>监控列表</b>"""
        
        if not monitored:
            status_msg += "\n   <i>暂无监控容器</i>"
        else:
            for container in monitored:
                info = self.docker.get_container_info(container)
                status_icon = "✅" if info.get('running') else "❌"
                tag = info.get('image', '').split(':')[-1] or 'latest'
                status_msg += f"\n   {status_icon} <code>{container}</code> [{tag}]"
        
        if excluded:
            status_msg += "\n\n🚫 <b>排除列表</b>"
            for container in sorted(excluded):
                status_msg += f"\n   • <code>{container}</code>"
        
        status_msg += "\n━━━━━━━━━━━━━━━━━━━━"
        self.bot.send_message(status_msg)
    
    def handle_update(self, chat_id: str):
        """处理 /update 命令"""
        servers = self.registry.get_active_servers()
        
        if not servers:
            self.bot.send_message("⚠️ 没有可用的服务器")
            return
        
        if len(servers) == 1:
            # 单服务器直接显示容器列表
            self._show_update_containers(chat_id, servers[0])
        else:
            # 多服务器选择
            buttons = {
                'inline_keyboard': [
                    [{'text': f"🖥️ {srv}", 'callback_data': f"update_srv:{srv}"}]
                    for srv in servers
                ]
            }
            self.bot.send_message("🔄 <b>选择要更新容器的服务器：</b>", buttons)
    
    def _show_update_containers(self, chat_id: str, server: str):
        """显示可更新的容器列表"""
        if server == SERVER_NAME:
            containers = [c for c in self.docker.get_all_containers() 
                         if self.config.is_monitored(c)]
        else:
            # 跨服务器操作需要提示
            self.bot.send_message(
                f"⚠️ 无法直接操作服务器 <code>{server}</code>\n"
                f"请在对应服务器上执行操作"
            )
            return
        
        if not containers:
            self.bot.send_message(f"⚠️ 服务器 <code>{server}</code> 没有可更新的容器")
            return
        
        buttons = {
            'inline_keyboard': [
                [{'text': f"📦 {c}", 'callback_data': f"update_cnt:{server}:{c}"}]
                for c in containers
            ]
        }
        self.bot.send_message(
            f"🔄 <b>服务器 <code>{server}</code></b>\n\n请选择要更新的容器：",
            buttons
        )
    
    def handle_restart(self, chat_id: str):
        """处理 /restart 命令"""
        servers = self.registry.get_active_servers()
        
        if not servers:
            self.bot.send_message("⚠️ 没有可用的服务器")
            return
        
        if len(servers) == 1:
            self._show_restart_containers(chat_id, servers[0])
        else:
            buttons = {
                'inline_keyboard': [
                    [{'text': f"🖥️ {srv}", 'callback_data': f"restart_srv:{srv}"}]
                    for srv in servers
                ]
            }
            self.bot.send_message("🔄 <b>选择要重启容器的服务器：</b>", buttons)
    
    def _show_restart_containers(self, chat_id: str, server: str):
        """显示可重启的容器列表"""
        if server == SERVER_NAME:
            containers = self.docker.get_all_containers()
        else:
            self.bot.send_message(
                f"⚠️ 无法直接操作服务器 <code>{server}</code>\n"
                f"请在对应服务器上执行操作"
            )
            return
        
        if not containers:
            self.bot.send_message(f"⚠️ 服务器 <code>{server}</code> 没有可重启的容器")
            return
        
        buttons = {
            'inline_keyboard': [
                [{'text': f"🔄 {c}", 'callback_data': f"restart_cnt:{server}:{c}"}]
                for c in containers
            ]
        }
        self.bot.send_message(
            f"🔄 <b>服务器 <code>{server}</code></b>\n\n请选择要重启的容器：",
            buttons
        )
    
    def handle_monitor(self, chat_id: str):
        """处理 /monitor 命令"""
        buttons = {
            'inline_keyboard': [
                [{'text': "➕ 添加监控", 'callback_data': "monitor_action:add"}],
                [{'text': "➖ 移除监控", 'callback_data': "monitor_action:remove"}],
                [{'text': "📋 查看列表", 'callback_data': "monitor_action:list"}]
            ]
        }
        self.bot.send_message("📡 <b>监控管理</b>\n\n请选择操作：", buttons)
    
    def handle_help(self):
        """处理 /help 命令"""
        servers = self.registry.get_active_servers()
        server_list = "\n".join([f"   • <code>{s}</code>" for s in servers])
        
        help_msg = f"""📖 <b>命令帮助</b>

━━━━━━━━━━━━━━━━━━━━
<b>可用命令：</b>

/status - 查看当前服务器状态
/update - 更新容器镜像
/restart - 重启容器
/monitor - 监控管理
/help - 显示此帮助信息

━━━━━━━━━━━━━━━━━━━━
<b>🌐 已连接服务器 ({len(servers)})：</b>
{server_list if servers else '   <i>暂无服务器</i>'}

━━━━━━━━━━━━━━━━━━━━
💡 <b>使用提示：</b>

• 所有操作通过按钮选择
• 多服务器会自动列出选项
• 跨服务器操作需在目标服务器执行
• 每条消息标注来源服务器
• 使用 /status 查看实时状态
━━━━━━━━━━━━━━━━━━━━"""
        
        self.bot.send_message(help_msg)
    
    def handle_callback(self, callback_data: str, callback_query_id: str, 
                       chat_id: str, message_id: str):
        """处理回调查询"""
        parts = callback_data.split(':')
        action = parts[0]
        
        if action == 'update_srv':
            server = parts[1]
            self.bot.answer_callback(callback_query_id, "正在加载容器列表...")
            self._show_update_containers(chat_id, server)
        
        elif action == 'restart_srv':
            server = parts[1]
            self.bot.answer_callback(callback_query_id, "正在加载容器列表...")
            self._show_restart_containers(chat_id, server)
        
        elif action == 'restart_cnt':
            server, container = parts[1], parts[2]
            if server != SERVER_NAME:
                self.bot.answer_callback(callback_query_id, "无法操作其他服务器")
                self.bot.edit_message(
                    chat_id, message_id,
                    f"❌ 当前服务器无法操作 <code>{server}</code> 的容器"
                )
                return
            
            # 确认对话框
            confirm_msg = f"""⚠️ <b>确认重启</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{server}</code>
📦 容器: <code>{container}</code>

是否继续？
━━━━━━━━━━━━━━━━━━━━"""
            
            buttons = {
                'inline_keyboard': [
                    [{'text': "✅ 确认重启", 
                      'callback_data': f"confirm_restart:{server}:{container}"}],
                    [{'text': "❌ 取消", 'callback_data': "cancel"}]
                ]
            }
            self.bot.answer_callback(callback_query_id, "准备重启...")
            self.bot.edit_message(chat_id, message_id, confirm_msg, buttons)
        
        elif action == 'confirm_restart':
            server, container = parts[1], parts[2]
            self.bot.answer_callback(callback_query_id, "开始重启容器...")
            self.bot.edit_message(
                chat_id, message_id,
                f"⏳ 正在重启容器 <code>{container}</code>..."
            )
            
            # 执行重启
            success = self.docker.restart_container(container)
            
            if success:
                result_msg = f"""✅ <b>重启成功</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{server}</code>
📦 容器: <code>{container}</code>
⏰ 时间: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━"""
            else:
                result_msg = f"""❌ <b>重启失败</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{server}</code>
📦 容器: <code>{container}</code>

请检查容器状态
━━━━━━━━━━━━━━━━━━━━"""
            
            self.bot.edit_message(chat_id, message_id, result_msg)
        
        elif action == 'monitor_action':
            action_type = parts[1]
            if action_type == 'list':
                self.handle_status(chat_id)
            else:
                servers = self.registry.get_active_servers()
                if len(servers) == 1:
                    self._handle_monitor_server(
                        chat_id, message_id, action_type, servers[0]
                    )
                else:
                    buttons = {
                        'inline_keyboard': [
                            [{'text': f"🖥️ {srv}", 
                              'callback_data': f"monitor_srv:{action_type}:{srv}"}]
                            for srv in servers
                        ]
                    }
                    action_text = "添加监控" if action_type == "add" else "移除监控"
                    self.bot.edit_message(
                        chat_id, message_id,
                        f"📡 <b>{action_text}</b>\n\n请选择服务器：",
                        buttons
                    )
        
        elif action == 'monitor_srv':
            action_type, server = parts[1], parts[2]
            self.bot.answer_callback(callback_query_id, "正在加载容器列表...")
            self._handle_monitor_server(chat_id, message_id, action_type, server)
        
        elif action == 'add_mon':
            server, container = parts[1], parts[2]
            if server != SERVER_NAME:
                self.bot.answer_callback(callback_query_id, "无法操作其他服务器")
                return
            
            self.config.remove_excluded(container)
            self.bot.answer_callback(callback_query_id, "已添加到监控列表")
            self.bot.edit_message(
                chat_id, message_id,
                f"""✅ <b>添加成功</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{server}</code>
📦 容器: <code>{container}</code>

已将容器添加到监控列表
━━━━━━━━━━━━━━━━━━━━"""
            )
        
        elif action == 'rem_mon':
            server, container = parts[1], parts[2]
            if server != SERVER_NAME:
                self.bot.answer_callback(callback_query_id, "无法操作其他服务器")
                return
            
            self.config.add_excluded(container)
            self.bot.answer_callback(callback_query_id, "已从监控列表移除")
            self.bot.edit_message(
                chat_id, message_id,
                f"""✅ <b>移除成功</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{server}</code>
📦 容器: <code>{container}</code>

已将容器从监控列表移除
━━━━━━━━━━━━━━━━━━━━"""
            )
        
        elif action == 'cancel':
            self.bot.answer_callback(callback_query_id, "已取消操作")
            self.bot.edit_message(chat_id, message_id, "❌ 操作已取消")
    
    def _handle_monitor_server(self, chat_id: str, message_id: str, 
                               action: str, server: str):
        """处理监控服务器选择"""
        if server != SERVER_NAME:
            self.bot.edit_message(
                chat_id, message_id,
                f"⚠️ 无法直接操作服务器 <code>{server}</code>\n"
                f"请在对应服务器上执行操作"
            )
            return
        
        if action == 'add':
            excluded = self.config.get_excluded_containers()
            if not excluded:
                self.bot.edit_message(
                    chat_id, message_id,
                    f"✅ 服务器 <code>{server}</code> 所有容器都已在监控中"
                )
                return
            
            buttons = {
                'inline_keyboard': [
                    [{'text': f"➕ {c}", 'callback_data': f"add_mon:{server}:{c}"}]
                    for c in sorted(excluded)
                ]
            }
            self.bot.edit_message(
                chat_id, message_id,
                f"📡 <b>添加监控</b>\n\n🖥️ 服务器: <code>{server}</code>\n\n请选择要添加监控的容器：",
                buttons
            )
        
        else:  # remove
            all_containers = self.docker.get_all_containers()
            monitored = [c for c in all_containers if self.config.is_monitored(c)]
            
            if not monitored:
                self.bot.edit_message(
                    chat_id, message_id,
                    f"⚠️ 服务器 <code>{server}</code> 当前没有监控中的容器"
                )
                return
            
            buttons = {
                'inline_keyboard': [
                    [{'text': f"➖ {c}", 'callback_data': f"rem_mon:{server}:{c}"}]
                    for c in monitored
                ]
            }
            self.bot.edit_message(
                chat_id, message_id,
                f"📡 <b>移除监控</b>\n\n🖥️ 服务器: <code>{server}</code>\n\n请选择要移除监控的容器：",
                buttons
            )


# ==================== Bot 轮询线程 ====================

class BotPoller(threading.Thread):
    """Bot 消息轮询线程"""
    
    def __init__(self, handler: CommandHandler, bot: TelegramBot):
        super().__init__(daemon=True)
        self.handler = handler
        self.bot = bot
        self.last_update_id = 0
    
    def run(self):
        """运行轮询"""
        logger.info("Bot 轮询线程已启动")
        
        while not shutdown_flag.is_set():
            try:
                updates = self.bot.get_updates(self.last_update_id + 1)
                
                if not updates:
                    continue
                
                for update in updates:
                    self.last_update_id = update.get('update_id', self.last_update_id)
                    
                    # 处理命令消息
                    message = update.get('message', {})
                    text = message.get('text', '')
                    chat_id = str(message.get('chat', {}).get('id', ''))
                    
                    if text and chat_id == CHAT_ID:
                        self._handle_command(text, chat_id)
                    
                    # 处理回调查询
                    callback_query = update.get('callback_query', {})
                    if callback_query:
                        self._handle_callback(callback_query)
                
            except Exception as e:
                logger.error(f"轮询错误: {e}")
                time.sleep(5)
    
    def _handle_command(self, text: str, chat_id: str):
        """处理命令"""
        try:
            if text.startswith('/status'):
                self.handler.handle_status(chat_id)
            elif text.startswith('/update'):
                self.handler.handle_update(chat_id)
            elif text.startswith('/restart'):
                self.handler.handle_restart(chat_id)
            elif text.startswith('/monitor'):
                self.handler.handle_monitor(chat_id)
            elif text.startswith('/help') or text.startswith('/start'):
                self.handler.handle_help()
        except Exception as e:
            logger.error(f"处理命令失败: {e}")
    
    def _handle_callback(self, callback_query: Dict):
        """处理回调"""
        try:
            callback_data = callback_query.get('data', '')
            callback_query_id = callback_query.get('id', '')
            chat_id = str(callback_query.get('message', {}).get('chat', {}).get('id', ''))
            message_id = str(callback_query.get('message', {}).get('message_id', ''))
            
            if chat_id == CHAT_ID:
                self.handler.handle_callback(
                    callback_data, callback_query_id, chat_id, message_id
                )
        except Exception as e:
            logger.error(f"处理回调失败: {e}")


# ==================== 心跳线程 ====================

class HeartbeatThread(threading.Thread):
    """服务器心跳线程"""
    
    def __init__(self, registry: ServerRegistry):
        super().__init__(daemon=True)
        self.registry = registry
    
    def run(self):
        """运行心跳"""
        logger.info("心跳线程已启动")
        
        while not shutdown_flag.is_set():
            try:
                self.registry.heartbeat()
                time.sleep(self.registry.heartbeat_interval)
            except Exception as e:
                logger.error(f"心跳错误: {e}")
                time.sleep(5)


# ==================== Watchtower 日志监控 ====================

class WatchtowerMonitor:
    """Watchtower 日志监控"""
    
    def __init__(self, bot: TelegramBot, docker: DockerManager, 
                 config: ConfigManager):
        self.bot = bot
        self.docker = docker
        self.config = config
        self.session_data = {}
    
    def start(self):
        """开始监控"""
        logger.info("开始监控 Watchtower 日志...")
        
        # 等待 Watchtower 启动
        self._wait_for_watchtower()
        
        # 启动日志监控
        try:
            process = subprocess.Popen(
                ['docker', 'logs', '-f', '--tail', '0', 'watchtower'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            for line in iter(process.stdout.readline, ''):
                if shutdown_flag.is_set():
                    break
                
                line = line.strip()
                if not line:
                    continue
                
                logger.info(line)
                self._process_log_line(line)
            
        except Exception as e:
            logger.error(f"监控 Watchtower 日志失败: {e}")
    
    def _wait_for_watchtower(self):
        """等待 Watchtower 启动"""
        logger.info("正在等待 Watchtower 容器启动...")
        
        for _ in range(60):
            try:
                result = subprocess.run(
                    ['docker', 'inspect', '-f', '{{.State.Running}}', 'watchtower'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and 'true' in result.stdout:
                    logger.info("Watchtower 已启动")
                    time.sleep(3)
                    return
            except Exception:
                pass
            time.sleep(2)
        
        logger.warning("Watchtower 启动超时，继续监控")
    
    def _process_log_line(self, line: str):
        """处理日志行"""
        try:
            # 检测容器停止
            if 'Stopping /' in line:
                container = self._extract_container_name(line, 'Stopping /')
                if container and self.config.is_monitored(container):
                    logger.info(f"→ 捕获到停止: {container}")
                    self._store_old_state(container)
            
            # 检测 Session 完成
            elif 'Session done' in line:
                import re
                match = re.search(r'Updated=(\d+)', line)
                if match:
                    updated = int(match.group(1))
                    logger.info(f"→ Session 完成: Updated={updated}")
                    
                    if updated > 0 and self.session_data:
                        self._process_updates()
            
            # 检测严重错误
            elif 'level=error' in line.lower() or 'level=fatal' in line.lower():
                self._process_error(line)
        
        except Exception as e:
            logger.error(f"处理日志行失败: {e}")
    
    def _extract_container_name(self, line: str, prefix: str) -> Optional[str]:
        """从日志行提取容器名"""
        try:
            start = line.find(prefix)
            if start != -1:
                start += len(prefix)
                end = line.find(' ', start)
                if end == -1:
                    end = len(line)
                return line[start:end].strip()
        except Exception:
            pass
        return None
    
    def _store_old_state(self, container: str):
        """存储旧状态"""
        try:
            info = self.docker.get_container_info(container)
            if info:
                self.session_data[container] = {
                    'image': info.get('image', 'unknown'),
                    'image_id': info.get('image_id', 'unknown'),
                    'version': self.docker.get_danmu_version(container)
                }
                logger.info(f"  → 已暂存 {container} 的旧信息")
        except Exception as e:
            logger.error(f"存储旧状态失败: {e}")
    
    def _process_updates(self):
        """处理更新"""
        logger.info(f"→ 发现 {len(self.session_data)} 个更新，开始处理...")
        
        for container, old_state in self.session_data.items():
            try:
                if not self.config.is_monitored(container):
                    logger.info(f"→ {container} 已被排除，跳过处理")
                    continue
                
                logger.info(f"→ 处理容器: {container}")
                time.sleep(5)  # 等待容器启动
                
                # 等待容器运行
                for _ in range(60):
                    info = self.docker.get_container_info(container)
                    if info.get('running'):
                        logger.info("  → 容器已启动")
                        time.sleep(5)
                        break
                    time.sleep(1)
                
                # 获取新状态
                new_info = self.docker.get_container_info(container)
                new_version = self.docker.get_danmu_version(container)
                
                # 格式化版本信息
                old_ver = self._format_version(old_state, container)
                new_ver = self._format_version({
                    'image': new_info.get('image', 'unknown'),
                    'image_id': new_info.get('image_id', 'unknown'),
                    'version': new_version
                }, container)
                
                # 发送通知
                self._send_update_notification(
                    container, 
                    new_info.get('image', 'unknown').split(':')[0],
                    old_ver, 
                    new_ver,
                    new_info.get('running', False)
                )
                
            except Exception as e:
                logger.error(f"处理容器 {container} 更新失败: {e}")
        
        self.session_data.clear()
        logger.info("→ 所有更新处理完成")
    
    def _format_version(self, state: Dict, container: str) -> str:
        """格式化版本信息"""
        image_id = state.get('image_id', 'unknown')
        id_short = image_id.replace('sha256:', '')[:12]
        
        if 'danmu' in container.lower() and state.get('version'):
            return f"v{state['version']} ({id_short})"
        else:
            tag = state.get('image', 'unknown:latest').split(':')[-1]
            return f"{tag} ({id_short})"
    
    def _send_update_notification(self, container: str, image: str, 
                                   old_ver: str, new_ver: str, running: bool):
        """发送更新通知"""
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if running:
            message = f"""✨ <b>容器更新成功</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器名称</b>
   <code>{container}</code>

🎯 <b>镜像信息</b>
   <code>{image}</code>

🔄 <b>版本变更</b>
   <code>{old_ver}</code>
   ➜
   <code>{new_ver}</code>

⏰ <b>更新时间</b>
   <code>{current_time}</code>
━━━━━━━━━━━━━━━━━━━━

✅ 容器已成功启动并运行正常"""
        else:
            message = f"""❌ <b>容器启动失败</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器名称</b>
   <code>{container}</code>

🎯 <b>镜像信息</b>
   <code>{image}</code>

🔄 <b>版本变更</b>
   旧: <code>{old_ver}</code>
   新: <code>{new_ver}</code>

⏰ <b>更新时间</b>
   <code>{current_time}</code>
━━━━━━━━━━━━━━━━━━━━

⚠️ 更新后无法启动
💡 检查: <code>docker logs {container}</code>"""
        
        logger.info("  → 发送通知...")
        self.bot.send_message(message)
    
    def _process_error(self, line: str):
        """处理错误日志"""
        # 过滤常见的非关键错误
        if any(keyword in line.lower() for keyword in 
               ['skipping', 'already up to date', 'no new images', 
                'connection refused', 'timeout']):
            return
        
        # 提取容器名和错误信息
        container = None
        for pattern in ['container=', 'container:', 'container ']:
            if pattern in line.lower():
                try:
                    start = line.lower().find(pattern) + len(pattern)
                    end = line.find(' ', start)
                    if end == -1:
                        end = len(line)
                    container = line[start:end].strip()
                    break
                except Exception:
                    pass
        
        if container and container not in ['watchtower', 'watchtower-notifier']:
            if self.config.is_monitored(container):
                error_msg = line[:200]
                self.bot.send_message(f"""⚠️ <b>Watchtower 严重错误</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器</b>: <code>{container}</code>
🔴 <b>错误</b>: <code>{error_msg}</code>
🕐 <b>时间</b>: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━""")


# ==================== 主程序 ====================

def main():
    """主程序入口"""
    # 验证环境变量
    if not SERVER_NAME:
        logger.error("错误: 必须设置 SERVER_NAME 环境变量")
        sys.exit(1)
    
    if not CHAT_ID or not os.getenv('BOT_TOKEN'):
        logger.error("错误: 必须设置 BOT_TOKEN 和 CHAT_ID 环境变量")
        sys.exit(1)
    
    # 打印启动信息
    print("=" * 50)
    print(f"Docker 容器监控通知服务 v{VERSION}")
    print(f"服务器: {SERVER_NAME}")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python 版本: {sys.version.split()[0]}")
    print("=" * 50)
    print()
    
    # 初始化组件
    bot = TelegramBot(os.getenv('BOT_TOKEN'), CHAT_ID, SERVER_NAME)
    docker = DockerManager()
    config = ConfigManager(MONITOR_CONFIG, SERVER_NAME)
    registry = ServerRegistry(SERVER_REGISTRY, SERVER_NAME)
    
    # 注册服务器
    registry.register()
    
    # 初始化命令处理器
    handler = CommandHandler(bot, docker, config, registry)
    
    # 启动 Bot 轮询线程
    bot_poller = BotPoller(handler, bot)
    bot_poller.start()
    logger.info(f"Bot 轮询线程已启动")
    
    # 启动心跳线程
    heartbeat = HeartbeatThread(registry)
    heartbeat.start()
    logger.info(f"心跳线程已启动")
    
    # 获取容器统计信息
    all_containers = docker.get_all_containers()
    monitored = [c for c in all_containers if config.is_monitored(c)]
    excluded = config.get_excluded_containers()
    
    logger.info(f"总容器: {len(all_containers)}, 监控: {len(monitored)}, 排除: {len(excluded)}")
    
    # 发送启动通知
    servers = registry.get_active_servers()
    server_list = "\n".join([f"   • <code>{s}</code>" for s in servers])
    
    startup_msg = f"""🚀 <b>监控服务启动成功</b>

━━━━━━━━━━━━━━━━━━━━
📊 <b>服务信息</b>
   版本: <code>v{VERSION}</code>
   服务器: <code>{SERVER_NAME}</code>
   语言: <code>Python {sys.version.split()[0]}</code>

🎯 <b>监控状态</b>
   总容器: <code>{len(all_containers)}</code>
   监控中: <code>{len(monitored)}</code>
   已排除: <code>{len(excluded)}</code>

🌐 <b>已连接服务器 ({len(servers)})</b>
{server_list}

🤖 <b>机器人功能</b>
   /status - 查看状态
   /update - 更新容器
   /restart - 重启容器
   /monitor - 监控管理
   /help - 显示帮助

💡 <b>新特性</b>
   • Python 实现，更稳定
   • 真正的多服务器支持
   • 自动服务发现
   • 更好的错误处理

⏰ <b>启动时间</b>
   <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━

✅ 服务正常运行中"""
    
    bot.send_message(startup_msg)
    
    # 设置信号处理
    def signal_handler(signum, frame):
        logger.info("收到退出信号，正在关闭...")
        shutdown_flag.set()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 启动 Watchtower 监控
    monitor = WatchtowerMonitor(bot, docker, config)
    try:
        monitor.start()
    except KeyboardInterrupt:
        logger.info("用户中断")
    except Exception as e:
        logger.error(f"监控异常: {e}")
    finally:
        shutdown_flag.set()
        logger.info("服务已停止")


if __name__ == "__main__":
    main()
