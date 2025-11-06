#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Docker 容器监控通知服务 v5.3.1
- 修复主服务器判断逻辑
- 修复回调处理竞争条件
- 优化服务器协调机制
"""

import os
import sys
import json
import time
import signal
import subprocess
import threading
import logging
import fcntl
from datetime import datetime
from typing import Dict, List, Optional, Set
import requests
from pathlib import Path

# ==================== 配置和常量 ====================

VERSION = "5.3.1"
TELEGRAM_API = f"https://api.telegram.org/bot{os.getenv('BOT_TOKEN')}"
CHAT_ID = os.getenv('CHAT_ID')
SERVER_NAME = os.getenv('SERVER_NAME')
PRIMARY_SERVER = os.getenv('PRIMARY_SERVER', SERVER_NAME)  # 主服务器，默认为当前服务器

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


# ==================== 文件锁管理器 ====================

class FileLock:
    """文件锁上下文管理器"""

    def __init__(self, file_path: Path, timeout: int = 10):
        self.file_path = file_path
        self.timeout = timeout
        self.lock_file = None

    def __enter__(self):
        lock_path = str(self.file_path) + '.lock'
        self.lock_file = open(lock_path, 'w')

        start_time = time.time()
        while True:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except IOError:
                if time.time() - start_time > self.timeout:
                    raise TimeoutError(f"无法获取文件锁: {self.file_path}")
                time.sleep(0.1)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.lock_file:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                self.lock_file.close()
            except Exception as e:
                logger.error(f"释放文件锁失败: {e}")


def safe_read_json(file_path: Path, default: Dict = None, max_retries: int = 3) -> Dict:
    """安全读取 JSON 文件（带重试和文件锁）"""
    if default is None:
        default = {}

    for attempt in range(max_retries):
        try:
            if not file_path.exists():
                return default.copy()

            with FileLock(file_path, timeout=5):
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if not content:
                        return default.copy()
                    data = json.loads(content)
                    return data

        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败 (尝试 {attempt + 1}/{max_retries}): {file_path}")
            if attempt < max_retries - 1:
                time.sleep(0.5)
            else:
                return default.copy()

        except TimeoutError:
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                return default.copy()

        except Exception as e:
            logger.error(f"读取文件失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(0.5)
            else:
                return default.copy()

    return default.copy()


def safe_write_json(file_path: Path, data: Dict, max_retries: int = 3) -> bool:
    """安全写入 JSON 文件（带重试和文件锁）"""
    for attempt in range(max_retries):
        try:
            with FileLock(file_path, timeout=5):
                temp_path = file_path.with_suffix('.tmp')
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                temp_path.replace(file_path)
                return True

        except TimeoutError:
            if attempt < max_retries - 1:
                time.sleep(1)

        except Exception as e:
            logger.error(f"写入文件失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(0.5)

    return False


# ==================== 工具类 ====================

class CommandCoordinator:
    """命令协调器 - 主服务器优先机制"""

    def __init__(self, server_name: str, primary_server: str, registry_file: Path):
        self.server_name = server_name
        self.primary_server = primary_server
        self.registry_file = registry_file
        self.is_primary = (server_name == primary_server)
        logger.info(f"协调器初始化: 当前={server_name}, 主服务器={primary_server}, 是否主服务器={self.is_primary}")

    def should_handle_command(self, command: str, callback_data: str = None) -> bool:
        """判断当前服务器是否应该处理该命令或回调"""

        if callback_data:
            return self._should_handle_callback(callback_data)

        # 全局命令（所有服务器响应）
        global_commands = ['/start']
        if any(command.startswith(cmd) for cmd in global_commands):
            return True

        # 需要协调的命令
        coordinated_commands = ['/status', '/update', '/restart', '/monitor', '/help']
        if not any(command.startswith(cmd) for cmd in coordinated_commands):
            return True

        # 获取协调者
        coordinator = self._get_coordinator()
        is_coordinator = (self.server_name == coordinator)

        if is_coordinator:
            logger.info(f"✓ 作为协调者处理命令: {command}")
        else:
            logger.info(f"✗ 非协调者忽略命令: {command} (协调者: {coordinator})")

        return is_coordinator

    def _should_handle_callback(self, callback_data: str) -> bool:
        """判断是否应该处理回调"""
        parts = callback_data.split(':')
        action = parts[0]

        # 不包含服务器信息的回调，由协调者处理
        non_server_callbacks = ['monitor_action', 'cancel']
        if action in non_server_callbacks:
            coordinator = self._get_coordinator()
            is_coordinator = (self.server_name == coordinator)
            logger.info(f"回调 {action}: 协调者={coordinator}, 当前={self.server_name}, 处理={is_coordinator}")
            return is_coordinator

        # 包含服务器信息的回调 - 由目标服务器处理
        if len(parts) >= 2:
            server_target_actions = [
                'status_srv', 'update_srv', 'restart_srv', 'monitor_srv',
                'update_cnt', 'restart_cnt', 'confirm_restart', 
                'confirm_update', 'add_mon', 'rem_mon'
            ]

            if action in server_target_actions:
                target_server = parts[1]
                should_handle = (target_server == self.server_name)
                logger.info(f"回调 {action} 目标: {target_server}, 当前: {self.server_name}, 处理: {should_handle}")
                return should_handle

        # 默认：让协调者处理
        coordinator = self._get_coordinator()
        is_coordinator = (self.server_name == coordinator)
        logger.info(f"回调 {action} (默认): 协调者={coordinator}, 当前={self.server_name}, 处理={is_coordinator}")
        return is_coordinator

    def _get_coordinator(self) -> str:
        """获取协调者（优先使用主服务器）"""
        registry = safe_read_json(self.registry_file, default={})
        
        if not registry:
            logger.debug(f"注册表为空，使用当前服务器: {self.server_name}")
            return self.server_name
        
        current_time = time.time()
        active_servers = []
        
        # 获取所有活跃服务器
        for server, info in registry.items():
            if current_time - info.get('last_heartbeat', 0) < 90:
                active_servers.append(server)
        
        if not active_servers:
            logger.debug(f"没有活跃服务器，使用当前服务器: {self.server_name}")
            return self.server_name
        
        # 如果主服务器在线且活跃，使用主服务器
        if self.primary_server in active_servers:
            logger.debug(f"主服务器 {self.primary_server} 活跃，作为协调者")
            return self.primary_server
        
        # 否则使用字母顺序第一个
        coordinator = sorted(active_servers)[0]
        logger.debug(f"主服务器不活跃，使用备用协调者: {coordinator}")
        return coordinator

    def _get_active_servers(self) -> List[str]:
        """获取活跃的服务器列表"""
        registry = safe_read_json(self.registry_file, default={})

        if not registry:
            return [self.server_name]

        current_time = time.time()
        active_servers = []

        for server, info in registry.items():
            if current_time - info.get('last_heartbeat', 0) < 90:
                active_servers.append(server)

        return sorted(active_servers) if active_servers else [self.server_name]


class TelegramBot:
    """Telegram Bot API 封装"""

    def __init__(self, token: str, chat_id: str, server_name: str):
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.server_name = server_name
        self.session = requests.Session()
        self.session.headers.update({'Connection': 'keep-alive'})

    def send_message(self, text: str, reply_markup: Optional[Dict] = None, 
                     max_retries: int = 3) -> bool:
        """发送 Telegram 消息"""
        for attempt in range(max_retries):
            try:
                payload = {
                    'chat_id': self.chat_id,
                    'text': text,
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
                time.sleep(wait_time)

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
    def update_container(container: str, progress_callback=None) -> Dict:
        """
        更新容器
        返回: {'success': bool, 'message': str, 'old_version': str, 'new_version': str}
        """
        result = {
            'success': False,
            'message': '',
            'old_version': '',
            'new_version': ''
        }

        try:
            # 1. 获取旧版本信息
            if progress_callback:
                progress_callback("📋 正在获取容器信息...")
            
            old_info = DockerManager.get_container_info(container)
            if not old_info:
                result['message'] = "无法获取容器信息"
                return result

            image = old_info['image']
            old_image_id = old_info['image_id']
            result['old_version'] = DockerManager._format_version_info(old_info, container)

            # 2. 拉取新镜像
            if progress_callback:
                progress_callback(f"🔄 正在拉取镜像: {image}")
            
            logger.info(f"拉取镜像: {image}")
            pull_result = subprocess.run(
                ['docker', 'pull', image],
                capture_output=True, text=True, timeout=300
            )

            if pull_result.returncode != 0:
                result['message'] = f"拉取镜像失败: {pull_result.stderr[:200]}"
                return result

            # 3. 检查是否有更新
            new_inspect = subprocess.run(
                ['docker', 'inspect', '--format', '{{.Id}}', image],
                capture_output=True, text=True, timeout=10
            )
            
            if new_inspect.returncode == 0:
                new_image_id = new_inspect.stdout.strip()
                if new_image_id == old_image_id:
                    result['message'] = "镜像已是最新版本，无需更新"
                    result['success'] = True
                    return result

            # 4. 获取容器配置
            if progress_callback:
                progress_callback("📦 正在获取容器配置...")

            inspect_result = subprocess.run(
                ['docker', 'inspect', container],
                capture_output=True, text=True, timeout=10
            )

            if inspect_result.returncode != 0:
                result['message'] = "无法获取容器配置"
                return result

            config = json.loads(inspect_result.stdout)[0]
            
            # 提取运行参数
            env_vars = config['Config'].get('Env', [])
            volumes = []
            for mount in config['Mounts']:
                volumes.extend(['-v', f"{mount['Source']}:{mount['Destination']}"])
            
            ports = []
            port_bindings = config['HostConfig'].get('PortBindings', {})
            for container_port, host_configs in port_bindings.items():
                if host_configs:
                    host_port = host_configs[0].get('HostPort', '')
                    if host_port:
                        ports.extend(['-p', f"{host_port}:{container_port.split('/')[0]}"])

            network = config['HostConfig'].get('NetworkMode', 'bridge')
            restart_policy = config['HostConfig'].get('RestartPolicy', {}).get('Name', 'unless-stopped')

            # 5. 停止并删除旧容器
            if progress_callback:
                progress_callback("⏸️ 正在停止旧容器...")

            logger.info(f"停止容器: {container}")
            subprocess.run(['docker', 'stop', container], timeout=30)

            if progress_callback:
                progress_callback("🗑️ 正在删除旧容器...")

            logger.info(f"删除容器: {container}")
            subprocess.run(['docker', 'rm', container], timeout=10)

            # 6. 创建并启动新容器
            if progress_callback:
                progress_callback("🚀 正在启动新容器...")

            logger.info(f"启动新容器: {container}")
            
            run_cmd = ['docker', 'run', '-d', '--name', container]
            run_cmd.extend(['--network', network])
            run_cmd.extend(['--restart', restart_policy])
            
            for env in env_vars:
                run_cmd.extend(['-e', env])
            
            run_cmd.extend(volumes)
            run_cmd.extend(ports)
            run_cmd.append(image)

            run_result = subprocess.run(
                run_cmd,
                capture_output=True, text=True, timeout=60
            )

            if run_result.returncode != 0:
                result['message'] = f"启动新容器失败: {run_result.stderr[:200]}"
                return result

            # 7. 等待容器启动
            time.sleep(5)

            # 8. 获取新版本信息
            new_info = DockerManager.get_container_info(container)
            if new_info and new_info.get('running'):
                result['new_version'] = DockerManager._format_version_info(new_info, container)
                result['success'] = True
                result['message'] = "容器更新成功"
            else:
                result['message'] = "容器启动失败，请检查日志"

            return result

        except subprocess.TimeoutExpired:
            result['message'] = "操作超时"
            return result
        except Exception as e:
            result['message'] = f"更新失败: {str(e)[:200]}"
            logger.error(f"更新容器 {container} 失败: {e}")
            return result

    @staticmethod
    def _format_version_info(info: Dict, container: str) -> str:
        """格式化版本信息"""
        image_id = info.get('image_id', 'unknown')
        id_short = image_id.replace('sha256:', '')[:12]

        if 'danmu' in container.lower():
            version = DockerManager.get_danmu_version(container)
            if version:
                return f"v{version} ({id_short})"

        tag = info.get('image', 'unknown:latest').split(':')[-1]
        return f"{tag} ({id_short})"

    @staticmethod
    def get_danmu_version(container: str) -> Optional[str]:
        """获取 danmu-api 版本"""
        if 'danmu' not in container.lower():
            return None

        try:
            for _ in range(30):
                check = subprocess.run(
                    ['docker', 'exec', container, 'test', '-f', 
                     '/app/danmu_api/configs/globals.js'],
                    capture_output=True, timeout=5
                )
                if check.returncode == 0:
                    break
                time.sleep(1)

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
        return safe_read_json(self.config_file, default={})

    def _save_config(self):
        """保存配置"""
        safe_write_json(self.config_file, self.config)

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
    """服务器注册中心"""

    def __init__(self, registry_file: Path, server_name: str, primary_server: str):
        self.registry_file = registry_file
        self.server_name = server_name
        self.primary_server = primary_server
        self.is_primary = (server_name == primary_server)
        self.heartbeat_interval = 30
        self.timeout = 90

    def register(self):
        """注册当前服务器"""
        registry = safe_read_json(self.registry_file, default={})
        registry[self.server_name] = {
            'last_heartbeat': time.time(),
            'version': VERSION,
            'is_primary': self.is_primary,
            'primary_server': self.primary_server
        }
        if safe_write_json(self.registry_file, registry):
            role = "主服务器 🌟" if self.is_primary else "从服务器"
            logger.info(f"服务器已注册: {self.server_name} ({role})")
        else:
            logger.error(f"服务器注册失败: {self.server_name}")

    def heartbeat(self):
        """发送心跳"""
        registry = safe_read_json(self.registry_file, default={})
        if self.server_name in registry:
            registry[self.server_name]['last_heartbeat'] = time.time()
            registry[self.server_name]['is_primary'] = self.is_primary
            registry[self.server_name]['primary_server'] = self.primary_server
            safe_write_json(self.registry_file, registry)

    def get_active_servers(self) -> List[str]:
        """获取活跃的服务器列表"""
        registry = safe_read_json(self.registry_file, default={})
        current_time = time.time()
        active_servers = []

        for server, info in registry.items():
            if current_time - info.get('last_heartbeat', 0) < self.timeout:
                active_servers.append(server)

        return sorted(active_servers)


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
        servers = self.registry.get_active_servers()

        if len(servers) > 1:
            buttons = {
                'inline_keyboard': [
                    [{'text': f"🖥️ {srv}", 'callback_data': f"status_srv:{srv}"}]
                    for srv in servers
                ]
            }
            self.bot.send_message("📊 <b>选择要查看状态的服务器：</b>", buttons)
        else:
            self._show_server_status(chat_id, servers[0] if servers else SERVER_NAME)

    def _show_server_status(self, chat_id: str, server: str):
        """显示指定服务器的状态"""
        all_containers = self.docker.get_all_containers()
        monitored = [c for c in all_containers if self.config.is_monitored(c)]
        excluded = self.config.get_excluded_containers()

        status_msg = f"""📊 <b>服务器状态</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ <b>服务器信息</b>
   名称: <code>{server}</code>
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

        if len(servers) > 1:
            buttons = {
                'inline_keyboard': [
                    [{'text': f"🖥️ {srv}", 'callback_data': f"update_srv:{srv}"}]
                    for srv in servers
                ]
            }
            self.bot.send_message("🔄 <b>选择要更新容器的服务器：</b>", buttons)
        else:
            self._show_update_containers(chat_id, servers[0])

    def _show_update_containers(self, chat_id: str, server: str):
        """显示可更新的容器列表"""
        containers = [c for c in self.docker.get_all_containers() 
                     if self.config.is_monitored(c)]

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

        if len(servers) > 1:
            buttons = {
                'inline_keyboard': [
                    [{'text': f"🖥️ {srv}", 'callback_data': f"restart_srv:{srv}"}]
                    for srv in servers
                ]
            }
            self.bot.send_message("🔄 <b>选择要重启容器的服务器：</b>", buttons)
        else:
            self._show_restart_containers(chat_id, servers[0])

    def _show_restart_containers(self, chat_id: str, server: str):
        """显示可重启的容器列表"""
        containers = self.docker.get_all_containers()

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
        
        # 获取注册信息，标记主服务器
        registry = safe_read_json(self.registry.registry_file, default={})
        server_lines = []
        for s in servers:
            info = registry.get(s, {})
            is_primary = info.get('is_primary', False)
            marker = " 🌟" if is_primary else ""
            server_lines.append(f"   • <code>{s}</code>{marker}")
        
        server_list = "\n".join(server_lines)

        help_msg = f"""📖 <b>命令帮助</b>

━━━━━━━━━━━━━━━━━━━━
<b>可用命令：</b>

/status - 查看服务器状态
/update - 更新容器镜像
/restart - 重启容器
/monitor - 监控管理
/help - 显示此帮助信息

━━━━━━━━━━━━━━━━━━━━
<b>🌐 已连接服务器 ({len(servers)})：</b>
{server_list if servers else '   <i>暂无服务器</i>'}

━━━━━━━━━━━━━━━━━━━━
💡 <b>使用提示：</b>

• 多服务器时先选择服务器
• 然后选择要操作的容器
• 所有操作通过按钮完成
• 使用 /status 查看实时状态
━━━━━━━━━━━━━━━━━━━━"""

        self.bot.send_message(help_msg)

    def handle_callback(self, callback_data: str, callback_query_id: str, 
                       chat_id: str, message_id: str):
        """处理回调查询"""
        parts = callback_data.split(':')
        action = parts[0]

        logger.info(f"处理回调: {callback_data}")

        if action == 'status_srv':
            server = parts[1]
            self.bot.answer_callback(callback_query_id, f"正在获取 {server} 状态...")
            self._show_server_status(chat_id, server)

        elif action == 'update_srv':
            server = parts[1]
            self.bot.answer_callback(callback_query_id, "正在加载容器列表...")
            self._show_update_containers(chat_id, server)

        elif action == 'update_cnt':
            server, container = parts[1], parts[2]
            confirm_msg = f"""⚠️ <b>确认更新</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{server}</code>
📦 容器: <code>{container}</code>

<b>更新流程：</b>
1. 拉取最新镜像
2. 停止当前容器
3. 删除旧容器
4. 启动新容器

⚠️ <b>注意：</b>容器将短暂停止服务

是否继续？
━━━━━━━━━━━━━━━━━━━━"""

            buttons = {
                'inline_keyboard': [
                    [{'text': "✅ 确认更新", 
                      'callback_data': f"confirm_update:{server}:{container}"}],
                    [{'text': "❌ 取消", 'callback_data': "cancel"}]
                ]
            }
            self.bot.answer_callback(callback_query_id, "准备更新...")
            self.bot.edit_message(chat_id, message_id, confirm_msg, buttons)

        elif action == 'confirm_update':
            server, container = parts[1], parts[2]
            self.bot.answer_callback(callback_query_id, "开始更新容器...")
            
            # 在新线程中执行更新，避免阻塞
            def update_thread():
                current_msg = f"⏳ 正在更新容器 <code>{container}</code>...\n\n"
                self.bot.edit_message(chat_id, message_id, current_msg + "📋 准备更新...")

                def progress_update(msg):
                    self.bot.edit_message(chat_id, message_id, current_msg + msg)

                result = self.docker.update_container(container, progress_update)

                if result['success']:
                    result_msg = f"""✅ <b>更新成功</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{server}</code>
📦 容器: <code>{container}</code>

🔄 <b>版本变更</b>
   旧: <code>{result['old_version']}</code>
   新: <code>{result['new_version']}</code>

⏰ 时间: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━

{result['message']}"""
                else:
                    result_msg = f"""❌ <b>更新失败</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{server}</code>
📦 容器: <code>{container}</code>

❌ <b>错误信息</b>
   {result['message']}

💡 <b>建议</b>
   • 检查镜像名称是否正确
   • 查看容器日志排查问题
   • 尝试手动更新容器
━━━━━━━━━━━━━━━━━━━━"""

                self.bot.edit_message(chat_id, message_id, result_msg)

            threading.Thread(target=update_thread, daemon=True).start()

        elif action == 'restart_srv':
            server = parts[1]
            self.bot.answer_callback(callback_query_id, "正在加载容器列表...")
            self._show_restart_containers(chat_id, server)

        elif action == 'restart_cnt':
            server, container = parts[1], parts[2]
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
                self.bot.answer_callback(callback_query_id, "正在查看列表...")
                self.handle_status(chat_id)
            else:
                servers = self.registry.get_active_servers()
                if len(servers) == 1:
                    self.bot.answer_callback(callback_query_id, "正在加载...")
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
                    self.bot.answer_callback(callback_query_id, f"选择服务器...")
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

        else:
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

    def __init__(self, handler: CommandHandler, bot: TelegramBot, 
                 coordinator: CommandCoordinator):
        super().__init__(daemon=True)
        self.handler = handler
        self.bot = bot
        self.coordinator = coordinator
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

                    # 处理普通消息
                    message = update.get('message', {})
                    text = message.get('text', '')
                    chat_id = str(message.get('chat', {}).get('id', ''))

                    if text and chat_id == CHAT_ID:
                        if self.coordinator.should_handle_command(text):
                            self._handle_command(text, chat_id)

                    # 处理回调查询
                    callback_query = update.get('callback_query', {})
                    if callback_query:
                        callback_data = callback_query.get('data', '')
                        if self.coordinator.should_handle_command(None, callback_data):
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
        self._wait_for_watchtower()

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
            if 'Stopping /' in line:
                container = self._extract_container_name(line, 'Stopping /')
                if container and self.config.is_monitored(container):
                    logger.info(f"→ 捕获到停止: {container}")
                    self._store_old_state(container)

            elif 'Session done' in line:
                import re
                match = re.search(r'Updated=(\d+)', line)
                if match:
                    updated = int(match.group(1))
                    logger.info(f"→ Session 完成: Updated={updated}")

                    if updated > 0 and self.session_data:
                        self._process_updates()

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
                time.sleep(5)

                for _ in range(60):
                    info = self.docker.get_container_info(container)
                    if info.get('running'):
                        logger.info("  → 容器已启动")
                        time.sleep(5)
                        break
                    time.sleep(1)

                new_info = self.docker.get_container_info(container)
                new_version = self.docker.get_danmu_version(container)

                old_ver = self._format_version(old_state, container)
                new_ver = self._format_version({
                    'image': new_info.get('image', 'unknown'),
                    'image_id': new_info.get('image_id', 'unknown'),
                    'version': new_version
                }, container)

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
            message = f"""<b>[{self.bot.server_name}]</b> ✨ <b>容器更新成功</b>

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
            message = f"""<b>[{self.bot.server_name}]</b> ❌ <b>容器启动失败</b>

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
        if any(keyword in line.lower() for keyword in 
               ['skipping', 'already up to date', 'no new images', 
                'connection refused', 'timeout']):
            return

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
                self.bot.send_message(f"""<b>[{self.bot.server_name}]</b> ⚠️ <b>Watchtower 严重错误</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器</b>: <code>{container}</code>
🔴 <b>错误</b>: <code>{error_msg}</code>
🕐 <b>时间</b>: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━""")


# ==================== 主程序 ====================

def main():
    """主程序入口"""
    if not SERVER_NAME:
        logger.error("错误: 必须设置 SERVER_NAME 环境变量")
        sys.exit(1)

    if not CHAT_ID or not os.getenv('BOT_TOKEN'):
        logger.error("错误: 必须设置 BOT_TOKEN 和 CHAT_ID 环境变量")
        sys.exit(1)

    print("=" * 50)
    print(f"Docker 容器监控通知服务 v{VERSION}")
    print(f"服务器: {SERVER_NAME}")
    print(f"主服务器: {PRIMARY_SERVER}")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python 版本: {sys.version.split()[0]}")
    print("=" * 50)
    print()

    is_primary = (SERVER_NAME == PRIMARY_SERVER)
    
    bot = TelegramBot(os.getenv('BOT_TOKEN'), CHAT_ID, SERVER_NAME)
    docker = DockerManager()
    config = ConfigManager(MONITOR_CONFIG, SERVER_NAME)
    registry = ServerRegistry(SERVER_REGISTRY, SERVER_NAME, is_primary)
    coordinator = CommandCoordinator(SERVER_NAME, PRIMARY_SERVER, SERVER_REGISTRY)

    # 立即注册服务器（主服务器无需等待）
    registry.register()
    
    # 从服务器等待0.5秒，让主服务器先注册
    if not is_primary:
        logger.info("从服务器等待 0.5 秒...")
        time.sleep(0.5)

    handler = CommandHandler(bot, docker, config, registry)

    bot_poller = BotPoller(handler, bot, coordinator)
    bot_poller.start()
    logger.info(f"Bot 轮询线程已启动")

    heartbeat = HeartbeatThread(registry)
    heartbeat.start()
    logger.info(f"心跳线程已启动")

    all_containers = docker.get_all_containers()
    monitored = [c for c in all_containers if config.is_monitored(c)]
    excluded = config.get_excluded_containers()

    logger.info(f"总容器: {len(all_containers)}, 监控: {len(monitored)}, 排除: {len(excluded)}")

    # 只有主服务器发送启动消息
    if is_primary:
        time.sleep(1)  # 等待其他服务器注册
        servers = registry.get_active_servers()
        server_list = "\n".join([
            f"   • <code>{s}</code>{' 🌟' if s == PRIMARY_SERVER else ''}" 
            for s in servers
        ])

        startup_msg = f"""🚀 <b>监控服务启动成功</b>

━━━━━━━━━━━━━━━━━━━━
📊 <b>服务信息</b>
   版本: <code>v{VERSION}</code>
   主服务器: <code>{PRIMARY_SERVER}</code> 🌟
   当前服务器: <code>{SERVER_NAME}</code>
   语言: <code>Python {sys.version.split()[0]}</code>

🎯 <b>监控状态</b>
   总容器: <code>{len(all_containers)}</code>
   监控中: <code>{len(monitored)}</code>
   已排除: <code>{len(excluded)}</code>

🌐 <b>已连接服务器 ({len(servers)})</b>
{server_list}

🤖 <b>机器人功能</b>
   /status - 查看服务器状态
   /update - 更新容器镜像
   /restart - 重启容器
   /monitor - 监控管理
   /help - 显示帮助

💡 <b>新特性 v5.3.0</b>
   • 添加主服务器优先协调机制
   • 实现单容器更新功能
   • 优化启动响应速度
   • 修复命令延迟响应问题

⏰ <b>启动时间</b>
   <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━

✅ 服务正常运行中"""

        bot.send_message(startup_msg)
    else:
        logger.info(f"从服务器已启动，等待主服务器 {PRIMARY_SERVER} 协调")

    def signal_handler(signum, frame):
        logger.info("收到退出信号，正在关闭...")
        shutdown_flag.set()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

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