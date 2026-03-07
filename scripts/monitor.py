#!/usr/bin/env python3

import os
import sys
import json
import time
import signal
import subprocess
import threading
import logging
import uuid
import fcntl
import hashlib
import html as html_lib
import re
import select
import shlex
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set
import requests
from pathlib import Path

VERSION = "5.3.3"
TELEGRAM_API = f"https://api.telegram.org/bot{os.getenv('BOT_TOKEN')}"
CHAT_ID = os.getenv('CHAT_ID')
SERVER_NAME = os.getenv('SERVER_NAME')
PRIMARY_SERVER = os.getenv('PRIMARY_SERVER', 'false').lower() == 'true'
ENABLE_ROLLBACK = os.getenv('ENABLE_ROLLBACK', 'true').lower() == 'true'
CLEANUP_OLD_IMAGES = os.getenv('CLEANUP', 'true').lower() == 'true'
AUTO_UPDATE = os.getenv('AUTO_UPDATE', 'true').lower() == 'true'
NOTIFY_ON_AVAILABLE_UPDATE = os.getenv('NOTIFY_ON_AVAILABLE_UPDATE', 'true').lower() == 'true'
UPDATE_SOURCE = os.getenv('UPDATE_SOURCE', 'auto').strip().lower()
CHECK_INTERVAL = max(int(os.getenv('POLL_INTERVAL', '1800') or '1800'), 30)
INITIAL_CHECK_DELAY = max(int(os.getenv('INITIAL_CHECK_DELAY', '15') or '15'), 0)
UPDATE_RETRY_BACKOFF = max(int(os.getenv('UPDATE_RETRY_BACKOFF', '1800') or '1800'), 60)

if UPDATE_SOURCE not in {'auto', 'independent', 'watchtower'}:
    UPDATE_SOURCE = 'independent'

DATA_DIR = Path("/data")
MONITOR_CONFIG = DATA_DIR / "monitor_config.json"
SERVER_REGISTRY = DATA_DIR / "server_registry.json"
UPDATE_STATE_FILE = DATA_DIR / "update_state.json"
COMMAND_QUEUE_FILE = DATA_DIR / "command_queue.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOCK_DIR = DATA_DIR / 'locks'
LOCK_DIR.mkdir(parents=True, exist_ok=True)


def sanitize_file_component(value: str) -> str:
    if not value:
        return 'default'

    normalized = value.strip()
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', normalized).strip('._-')
    digest = hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:10]

    if not cleaned:
        return f'server_{digest}'

    if cleaned != normalized:
        return f'{cleaned[:32]}_{digest}'

    return cleaned[:48]


def parse_container_list(value: str) -> Set[str]:
    if not value:
        return set()

    normalized = value.replace(',', ' ').replace('\n', ' ')
    return {item.strip() for item in normalized.split() if item.strip()}


def escape_html(value: Any) -> str:
    return html_lib.escape(str(value), quote=False)


def strip_html(value: str) -> str:
    return html_lib.unescape(re.sub(r'<[^>]+>', '', value))


def container_lock_path(container: str) -> Path:
    return LOCK_DIR / f"container_action_{sanitize_file_component(container)}"


SERVER_FILE_KEY = sanitize_file_component(SERVER_NAME or 'default')
HEALTH_FILE = DATA_DIR / f"health_status.{SERVER_FILE_KEY}.json"
STATIC_MONITORED_CONTAINERS = parse_container_list(os.getenv('MONITORED_CONTAINERS', ''))

logging.basicConfig(
   level=logging.INFO,
   format='[%(asctime)s] %(levelname)s: %(message)s',
   datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

shutdown_flag = threading.Event()

class FileLock:
   def __init__(self, file_path: Path, timeout: int = 10):
       self.file_path = file_path
       self.timeout = timeout
       self.lock_path = Path(str(file_path) + '.lock')
       self.acquired = False

   def __enter__(self):
       start_time = time.time()
       while True:
           try:
               fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
               with os.fdopen(fd, 'w', encoding='utf-8') as lock_file:
                   lock_file.write(f'{os.getpid()}\n')
               self.acquired = True
               return self
           except FileExistsError:
               try:
                   if self.lock_path.exists() and time.time() - self.lock_path.stat().st_mtime > self.timeout:
                       self.lock_path.unlink(missing_ok=True)
                       continue
               except Exception:
                   pass
               if time.time() - start_time > self.timeout:
                   raise TimeoutError(f"无法获取文件锁: {self.file_path}")
               time.sleep(0.1)

   def __exit__(self, exc_type, exc_val, exc_tb):
       if self.acquired:
           try:
               self.lock_path.unlink(missing_ok=True)
           except Exception as e:
               logger.error(f"释放文件锁失败: {e}")

def safe_read_json(file_path: Path, default: Dict = None, max_retries: int = 3) -> Dict:
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
   for attempt in range(max_retries):
       try:
           with FileLock(file_path, timeout=5):
               temp_path = file_path.with_name(f'{file_path.name}.{uuid.uuid4().hex}.tmp')
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

def safe_update_json(file_path: Path, updater: Callable[[Dict], Dict], default: Dict = None,
                    max_retries: int = 3) -> Optional[Dict]:
   if default is None:
       default = {}

   for attempt in range(max_retries):
       try:
           with FileLock(file_path, timeout=5):
               data = default.copy()
               if file_path.exists():
                   with open(file_path, 'r', encoding='utf-8') as f:
                       content = f.read().strip()
                       if content:
                           data = json.loads(content)

               updated = updater(data)
               if updated is None:
                   updated = data

               temp_path = file_path.with_name(f'{file_path.name}.{uuid.uuid4().hex}.tmp')
               with open(temp_path, 'w', encoding='utf-8') as f:
                   json.dump(updated, f, ensure_ascii=False, indent=2)
                   f.flush()
                   os.fsync(f.fileno())
               temp_path.replace(file_path)
               return updated

       except (json.JSONDecodeError, TimeoutError) as e:
           logger.error(f"更新文件失败: {file_path} - {e}")
           if attempt < max_retries - 1:
               time.sleep(0.5 if isinstance(e, json.JSONDecodeError) else 1)

       except Exception as e:
           logger.error(f"更新文件失败: {e}")
           if attempt < max_retries - 1:
               time.sleep(0.5)

   return None

class HealthReporter:
    def __init__(self, health_file: Path, server_name: str):
        self.health_file = health_file
        self.server_name = server_name
        self._lock = threading.Lock()
        self._state = {
            'pid': os.getpid(),
            'server_name': server_name,
            'version': VERSION,
            'started_at': time.time(),
            'updated_at': time.time(),
            'components': {}
        }

    def beat(self, component: str, status: str = 'ok', details: Optional[Dict] = None):
        with self._lock:
            now = time.time()
            self._state['pid'] = os.getpid()
            self._state['updated_at'] = now

            component_state = {
                'status': status,
                'updated_at': now
            }

            if details:
                component_state['details'] = details

            self._state['components'][component] = component_state
            safe_write_json(self.health_file, self._state)

    def fail(self, component: str, error: Exception):
        self.beat(component, status='error', details={'error': str(error)[:200]})

class UpdateStateManager:
    def __init__(self, state_file: Path, server_name: str):
        self.state_file = state_file
        self.server_name = server_name

    def _get_server_state(self, data: Dict) -> Dict:
        server_state = data.setdefault(self.server_name, {})
        server_state.setdefault('containers', {})
        server_state['updated_at'] = time.time()
        return server_state

    def get_container_state(self, container: str) -> Dict:
        data = safe_read_json(self.state_file, default={})
        server_state = data.get(self.server_name, {})
        containers = server_state.get('containers', {})
        return dict(containers.get(container, {}))

    def set_container_state(self, container: str, state: Dict):
        def updater(data: Dict) -> Dict:
            server_state = self._get_server_state(data)
            server_state['containers'][container] = state
            return data

        safe_update_json(self.state_file, updater, default={})

    def prune_containers(self, active_containers: Set[str]):
        def updater(data: Dict) -> Dict:
            server_state = self._get_server_state(data)
            containers = server_state.get('containers', {})
            for name in list(containers.keys()):
                if name not in active_containers:
                    containers.pop(name, None)
            return data

        safe_update_json(self.state_file, updater, default={})

class RemoteCommandQueue:
    def __init__(self, queue_file: Path):
        self.queue_file = queue_file

    def enqueue(self, target_server: str, action: str, payload: Dict) -> str:
        job_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

        def updater(data: Dict) -> Dict:
            jobs = data.setdefault('jobs', [])
            jobs.append({
                'id': job_id,
                'target_server': target_server,
                'action': action,
                'payload': payload,
                'status': 'pending',
                'created_at': time.time()
            })
            cutoff = time.time() - 86400
            data['jobs'] = [job for job in jobs if job.get('created_at', 0) >= cutoff and job.get('status') != 'done']
            return data

        safe_update_json(self.queue_file, updater, default={})
        return job_id

    def count_pending(self, target_server: Optional[str] = None) -> int:
        data = safe_read_json(self.queue_file, default={})
        jobs = data.get('jobs', [])
        if target_server is None:
            return sum(1 for job in jobs if job.get('status') in {'pending', 'processing'})
        return sum(
            1 for job in jobs
            if job.get('target_server') == target_server and job.get('status') in {'pending', 'processing'}
        )

    def claim(self, target_server: str) -> Optional[Dict]:
        claimed = {}

        def updater(data: Dict) -> Dict:
            jobs = data.setdefault('jobs', [])
            for job in jobs:
                if job.get('target_server') != target_server or job.get('status') != 'pending':
                    continue
                job['status'] = 'processing'
                job['claimed_at'] = time.time()
                claimed.update(job)
                break
            return data

        safe_update_json(self.queue_file, updater, default={})
        return claimed or None

    def complete(self, job_id: str, error: Optional[str] = None):
        def updater(data: Dict) -> Dict:
            jobs = data.setdefault('jobs', [])
            for job in jobs:
                if job.get('id') == job_id:
                    job['status'] = 'done'
                    job['completed_at'] = time.time()
                    if error:
                        job['error'] = error[:300]
                    break
            cutoff = time.time() - 3600
            data['jobs'] = [job for job in jobs if not (job.get('status') == 'done' and job.get('completed_at', 0) < cutoff)]
            return data

        safe_update_json(self.queue_file, updater, default={})

class CommandCoordinator:
   def __init__(self, server_name: str, primary_server: bool, registry_file: Path):
       self.server_name = server_name
       self.is_primary = primary_server
       self.registry_file = registry_file
       logger.info(f"协调器初始化: 当前={server_name}, 是否主服务器={self.is_primary}")

   def should_handle_command(self, command: str, callback_data: str = None) -> bool:
       if callback_data:
           return self._should_handle_callback(callback_data)

       global_commands = ['/start']
       if any(command.startswith(cmd) for cmd in global_commands):
           return True

       coordinated_commands = ['/status', '/update', '/restart', '/monitor', '/help', '/servers']

       if not any(command.startswith(cmd) for cmd in coordinated_commands):
           return True

       if self.is_primary:
           logger.info(f"✓ 作为主服务器处理命令: {command}")
           return True
       else:
           logger.info(f"✗ 从服务器忽略协调命令: {command} (应由主服务器处理)")
           return False

   def _should_handle_callback(self, callback_data: str) -> bool:
       parts = callback_data.split(':')
       action = parts[0]

       if self.is_primary:
           return True

       logger.debug(f"从服务器忽略回调: {action} (仅主服务器处理 Bot 回调)")
       return False

class TelegramBot:
    def __init__(self, token: str, chat_id: str, server_name: str):
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.server_name = server_name
        self.session = requests.Session()
        self.session.headers.update({'Connection': 'keep-alive'})
        self._last_edit = {}

    def _request(self, endpoint: str, payload: Dict, timeout: int = 30,
                 max_retries: int = 3, allow_plain_fallback: bool = False) -> Dict:
        current_payload = dict(payload)

        for attempt in range(max_retries):
            try:
                response = self.session.post(
                    f"{self.api_url}/{endpoint}",
                    json=current_payload,
                    timeout=timeout
                )

                try:
                    data = response.json()
                except Exception:
                    data = {'ok': False, 'description': response.text[:200] or '未知错误'}

                if response.status_code == 200 and data.get('ok'):
                    return {'ok': True, 'data': data}

                description = str(data.get('description', response.text[:200] or '未知错误'))
                description_lower = description.lower()

                if allow_plain_fallback and current_payload.get('parse_mode') == 'HTML':
                    if 'parse entities' in description_lower or "can\'t parse" in description_lower:
                        logger.warning('Telegram HTML 解析失败，尝试纯文本回退发送')
                        current_payload.pop('parse_mode', None)
                        current_payload['text'] = strip_html(str(current_payload.get('text', '')))
                        allow_plain_fallback = False
                        continue

                if endpoint == 'editMessageText' and 'message is not modified' in description_lower:
                    return {'ok': True, 'data': data}

                if response.status_code == 429:
                    retry_after = int(data.get('parameters', {}).get('retry_after', attempt + 1))
                    logger.warning(f"Telegram 触发限流，{retry_after} 秒后重试")
                    time.sleep(min(max(retry_after, 1), 60))
                    continue

                logger.error(f"Telegram API 错误 [{endpoint}]: {description}")

            except Exception as e:
                logger.error(f"Telegram 请求失败 [{endpoint}]: {e}")

            if attempt < max_retries - 1:
                time.sleep(min((attempt + 1) * 2, 10))

        return {'ok': False, 'data': {}}

    def send_message(self, text: str, reply_markup: Optional[Dict] = None,
                     max_retries: int = 3) -> bool:
        payload = {
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        if reply_markup:
            payload['reply_markup'] = json.dumps(reply_markup)

        result = self._request(
            'sendMessage',
            payload,
            timeout=30,
            max_retries=max_retries,
            allow_plain_fallback=True
        )
        return result.get('ok', False)

    def edit_message(self, chat_id: str, message_id: str, text: str,
                     reply_markup: Optional[Dict] = None, max_retries: int = 3) -> bool:
        edit_key = f"{chat_id}:{message_id}"
        current_time = time.time()
        if edit_key in self._last_edit and current_time - self._last_edit[edit_key] < 0.5:
            logger.debug(f"跳过频繁编辑: {message_id}")
            return False

        self._last_edit[edit_key] = current_time
        payload = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        if reply_markup:
            payload['reply_markup'] = json.dumps(reply_markup)

        result = self._request(
            'editMessageText',
            payload,
            timeout=30,
            max_retries=max_retries,
            allow_plain_fallback=True
        )
        if result.get('ok'):
            return True

        return False

    def answer_callback(self, callback_query_id: str, text: str = '', show_alert: bool = False) -> bool:
        payload = {
            'callback_query_id': callback_query_id,
            'show_alert': show_alert
        }
        if text:
            payload['text'] = text

        result = self._request('answerCallbackQuery', payload, timeout=10, max_retries=2)
        return result.get('ok', False)

    def get_updates(self, offset: int = 0, timeout: int = 30) -> Optional[List]:
        payload = {'offset': offset, 'timeout': timeout}
        result = self._request('getUpdates', payload, timeout=timeout + 10, max_retries=2)
        if result.get('ok'):
            return result['data'].get('result', [])
        return None

class DockerManager:
    @staticmethod
    def _run(command: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)

    @staticmethod
    def has_watchtower_deployment() -> bool:
        try:
            result = DockerManager._run(
                ['docker', 'ps', '-a', '--format', '{{.Names}}	{{.Image}}'],
                timeout=15
            )
            if result.returncode != 0:
                return False

            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                name, _, image = line.partition('	')
                name = name.strip().lower()
                image = image.strip().lower()
                if name == 'watchtower' or image.startswith('containrrr/watchtower'):
                    return True
            return False
        except Exception as e:
            logger.debug(f'检测 watchtower 部署失败: {e}')
            return False

    @staticmethod
    def get_all_containers() -> List[str]:
        try:
            result = DockerManager._run(
                ['docker', 'ps', '--format', '{{.Names}}'],
                timeout=10
            )
            if result.returncode == 0:
                containers = result.stdout.strip().split('\n')
                return [
                    container for container in containers
                    if container and container not in ['watchtower', 'watchtower-notifier']
                ]
        except Exception as e:
            logger.error(f'获取容器列表失败: {e}')
        return []

    @staticmethod
    def get_container_inspect(container: str) -> Dict:
        try:
            result = DockerManager._run(['docker', 'inspect', container], timeout=10)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if data:
                    return data[0]
        except Exception as e:
            logger.error(f'获取容器 {container} 详情失败: {e}')
        return {}

    @staticmethod
    def get_container_info(container: str) -> Dict:
        info = DockerManager.get_container_inspect(container)
        if info:
            state = info.get('State', {})
            return {
                'name': container,
                'running': state.get('Running', False),
                'health': (state.get('Health') or {}).get('Status'),
                'image': info.get('Config', {}).get('Image', 'unknown'),
                'image_id': info.get('Image', 'unknown'),
                'created': info.get('Created', '')
            }
        return {}

    @staticmethod
    def get_image_id(image: str) -> str:
        try:
            result = DockerManager._run(
                ['docker', 'inspect', '--format', '{{.Id}}', image],
                timeout=15
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            logger.debug(f'获取镜像 {image} ID 失败: {e}')
        return ''

    @staticmethod
    def pull_image(image: str, timeout: int = 300) -> Dict:
        result = {
            'success': False,
            'image': image,
            'image_id': '',
            'message': ''
        }

        try:
            pull_result = DockerManager._run(['docker', 'pull', image], timeout=timeout)
            if pull_result.returncode != 0:
                result['message'] = (pull_result.stderr or pull_result.stdout or '拉取失败')[:300]
                return result

            result['image_id'] = DockerManager.get_image_id(image)
            if not result['image_id']:
                result['message'] = '拉取成功，但无法读取最新镜像 ID'
                return result

            result['success'] = True
            return result
        except subprocess.TimeoutExpired:
            result['message'] = '拉取镜像超时'
            return result
        except Exception as e:
            result['message'] = f'拉取镜像异常: {str(e)[:200]}'
            return result

    @staticmethod
    def wait_container_ready(container: str, timeout: int = 90) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = DockerManager.get_container_inspect(container)
            if not info:
                time.sleep(2)
                continue

            state = info.get('State', {})
            if not state.get('Running', False):
                time.sleep(2)
                continue

            health = state.get('Health')
            if not health:
                return True

            status = health.get('Status')
            if status == 'healthy':
                return True
            if status == 'unhealthy':
                return False

            time.sleep(2)

        return False

    @staticmethod
    def restart_container(container: str) -> bool:
        try:
            result = DockerManager._run(['docker', 'restart', container], timeout=60)
            if result.returncode != 0:
                return False
            return DockerManager.wait_container_ready(container, timeout=60)
        except Exception as e:
            logger.error(f'重启容器 {container} 失败: {e}')
            return False

    @staticmethod
    def update_container(container: str, progress_callback=None) -> Dict:
        result = {
            'success': False,
            'message': '',
            'old_version': '',
            'new_version': '',
            'busy': False
        }

        try:
            with FileLock(container_lock_path(container), timeout=1):
                return DockerManager._update_container_internal(container, progress_callback)
        except TimeoutError:
            result['busy'] = True
            result['message'] = '容器正在执行其他更新任务，请稍后再试'
            return result

    @staticmethod
    def _update_container_internal(container: str, progress_callback=None) -> Dict:
        result = {
            'success': False,
            'message': '',
            'old_version': '',
            'new_version': ''
        }

        backup_tag = None
        config = {}
        old_image_id = ''

        def build_run_cmd(container_config: Dict, image_ref: str) -> List[str]:
            host_config = container_config.get('HostConfig', {})
            config_section = container_config.get('Config', {})
            run_cmd = ['docker', 'run', '-d', '--name', container]

            network_mode = host_config.get('NetworkMode', 'bridge')
            if network_mode:
                run_cmd.extend(['--network', network_mode])

            restart_policy = host_config.get('RestartPolicy', {}) or {}
            restart_name = restart_policy.get('Name')
            if restart_name:
                if restart_name == 'on-failure' and restart_policy.get('MaximumRetryCount'):
                    run_cmd.extend(['--restart', f"{restart_name}:{restart_policy['MaximumRetryCount']}"])
                else:
                    run_cmd.extend(['--restart', restart_name])

            if host_config.get('Privileged'):
                run_cmd.append('--privileged')
            if host_config.get('ReadonlyRootfs'):
                run_cmd.append('--read-only')
            if host_config.get('ShmSize'):
                run_cmd.extend(['--shm-size', str(host_config['ShmSize'])])
            if host_config.get('NanoCpus'):
                run_cmd.extend(['--cpus', str(host_config['NanoCpus'] / 1_000_000_000)])
            if host_config.get('Memory'):
                run_cmd.extend(['--memory', str(host_config['Memory'])])
            if host_config.get('PidsLimit') and host_config.get('PidsLimit') > 0:
                run_cmd.extend(['--pids-limit', str(host_config['PidsLimit'])])

            if config_section.get('WorkingDir'):
                run_cmd.extend(['-w', config_section['WorkingDir']])
            if config_section.get('User'):
                run_cmd.extend(['-u', config_section['User']])
            if config_section.get('Hostname'):
                run_cmd.extend(['--hostname', config_section['Hostname']])

            for env in config_section.get('Env', []) or []:
                run_cmd.extend(['-e', env])

            labels = config_section.get('Labels') or {}
            for key, value in labels.items():
                if key.startswith('com.docker.compose.'):
                    continue
                run_cmd.extend(['--label', key if value in (None, '') else f'{key}={value}'])

            log_config = host_config.get('LogConfig', {}) or {}
            if log_config.get('Type'):
                run_cmd.extend(['--log-driver', log_config['Type']])
                for key, value in (log_config.get('Config') or {}).items():
                    run_cmd.extend(['--log-opt', f'{key}={value}'])

            healthcheck = config_section.get('Healthcheck') or {}
            test = healthcheck.get('Test') or []
            if test:
                if test[0] == 'CMD-SHELL' and len(test) > 1:
                    run_cmd.extend(['--health-cmd', test[1]])
                elif test[0] == 'CMD' and len(test) > 1:
                    run_cmd.extend(['--health-cmd', ' '.join(shlex.quote(part) for part in test[1:])])
                elif test[0] == 'NONE':
                    run_cmd.extend(['--no-healthcheck'])

                if healthcheck.get('Interval'):
                    run_cmd.extend(['--health-interval', f"{max(int(healthcheck['Interval'] / 1_000_000_000), 1)}s"])
                if healthcheck.get('Timeout'):
                    run_cmd.extend(['--health-timeout', f"{max(int(healthcheck['Timeout'] / 1_000_000_000), 1)}s"])
                if healthcheck.get('StartPeriod'):
                    run_cmd.extend(['--health-start-period', f"{max(int(healthcheck['StartPeriod'] / 1_000_000_000), 1)}s"])
                if healthcheck.get('Retries') is not None:
                    run_cmd.extend(['--health-retries', str(healthcheck['Retries'])])

            for extra_host in host_config.get('ExtraHosts') or []:
                run_cmd.extend(['--add-host', extra_host])
            for cap in host_config.get('CapAdd') or []:
                run_cmd.extend(['--cap-add', cap])
            for cap in host_config.get('CapDrop') or []:
                run_cmd.extend(['--cap-drop', cap])
            for dns in host_config.get('Dns') or []:
                run_cmd.extend(['--dns', dns])
            for dns_search in host_config.get('DnsSearch') or []:
                run_cmd.extend(['--dns-search', dns_search])

            for mount in container_config.get('Mounts', []) or []:
                source = mount.get('Name') or mount.get('Source')
                destination = mount.get('Destination')
                if not source or not destination:
                    continue

                mount_spec = f'{source}:{destination}'
                mode = mount.get('Mode') or ''
                if mode:
                    mount_spec += f':{mode}'
                elif mount.get('RW') is False:
                    mount_spec += ':ro'
                run_cmd.extend(['-v', mount_spec])

            if network_mode != 'host':
                port_bindings = host_config.get('PortBindings', {}) or {}
                for container_port, host_configs in port_bindings.items():
                    for host_cfg in host_configs or []:
                        host_port = host_cfg.get('HostPort', '')
                        if not host_port:
                            continue
                        host_ip = host_cfg.get('HostIp', '')
                        mapping = ''
                        if host_ip and host_ip != '0.0.0.0':
                            mapping += f'{host_ip}:'
                        mapping += f'{host_port}:{container_port}'
                        run_cmd.extend(['-p', mapping])

            entrypoint = config_section.get('Entrypoint')
            cmd_args = []
            if isinstance(entrypoint, list) and entrypoint:
                run_cmd.extend(['--entrypoint', entrypoint[0]])
                cmd_args.extend(entrypoint[1:])
            elif isinstance(entrypoint, str) and entrypoint:
                run_cmd.extend(['--entrypoint', entrypoint])

            cmd = config_section.get('Cmd')
            if isinstance(cmd, list):
                cmd_args.extend(cmd)
            elif isinstance(cmd, str) and cmd:
                cmd_args.append(cmd)

            run_cmd.append(image_ref)
            run_cmd.extend(cmd_args)
            return run_cmd

        def rollback_container(reason: str) -> str:
            if not ENABLE_ROLLBACK or not backup_tag:
                return reason

            logger.warning(f'更新失败，准备自动回滚容器 {container}')
            if progress_callback:
                progress_callback('↩️ 更新失败，正在自动回滚...')

            DockerManager._run(['docker', 'rm', '-f', container], timeout=30)
            rollback_cmd = build_run_cmd(config, backup_tag)
            rollback_result = DockerManager._run(rollback_cmd, timeout=60)
            if rollback_result.returncode == 0 and DockerManager.wait_container_ready(container, timeout=90):
                return f'{reason}；已自动回滚到旧镜像'

            rollback_message = (rollback_result.stderr or rollback_result.stdout or '未知错误')[:200]
            return f'{reason}；自动回滚失败: {rollback_message}'

        try:
            if progress_callback:
                progress_callback('📋 正在获取容器信息...')

            old_info = DockerManager.get_container_info(container)
            if not old_info:
                result['message'] = '无法获取容器信息'
                return result

            config = DockerManager.get_container_inspect(container)
            if not config:
                result['message'] = '无法获取容器配置'
                return result

            image = old_info['image']
            old_image_id = old_info['image_id']
            result['old_version'] = DockerManager._format_version_info(old_info, container)

            if ENABLE_ROLLBACK:
                safe_container = sanitize_file_component(container.lower())
                backup_tag = f'watchtower-rollback/{safe_container}:{int(time.time())}'
                backup_result = DockerManager._run(
                    ['docker', 'image', 'tag', old_image_id, backup_tag],
                    timeout=20
                )
                if backup_result.returncode != 0:
                    logger.warning(f"创建回滚镜像标签失败: {(backup_result.stderr or backup_result.stdout)[:200]}")
                    backup_tag = None

            if progress_callback:
                progress_callback(f'🔄 正在拉取镜像: {image}')

            pull_result = DockerManager.pull_image(image)
            if not pull_result['success']:
                result['message'] = f"拉取镜像失败: {pull_result['message']}"
                return result

            new_image_id = pull_result['image_id']
            if new_image_id == old_image_id:
                result['success'] = True
                result['new_version'] = result['old_version']
                result['message'] = '镜像已是最新版本，无需更新'
                return result

            if progress_callback:
                progress_callback('⏸️ 正在停止旧容器...')

            stop_result = DockerManager._run(['docker', 'stop', container], timeout=30)
            if stop_result.returncode != 0:
                result['message'] = f"停止旧容器失败: {(stop_result.stderr or stop_result.stdout)[:200]}"
                return result

            if progress_callback:
                progress_callback('🗑️ 正在删除旧容器...')

            rm_result = DockerManager._run(['docker', 'rm', container], timeout=10)
            if rm_result.returncode != 0:
                result['message'] = f"删除旧容器失败: {(rm_result.stderr or rm_result.stdout)[:200]}"
                return result

            if progress_callback:
                progress_callback('🚀 正在启动新容器...')

            run_cmd = build_run_cmd(config, image)
            run_result = DockerManager._run(run_cmd, timeout=60)
            if run_result.returncode != 0:
                result['message'] = rollback_container(
                    f"启动新容器失败: {(run_result.stderr or run_result.stdout)[:200]}"
                )
                return result

            if DockerManager.wait_container_ready(container, timeout=90):
                new_info = DockerManager.get_container_info(container)
                result['new_version'] = DockerManager._format_version_info(new_info or {
                    'image': image,
                    'image_id': new_image_id
                }, container)
                result['success'] = True
                result['message'] = '容器更新成功'
            else:
                result['message'] = rollback_container('容器启动失败，请检查日志')

            return result

        except subprocess.TimeoutExpired:
            result['message'] = rollback_container('操作超时') if config else '操作超时'
            return result
        except Exception as e:
            result['message'] = rollback_container(f'更新失败: {str(e)[:200]}') if config else f'更新失败: {str(e)[:200]}'
            logger.error(f'更新容器 {container} 失败: {e}')
            return result
        finally:
            if backup_tag and result.get('success'):
                DockerManager._run(['docker', 'image', 'rm', backup_tag], timeout=20)

            if result.get('success') and CLEANUP_OLD_IMAGES and old_image_id:
                DockerManager._run(['docker', 'image', 'rm', old_image_id], timeout=20)

    @staticmethod
    def _format_version_info(info: Dict, container: str) -> str:
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
        if 'danmu' not in container.lower():
            return None

        try:
            for _ in range(30):
                check = DockerManager._run(
                    ['docker', 'exec', container, 'test', '-f', '/app/danmu_api/configs/globals.js'],
                    timeout=5
                )
                if check.returncode == 0:
                    break
                time.sleep(1)

            result = DockerManager._run(
                ['docker', 'exec', container, 'cat', '/app/danmu_api/configs/globals.js'],
                timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'VERSION:' in line:
                        match = re.search(r"VERSION:\s*['\"]([^'\"]+)['\"]", line)
                        if match:
                            return match.group(1)
        except Exception as e:
            logger.debug(f'获取 danmu 版本失败: {e}')

        return None

class ConfigManager:
    def __init__(self, config_file: Path, server_name: str):
        self.config_file = config_file
        self.server_name = server_name
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        return safe_read_json(self.config_file, default={})

    def _refresh_config(self) -> Dict:
        self.config = self._load_config()
        return self.config

    def _update_config(self, updater: Callable[[Dict], Dict]):
        updated = safe_update_json(self.config_file, updater, default={})
        if updated is None:
            return False
        self.config = updated
        return True

    def get_excluded_containers(self, server: Optional[str] = None) -> Set[str]:
        server = server or self.server_name
        config = self._refresh_config()
        return set(config.get(server, {}).get('excluded', []))

    def add_excluded(self, container: str, server: Optional[str] = None):
        server = server or self.server_name

        def updater(config: Dict) -> Dict:
            if server not in config:
                config[server] = {'excluded': []}

            excluded = set(config[server].get('excluded', []))
            excluded.add(container)
            config[server]['excluded'] = sorted(list(excluded))
            return config

        self._update_config(updater)

    def remove_excluded(self, container: str, server: Optional[str] = None):
        server = server or self.server_name

        def updater(config: Dict) -> Dict:
            if server not in config:
                return config

            excluded = set(config[server].get('excluded', []))
            excluded.discard(container)
            config[server]['excluded'] = sorted(list(excluded))
            return config

        self._update_config(updater)

    def get_static_monitored_containers(self) -> Set[str]:
        return set(STATIC_MONITORED_CONTAINERS)

    def has_static_monitor_list(self) -> bool:
        return bool(STATIC_MONITORED_CONTAINERS)

    def is_monitored(self, container: str, server: Optional[str] = None) -> bool:
        if container in self.get_excluded_containers(server):
            return False

        static_containers = self.get_static_monitored_containers()
        if static_containers:
            return container in static_containers

        return True

class ServerRegistry:
    def __init__(self, registry_file: Path, server_name: str, is_primary: bool):
        self.registry_file = registry_file
        self.server_name = server_name
        self.is_primary = is_primary
        self.heartbeat_interval = 30
        self.timeout = 120
        self.current_mode = 'unknown'

    def set_mode(self, mode: str):
        self.current_mode = mode

    def register(self):
        all_containers = DockerManager.get_all_containers()
        config_manager = ConfigManager(MONITOR_CONFIG, self.server_name)
        monitored_containers = [c for c in all_containers if config_manager.is_monitored(c)]

        def updater(registry: Dict) -> Dict:
            registry[self.server_name] = {
                'last_heartbeat': time.time(),
                'version': VERSION,
                'is_primary': self.is_primary,
                'container_count': len(monitored_containers),
                'mode': self.current_mode
            }
            return registry

        updated = safe_update_json(self.registry_file, updater, default={})
        if updated is not None:
            role = "主服务器 🌟" if self.is_primary else "从服务器"
            logger.info(f"服务器已注册: {self.server_name} ({role}) - 容器: {len(monitored_containers)}个")
        else:
            logger.error(f"服务器注册失败: {self.server_name}")

    def heartbeat(self):
        all_containers = DockerManager.get_all_containers()
        config_manager = ConfigManager(MONITOR_CONFIG, self.server_name)
        monitored_containers = [c for c in all_containers if config_manager.is_monitored(c)]

        def updater(registry: Dict) -> Dict:
            if self.server_name not in registry:
                logger.warning(f"服务器注册信息丢失，重新注册: {self.server_name}")

            registry[self.server_name] = {
                'last_heartbeat': time.time(),
                'version': VERSION,
                'is_primary': self.is_primary,
                'container_count': len(monitored_containers),
                'mode': self.current_mode
            }
            return registry

        safe_update_json(self.registry_file, updater, default={})

    def get_active_servers(self) -> List[str]:
        registry = safe_read_json(self.registry_file, default={})
        current_time = time.time()
        active_servers = []

        for server, info in registry.items():
            if current_time - info.get('last_heartbeat', 0) < self.timeout:
                active_servers.append(server)

        return sorted(active_servers)

class CommandHandler:
    def __init__(self, bot: TelegramBot, docker: DockerManager,
                 config: ConfigManager, registry: ServerRegistry):
        self.bot = bot
        self.docker = docker
        self.config = config
        self.registry = registry
        self.command_queue = RemoteCommandQueue(COMMAND_QUEUE_FILE)
        self._processing_callbacks = set()

    def _is_local_server(self, server: str) -> bool:
        return server == self.bot.server_name

    def _get_update_state(self) -> Dict:
        return safe_read_json(UPDATE_STATE_FILE, default={})

    def _get_server_snapshots(self, server: str) -> Dict[str, Dict]:
        if self._is_local_server(server):
            snapshots = {}
            for container in self.docker.get_all_containers():
                info = self.docker.get_container_info(container)
                if info:
                    snapshots[container] = {
                        'current_version': self.docker._format_version_info(info, container),
                        'running': info.get('running', False),
                        'health': info.get('health'),
                        'image': info.get('image', 'unknown')
                    }
            return snapshots

        data = self._get_update_state()
        return dict(data.get(server, {}).get('containers', {}))

    def _get_server_containers(self, server: str) -> List[str]:
        return sorted(self._get_server_snapshots(server).keys())

    def _get_server_update_meta(self, server: str) -> Dict:
        data = self._get_update_state()
        return dict(data.get(server, {}))

    def _send_or_edit(self, chat_id: str, text: str, reply_markup: Optional[Dict] = None,
                      message_id: Optional[str] = None):
        if message_id:
            self.bot.edit_message(chat_id, message_id, text, reply_markup)
        else:
            self.bot.send_message(text, reply_markup)

    def handle_servers(self, chat_id: str):
        servers = self.registry.get_active_servers()
        registry_data = safe_read_json(self.registry.registry_file, default={})
        if not servers:
            self.bot.send_message('⚠️ 当前没有活跃的服务器')
            return

        primary_server = next(
            (server for server, info in registry_data.items() if info.get('is_primary', False)),
            None
        )

        server_msg = f"🌐 <b>在线服务器 ({len(servers)})</b>\n\n"
        for server in servers:
            server_info = registry_data.get(server, {})
            last_heartbeat = server_info.get('last_heartbeat', 0)
            time_diff = time.time() - last_heartbeat
            if time_diff < 30:
                time_text = '刚刚'
            elif time_diff < 60:
                time_text = f'{int(time_diff)}秒前'
            else:
                minutes = int(time_diff / 60)
                time_text = f'{minutes}分钟前' if minutes < 60 else f'{int(minutes/60)}小时前'
            container_count = server_info.get('container_count', 0)
            marker = ' 🌟' if server_info.get('is_primary', False) else ''
            server_msg += f"🖥️ <b>{escape_html(server)}{marker}</b> ({container_count}个容器)\n"
            server_msg += f"   最后心跳: {time_text}\n\n"

        server_msg += '━━━━━━━━━━━━━━━━━━━━\n'
        server_msg += f"💡 主服务器: <code>{escape_html(primary_server or '未设置')}</code>\n"
        server_msg += f"⏰ 更新时间: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
        self.bot.send_message(server_msg)

    def handle_status(self, chat_id: str):
        servers = self.registry.get_active_servers()
        if len(servers) > 1:
            buttons = {
                'inline_keyboard': [
                    [{'text': f'🖥️ {server}', 'callback_data': f'status_srv:{server}'}]
                    for server in servers
                ]
            }
            self.bot.send_message('📊 <b>选择要查看状态的服务器：</b>', buttons)
        else:
            self._show_server_status(chat_id, servers[0] if servers else SERVER_NAME)

    def _show_server_status(self, chat_id: str, server: str, message_id: Optional[str] = None):
        snapshots = self._get_server_snapshots(server)
        all_containers = sorted(snapshots.keys())
        monitored = [container for container in all_containers if self.config.is_monitored(container, server)]
        excluded = sorted(self.config.get_excluded_containers(server))
        update_meta = self._get_server_update_meta(server)
        registry_data = safe_read_json(self.registry.registry_file, default={})
        server_registry = registry_data.get(server, {})
        mode = server_registry.get('mode', 'unknown')
        last_sync = update_meta.get('updated_at')
        queue_count = self.command_queue.count_pending(server)
        sync_text = datetime.fromtimestamp(last_sync).strftime('%Y-%m-%d %H:%M:%S') if last_sync else '未同步'

        status_msg = f"""📊 <b>服务器状态</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ <b>服务器信息</b>
  名称: <code>{escape_html(server)}</code>
  时间: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
  版本: <code>v{VERSION}</code>
  模式: <code>{escape_html(mode)}</code>
  同步: <code>{sync_text}</code>
  队列: <code>{queue_count}</code>

📦 <b>容器统计</b>
  总计: <code>{len(all_containers)}</code>
  监控中: <code>{len(monitored)}</code>
  已排除: <code>{len(excluded)}</code>

🔍 <b>监控列表</b>"""

        if not monitored:
            status_msg += "\n   <i>暂无监控容器</i>"
        else:
            for container in monitored:
                info = snapshots.get(container, {})
                running = info.get('running')
                status_icon = '✅' if running is True else '❌' if running is False else '⚪️'
                version = info.get('current_version') or info.get('latest_version') or info.get('image', 'unknown')
                status_msg += f"\n   {status_icon} <code>{escape_html(container)}</code> [{escape_html(version)}]"

        if excluded:
            status_msg += "\n\n🚫 <b>排除列表</b>"
            for container in excluded:
                status_msg += f"\n   • <code>{escape_html(container)}</code>"

        status_msg += "\n━━━━━━━━━━━━━━━━━━━━"
        self._send_or_edit(chat_id, status_msg, message_id=message_id)

    def handle_update(self, chat_id: str):
        servers = self.registry.get_active_servers()
        if not servers:
            self.bot.send_message('⚠️ 没有可用的服务器')
            return
        if len(servers) > 1:
            buttons = {
                'inline_keyboard': [
                    [{'text': f'🖥️ {server}', 'callback_data': f'update_srv:{server}'}]
                    for server in servers
                ]
            }
            self.bot.send_message('🔄 <b>选择要更新容器的服务器：</b>', buttons)
        else:
            self._show_update_containers(chat_id, servers[0])

    def _show_update_containers(self, chat_id: str, server: str, message_id: Optional[str] = None):
        containers = [container for container in self._get_server_containers(server) if self.config.is_monitored(container, server)]
        if not containers:
            self._send_or_edit(chat_id, f'⚠️ 服务器 <code>{escape_html(server)}</code> 没有可更新的容器', message_id=message_id)
            return
        buttons = {
            'inline_keyboard': [
                [{'text': f'📦 {container}', 'callback_data': f'update_cnt:{server}:{container}'}]
                for container in containers
            ]
        }
        text = f"🔄 <b>服务器 <code>{escape_html(server)}</code></b>\n\n请选择要更新的容器："
        self._send_or_edit(chat_id, text, buttons, message_id)

    def handle_restart(self, chat_id: str):
        servers = self.registry.get_active_servers()
        if not servers:
            self.bot.send_message('⚠️ 没有可用的服务器')
            return
        if len(servers) > 1:
            buttons = {
                'inline_keyboard': [
                    [{'text': f'🖥️ {server}', 'callback_data': f'restart_srv:{server}'}]
                    for server in servers
                ]
            }
            self.bot.send_message('🔄 <b>选择要重启容器的服务器：</b>', buttons)
        else:
            self._show_restart_containers(chat_id, servers[0])

    def _show_restart_containers(self, chat_id: str, server: str, message_id: Optional[str] = None):
        containers = self._get_server_containers(server)
        if not containers:
            self._send_or_edit(chat_id, f'⚠️ 服务器 <code>{escape_html(server)}</code> 没有可重启的容器', message_id=message_id)
            return
        buttons = {
            'inline_keyboard': [
                [{'text': f'🔄 {container}', 'callback_data': f'restart_cnt:{server}:{container}'}]
                for container in containers
            ]
        }
        text = f"🔄 <b>服务器 <code>{escape_html(server)}</code></b>\n\n请选择要重启的容器："
        self._send_or_edit(chat_id, text, buttons, message_id)

    def handle_monitor(self, chat_id: str):
        if self.config.has_static_monitor_list():
            static_list = "\n".join(
                f'   • <code>{escape_html(container)}</code>'
                for container in sorted(self.config.get_static_monitored_containers())
            )
            self.bot.send_message(
                '📡 <b>监控管理</b>\n\n'
                '当前启用了 <code>MONITORED_CONTAINERS</code> 固定名单模式。\n'
                '如需修改监控范围，请编辑部署环境变量后重启服务。\n\n'
                f'当前固定名单：\n{static_list if static_list else "   <i>未设置</i>"}'
            )
            return
        buttons = {
            'inline_keyboard': [
                [{'text': '➕ 添加监控', 'callback_data': 'monitor_action:add'}],
                [{'text': '➖ 移除监控', 'callback_data': 'monitor_action:remove'}],
                [{'text': '📋 查看列表', 'callback_data': 'monitor_action:list'}]
            ]
        }
        self.bot.send_message('📡 <b>监控管理</b>\n\n请选择操作：', buttons)

    def handle_help(self):
        servers = self.registry.get_active_servers()
        registry = safe_read_json(self.registry.registry_file, default={})
        server_lines = []
        for server in servers:
            info = registry.get(server, {})
            marker = ' 🌟' if info.get('is_primary', False) else ''
            server_lines.append(f'   • <code>{escape_html(server)}</code>{marker}')
        server_list = "\n".join(server_lines)
        help_msg = f"""📖 <b>命令帮助</b>

━━━━━━━━━━━━━━━━━━━━
<b>可用命令：</b>

/status - 查看服务器状态
/servers - 查看所有服务器概览
/update - 更新容器镜像
/restart - 重启容器
/monitor - 监控管理
/help - 显示此帮助信息

━━━━━━━━━━━━━━━━━━━━
<b>🌐 已连接服务器 ({len(servers)})：</b>
{server_list if servers else '   <i>暂无服务器</i>'}

━━━━━━━━━━━━━━━━━━━━
💡 <b>使用提示：</b>

• 仅主服务器轮询 Telegram Bot，避免 getUpdates 冲突
• 远程服务器的状态展示使用共享状态文件
• 远程服务器的重启/更新通过共享队列分发执行
• 设置 <code>MONITORED_CONTAINERS</code> 后将启用固定名单模式
• <code>ENABLE_ROLLBACK=true</code> 时，更新失败会自动回滚
━━━━━━━━━━━━━━━━━━━━"""
        self.bot.send_message(help_msg)

    def _execute_update(self, chat_id: str, message_id: str, server: str, container: str):
        current_msg = f'⏳ 正在更新容器 <code>{escape_html(container)}</code>...\n\n'
        self.bot.edit_message(chat_id, message_id, current_msg + '📋 准备更新...')
        last_progress = [time.time()]

        def progress_update(msg):
            if time.time() - last_progress[0] > 2:
                self.bot.edit_message(chat_id, message_id, current_msg + escape_html(msg))
                last_progress[0] = time.time()

        result = self.docker.update_container(container, progress_update)
        if result['success']:
            result_msg = f"""✅ <b>更新成功</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{escape_html(server)}</code>
📦 容器: <code>{escape_html(container)}</code>

🔄 <b>版本变更</b>
  旧: <code>{escape_html(result['old_version'])}</code>
  新: <code>{escape_html(result['new_version'])}</code>

⏰ 时间: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━

{escape_html(result['message'])}"""
        else:
            result_msg = f"""❌ <b>更新失败</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{escape_html(server)}</code>
📦 容器: <code>{escape_html(container)}</code>

❌ <b>错误信息</b>
  {escape_html(result['message'])}

💡 <b>建议</b>
  • 检查镜像名称是否正确
  • 查看容器日志排查问题
  • 尝试手动更新容器
━━━━━━━━━━━━━━━━━━━━"""
        self.bot.edit_message(chat_id, message_id, result_msg)

    def _execute_restart(self, chat_id: str, message_id: str, server: str, container: str):
        self.bot.edit_message(chat_id, message_id, f'⏳ 正在重启容器 <code>{escape_html(container)}</code>...')
        success = self.docker.restart_container(container)
        if success:
            result_msg = f"""✅ <b>重启成功</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{escape_html(server)}</code>
📦 容器: <code>{escape_html(container)}</code>
⏰ 时间: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━"""
        else:
            result_msg = f"""❌ <b>重启失败</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{escape_html(server)}</code>
📦 容器: <code>{escape_html(container)}</code>

请检查容器状态
━━━━━━━━━━━━━━━━━━━━"""
        self.bot.edit_message(chat_id, message_id, result_msg)

    def process_remote_job(self, job: Dict):
        action = job.get('action')
        payload = job.get('payload', {})
        chat_id = str(payload.get('chat_id', ''))
        message_id = str(payload.get('message_id', ''))
        server = payload.get('server', self.bot.server_name)
        container = payload.get('container', '')
        if action == 'confirm_update' and container:
            self._execute_update(chat_id, message_id, server, container)
        elif action == 'confirm_restart' and container:
            self._execute_restart(chat_id, message_id, server, container)

    def _enqueue_remote_action(self, action: str, server: str, container: str, chat_id: str, message_id: str):
        job_id = self.command_queue.enqueue(server, action, {
            'server': server,
            'container': container,
            'chat_id': chat_id,
            'message_id': message_id
        })
        waiting = '⏳ 已提交远程更新任务...' if action == 'confirm_update' else '⏳ 已提交远程重启任务...'
        waiting += f"\n\n🖥️ 目标服务器: <code>{escape_html(server)}</code>"
        waiting += f"\n📦 容器: <code>{escape_html(container)}</code>"
        waiting += f"\n🧾 任务号: <code>{escape_html(job_id)}</code>"
        waiting += '\n\n请稍候，目标服务器开始执行后会继续回写此消息。'
        self.bot.edit_message(chat_id, message_id, waiting)

    def handle_callback(self, callback_data: str, callback_query_id: str,
                        chat_id: str, message_id: str):
        callback_key = f'{callback_query_id}:{callback_data}' if callback_query_id else f'queued:{callback_data}:{message_id}'
        if callback_key in self._processing_callbacks:
            logger.debug(f'跳过重复回调: {callback_data}')
            return
        self._processing_callbacks.add(callback_key)

        try:
            parts = callback_data.split(':')
            action = parts[0]
            logger.info(f'处理回调: {callback_data}')
            if callback_query_id:
                self.bot.answer_callback(callback_query_id, '')
            time.sleep(0.2)

            if action == 'status_srv':
                self.bot.send_message(f'🖥️ 已选择服务器：<code>{escape_html(parts[1])}</code>')
                self._show_server_status(chat_id, parts[1])
            elif action == 'update_srv':
                self.bot.send_message(f'🖥️ 已选择服务器：<code>{escape_html(parts[1])}</code>')
                self._show_update_containers(chat_id, parts[1])
            elif action == 'update_cnt':
                server, container = parts[1], parts[2]
                confirm_msg = f"""⚠️ <b>确认更新</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{escape_html(server)}</code>
📦 容器: <code>{escape_html(container)}</code>

⚠️ <b>注意：</b>容器将短暂停止服务

是否继续？
━━━━━━━━━━━━━━━━━━━━"""
                buttons = {
                    'inline_keyboard': [
                        [{'text': '✅ 确认更新', 'callback_data': f'confirm_update:{server}:{container}'}],
                        [{'text': '❌ 取消', 'callback_data': 'cancel'}]
                    ]
                }
                self.bot.edit_message(chat_id, message_id, confirm_msg, buttons)
            elif action == 'confirm_update':
                server, container = parts[1], parts[2]
                if self._is_local_server(server):
                    threading.Thread(target=self._execute_update, args=(chat_id, message_id, server, container), daemon=True).start()
                else:
                    self._enqueue_remote_action(action, server, container, chat_id, message_id)
            elif action == 'restart_srv':
                self.bot.send_message(f'🖥️ 已选择服务器：<code>{escape_html(parts[1])}</code>')
                self._show_restart_containers(chat_id, parts[1])
            elif action == 'restart_cnt':
                server, container = parts[1], parts[2]
                confirm_msg = f"""⚠️ <b>确认重启</b>

━━━━━━━━━━━━━━━━━━━━
🖥️ 服务器: <code>{escape_html(server)}</code>
📦 容器: <code>{escape_html(container)}</code>

是否继续？
━━━━━━━━━━━━━━━━━━━━"""
                buttons = {
                    'inline_keyboard': [
                        [{'text': '✅ 确认重启', 'callback_data': f'confirm_restart:{server}:{container}'}],
                        [{'text': '❌ 取消', 'callback_data': 'cancel'}]
                    ]
                }
                self.bot.edit_message(chat_id, message_id, confirm_msg, buttons)
            elif action == 'confirm_restart':
                server, container = parts[1], parts[2]
                if self._is_local_server(server):
                    threading.Thread(target=self._execute_restart, args=(chat_id, message_id, server, container), daemon=True).start()
                else:
                    self._enqueue_remote_action(action, server, container, chat_id, message_id)
            elif action == 'monitor_action':
                action_type = parts[1]
                if action_type == 'list':
                    self.handle_status(chat_id)
                else:
                    servers = self.registry.get_active_servers()
                    if len(servers) == 1:
                        self._handle_monitor_server(chat_id, message_id, action_type, servers[0])
                    else:
                        buttons = {
                            'inline_keyboard': [
                                [{'text': f'🖥️ {server}', 'callback_data': f'monitor_srv:{action_type}:{server}'}]
                                for server in servers
                            ]
                        }
                        action_text = '添加监控' if action_type == 'add' else '移除监控'
                        self.bot.edit_message(chat_id, message_id, f'📡 <b>{action_text}</b>\n\n请选择服务器：', buttons)
            elif action == 'monitor_srv':
                self._handle_monitor_server(chat_id, message_id, parts[1], parts[2])
            elif action == 'add_mon':
                server, container = parts[1], parts[2]
                self.config.remove_excluded(container, server)
                self.bot.edit_message(chat_id, message_id, f'✅ <b>添加成功</b>\n\n已将 <code>{escape_html(container)}</code> 添加到服务器 <code>{escape_html(server)}</code> 的监控列表')
            elif action == 'rem_mon':
                server, container = parts[1], parts[2]
                self.config.add_excluded(container, server)
                self.bot.edit_message(chat_id, message_id, f'✅ <b>移除成功</b>\n\n已将 <code>{escape_html(container)}</code> 从服务器 <code>{escape_html(server)}</code> 的监控列表移除')
            elif action == 'cancel':
                self.bot.edit_message(chat_id, message_id, '❌ 操作已取消')
        except Exception as e:
            logger.error(f'处理回调异常: {e}')
        finally:
            def cleanup():
                time.sleep(2)
                self._processing_callbacks.discard(callback_key)
            threading.Thread(target=cleanup, daemon=True).start()

    def _handle_monitor_server(self, chat_id: str, message_id: str, action: str, server: str):
        if action == 'add':
            excluded = sorted(self.config.get_excluded_containers(server))
            if not excluded:
                self.bot.edit_message(chat_id, message_id, f'✅ 服务器 <code>{escape_html(server)}</code> 所有容器都已在监控中')
                return
            buttons = {
                'inline_keyboard': [
                    [{'text': f'➕ {container}', 'callback_data': f'add_mon:{server}:{container}'}]
                    for container in excluded
                ]
            }
            text = f'📡 <b>添加监控</b>\n\n🖥️ 服务器: <code>{escape_html(server)}</code>\n\n请选择要添加监控的容器：'
            self.bot.edit_message(chat_id, message_id, text, buttons)
        else:
            monitored = [container for container in self._get_server_containers(server) if self.config.is_monitored(container, server)]
            if not monitored:
                self.bot.edit_message(chat_id, message_id, f'⚠️ 服务器 <code>{escape_html(server)}</code> 当前没有监控中的容器')
                return
            buttons = {
                'inline_keyboard': [
                    [{'text': f'➖ {container}', 'callback_data': f'rem_mon:{server}:{container}'}]
                    for container in monitored
                ]
            }
            text = f'📡 <b>移除监控</b>\n\n🖥️ 服务器: <code>{escape_html(server)}</code>\n\n请选择要移除监控的容器：'
            self.bot.edit_message(chat_id, message_id, text, buttons)

class RemoteCommandWorker(threading.Thread):
    def __init__(self, handler: CommandHandler, queue: RemoteCommandQueue, server_name: str, health_reporter: HealthReporter):
        super().__init__(daemon=True)
        self.handler = handler
        self.queue = queue
        self.server_name = server_name
        self.health = health_reporter

    def run(self):
        logger.info('远程命令工作线程已启动')
        while not shutdown_flag.is_set():
            try:
                self.health.beat('remote_worker', details={'server': self.server_name})
                job = self.queue.claim(self.server_name)
                if not job:
                    time.sleep(1)
                    continue
                self.handler.process_remote_job(job)
                self.queue.complete(job['id'])
            except Exception as e:
                self.health.fail('remote_worker', e)
                logger.error(f'远程命令处理失败: {e}')
                time.sleep(2)

class BotPoller(threading.Thread):
    def __init__(self, handler: CommandHandler, bot: TelegramBot,
                 coordinator: CommandCoordinator, health_reporter: HealthReporter):
        super().__init__(daemon=True)
        self.handler = handler
        self.bot = bot
        self.coordinator = coordinator
        self.health = health_reporter
        self.last_update_id = 0
        self._processed_updates = set()

    def run(self):
        logger.info("Bot 轮询线程已启动")
        self.health.beat('bot_poller', details={'last_update_id': self.last_update_id})

        while not shutdown_flag.is_set():
            try:
                updates = self.bot.get_updates(self.last_update_id + 1, timeout=30)
                self.health.beat('bot_poller', details={'last_update_id': self.last_update_id})

                if not updates:
                    continue

                for update in updates:
                    update_id = update.get('update_id', 0)

                    if update_id in self._processed_updates:
                        continue

                    self._processed_updates.add(update_id)
                    self.last_update_id = update_id

                    if len(self._processed_updates) > 1000:
                        self._processed_updates = set(
                            sorted(self._processed_updates)[-500:]
                        )

                    message = update.get('message', {})
                    text_msg = message.get('text', '')
                    chat_id = str(message.get('chat', {}).get('id', ''))

                    if text_msg and chat_id == CHAT_ID:
                        if self.coordinator.should_handle_command(text_msg):
                            self._handle_command(text_msg, chat_id)
                        else:
                            logger.debug(f"命令被其他服务器处理: {text_msg}")

                    callback_query = update.get('callback_query', {})
                    if callback_query:
                        callback_data = callback_query.get('data', '')
                        if self.coordinator.should_handle_command(None, callback_data):
                            self._handle_callback(callback_query)
                        else:
                            logger.debug(f"回调被其他服务器处理: {callback_data}")

            except Exception as e:
                self.health.fail('bot_poller', e)
                logger.error(f"轮询错误: {e}")
                time.sleep(5)

    def _handle_command(self, text: str, chat_id: str):
        try:
            if text.startswith('/status'):
                self.handler.handle_status(chat_id)
            elif text.startswith('/update'):
                self.handler.handle_update(chat_id)
            elif text.startswith('/restart'):
                self.handler.handle_restart(chat_id)
            elif text.startswith('/monitor'):
                self.handler.handle_monitor(chat_id)
            elif text.startswith('/servers'):
                self.handler.handle_servers(chat_id)
            elif text.startswith('/help') or text.startswith('/start'):
                self.handler.handle_help()
        except Exception as e:
            logger.error(f"处理命令失败: {e}")

    def _handle_callback(self, callback_query: Dict):
        try:
            callback_data = callback_query.get('data', '')
            chat_id = str(callback_query.get('message', {}).get('chat', {}).get('id', ''))
            message_id = str(callback_query.get('message', {}).get('message_id', ''))

            if chat_id == CHAT_ID:
                self.handler.handle_callback(
                    callback_data,
                    callback_query.get('id', ''),
                    chat_id,
                    message_id
                )
        except Exception as e:
            logger.error(f"处理回调失败: {e}")

class HeartbeatThread(threading.Thread):
    def __init__(self, registry: ServerRegistry, health_reporter: HealthReporter):
        super().__init__(daemon=True)
        self.registry = registry
        self.health = health_reporter

    def run(self):
        logger.info("心跳线程已启动")
        self.health.beat('heartbeat', details={'interval': self.registry.heartbeat_interval})

        while not shutdown_flag.is_set():
            try:
                self.registry.heartbeat()
                self.health.beat('heartbeat', details={'interval': self.registry.heartbeat_interval})
                time.sleep(self.registry.heartbeat_interval)
            except Exception as e:
                self.health.fail('heartbeat', e)
                logger.error(f"心跳错误: {e}")
                time.sleep(5)

class WatchtowerMonitor:
    def __init__(self, bot: TelegramBot, docker: DockerManager,
                 config: ConfigManager, health_reporter: HealthReporter):
        self.bot = bot
        self.docker = docker
        self.config = config
        self.health = health_reporter
        self.session_data = {}
        self.state_store = UpdateStateManager(UPDATE_STATE_FILE, bot.server_name)

    def _publish_local_inventory(self):
        containers = sorted(self.docker.get_all_containers())
        self.state_store.prune_containers(set(containers))
        now = time.time()
        for container in containers:
            info = self.docker.get_container_info(container)
            if not info:
                continue
            self.state_store.set_container_state(container, {
                'image': info.get('image', 'unknown'),
                'current_image_id': info.get('image_id', 'unknown'),
                'current_version': self.docker._format_version_info(info, container),
                'running': info.get('running'),
                'health': info.get('health'),
                'last_checked_at': now
            })

    def _resolve_mode(self) -> str:
        if UPDATE_SOURCE == 'watchtower':
            return 'watchtower'
        if UPDATE_SOURCE == 'independent':
            return 'independent'
        return 'watchtower' if self.docker.has_watchtower_deployment() else 'independent'

    def start(self):
        mode = self._resolve_mode()
        logger.info(f'更新检测模式: {mode}')
        self._publish_local_inventory()
        self.health.beat('update_monitor', status='starting', details={'mode': mode})

        if mode == 'watchtower':
            self._start_watchtower_mode()
        else:
            self._start_independent_mode()

    def _start_watchtower_mode(self):
        logger.info('开始监控 Watchtower 日志...')
        process = None

        try:
            self._wait_for_watchtower()
            process = subprocess.Popen(
                ['docker', 'logs', '-f', '--tail', '0', 'watchtower'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            self.health.beat('update_monitor', details={'mode': 'watchtower', 'attached': True})

            while not shutdown_flag.is_set():
                if process.stdout is None:
                    raise RuntimeError('Watchtower 日志流不可用')

                ready, _, _ = select.select([process.stdout], [], [], 5)
                self.health.beat('update_monitor', details={'mode': 'watchtower'})

                if not ready:
                    if process.poll() is not None:
                        raise RuntimeError('Watchtower 日志进程已退出')
                    continue

                line = process.stdout.readline()
                if not line:
                    if process.poll() is not None:
                        raise RuntimeError('Watchtower 日志进程已退出')
                    continue

                line = line.strip()
                if not line:
                    continue

                logger.info(line)
                self._process_log_line(line)

        except Exception as e:
            self.health.fail('update_monitor', e)
            logger.error(f'监控 Watchtower 日志失败: {e}')
            raise
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

    def _start_independent_mode(self):
        logger.info('开始独立检查容器更新...')
        if INITIAL_CHECK_DELAY > 0:
            logger.info(f'首次检查延迟 {INITIAL_CHECK_DELAY} 秒')
            for _ in range(INITIAL_CHECK_DELAY):
                if shutdown_flag.is_set():
                    return
                time.sleep(1)

        while not shutdown_flag.is_set():
            cycle_started_at = time.time()
            try:
                self.health.beat('update_monitor', details={
                    'mode': 'independent',
                    'cycle_started_at': cycle_started_at,
                    'interval': CHECK_INTERVAL
                })
                self._run_independent_check_cycle()
                self.health.beat('update_monitor', details={
                    'mode': 'independent',
                    'cycle_started_at': cycle_started_at,
                    'cycle_finished_at': time.time(),
                    'interval': CHECK_INTERVAL
                })
            except Exception as e:
                self.health.fail('update_monitor', e)
                logger.exception(f'独立检查容器更新失败: {e}')

            elapsed = time.time() - cycle_started_at
            sleep_left = max(CHECK_INTERVAL - elapsed, 1)
            while sleep_left > 0 and not shutdown_flag.is_set():
                step = min(sleep_left, 1)
                time.sleep(step)
                sleep_left -= step

    def _run_independent_check_cycle(self):
        containers = sorted(
            container for container in self.docker.get_all_containers()
            if self.config.is_monitored(container)
        )
        self.state_store.prune_containers(set(containers))

        for container in containers:
            if shutdown_flag.is_set():
                break
            self._check_container_update(container)

    def _format_remote_version(self, image: str, image_id: str) -> str:
        image_short = image_id.replace('sha256:', '')[:12] if image_id else 'unknown'
        tag = image.split(':')[-1] if ':' in image else 'latest'
        return f'{tag} ({image_short})'

    def _send_update_available_notification(self, container: str, image: str,
                                            current_version: str, latest_version: str):
        message = f'''<b>[{escape_html(self.bot.server_name)}]</b> 🆕 <b>发现可用更新</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器名称</b>
  <code>{escape_html(container)}</code>

🎯 <b>镜像信息</b>
  <code>{escape_html(image)}</code>

🔄 <b>版本变更</b>
  当前: <code>{escape_html(current_version)}</code>
  最新: <code>{escape_html(latest_version)}</code>

⏰ <b>发现时间</b>
  <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━

💡 当前为仅通知模式，可通过 /update 手动更新'''
        self.bot.send_message(message)

    def _send_update_failure_notification(self, container: str, image: str,
                                          current_version: str, latest_version: str,
                                          error_message: str):
        message = f'''<b>[{escape_html(self.bot.server_name)}]</b> ❌ <b>自动更新失败</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器名称</b>
  <code>{escape_html(container)}</code>

🎯 <b>镜像信息</b>
  <code>{escape_html(image)}</code>

🔄 <b>版本变更</b>
  当前: <code>{escape_html(current_version)}</code>
  目标: <code>{escape_html(latest_version)}</code>

❌ <b>错误信息</b>
  {escape_html(error_message)}

⏰ <b>时间</b>
  <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━'''
        self.bot.send_message(message)

    def _send_check_error_notification(self, container: str, image: str, error_message: str):
        message = f'''<b>[{escape_html(self.bot.server_name)}]</b> ⚠️ <b>检查更新失败</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器名称</b>
  <code>{escape_html(container)}</code>

🎯 <b>镜像信息</b>
  <code>{escape_html(image)}</code>

❌ <b>错误信息</b>
  {escape_html(error_message)}

⏰ <b>时间</b>
  <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━'''
        self.bot.send_message(message)

    def _check_container_update(self, container: str):
        current_info = self.docker.get_container_info(container)
        if not current_info or not current_info.get('image') or not current_info.get('image_id'):
            logger.warning(f'跳过容器 {container}，无法获取当前镜像信息')
            return

        now = time.time()
        image = current_info['image']
        current_image_id = current_info['image_id']
        current_version = self.docker._format_version_info(current_info, container)
        state = self.state_store.get_container_state(container)
        new_state = dict(state)
        new_state.update({
            'image': image,
            'current_image_id': current_image_id,
            'current_version': current_version,
            'running': current_info.get('running'),
            'health': current_info.get('health'),
            'last_checked_at': now
        })

        pull_result = self.docker.pull_image(image)
        if not pull_result['success']:
            error_signature = f"{image}:{pull_result['message']}"[:300]
            if error_signature != state.get('last_error_signature'):
                self._send_check_error_notification(container, image, pull_result['message'])
                new_state['last_error_signature'] = error_signature
                new_state['last_error_notified_at'] = now
            self.state_store.set_container_state(container, new_state)
            return

        latest_image_id = pull_result['image_id']
        latest_version = self._format_remote_version(image, latest_image_id)
        new_state['last_error_signature'] = ''
        new_state['latest_image_id'] = latest_image_id
        new_state['latest_version'] = latest_version

        if latest_image_id == current_image_id:
            for key in [
                'available_image_id',
                'available_version',
                'last_failed_target_image_id',
                'last_failure_message',
                'last_notified_failure_image_id'
            ]:
                new_state.pop(key, None)
            self.state_store.set_container_state(container, new_state)
            return

        new_state['available_image_id'] = latest_image_id
        new_state['available_version'] = latest_version

        if not AUTO_UPDATE:
            if NOTIFY_ON_AVAILABLE_UPDATE and latest_image_id != state.get('last_notified_available_image_id'):
                self._send_update_available_notification(container, image, current_version, latest_version)
                new_state['last_notified_available_image_id'] = latest_image_id
                new_state['last_notified_available_at'] = now
            self.state_store.set_container_state(container, new_state)
            return

        last_attempt_at = float(state.get('last_attempt_at', 0) or 0)
        if latest_image_id == state.get('last_failed_target_image_id') and now - last_attempt_at < UPDATE_RETRY_BACKOFF:
            self.state_store.set_container_state(container, new_state)
            return

        logger.info(f'检测到容器 {container} 存在新版本，开始自动更新')
        new_state['last_attempt_at'] = now
        new_state['last_attempt_target_image_id'] = latest_image_id
        update_result = self.docker.update_container(container)

        refreshed_info = self.docker.get_container_info(container)
        if refreshed_info:
            new_state['current_image_id'] = refreshed_info.get('image_id', current_image_id)
            new_state['current_version'] = self.docker._format_version_info(refreshed_info, container)
            new_state['running'] = refreshed_info.get('running')
            new_state['health'] = refreshed_info.get('health')

        if update_result.get('busy'):
            logger.info(f'容器 {container} 正在被其他任务处理，本轮跳过自动更新')
        elif update_result['success']:
            if update_result['message'] != '镜像已是最新版本，无需更新':
                self._send_update_notification(
                    container,
                    image.split(':')[0],
                    update_result.get('old_version') or current_version,
                    update_result.get('new_version') or latest_version,
                    True
                )
            for key in [
                'available_image_id',
                'available_version',
                'last_failed_target_image_id',
                'last_failure_message',
                'last_notified_failure_image_id'
            ]:
                new_state.pop(key, None)
            new_state['last_success_image_id'] = latest_image_id
            new_state['last_success_at'] = time.time()
        else:
            new_state['last_failed_target_image_id'] = latest_image_id
            new_state['last_failure_message'] = update_result['message']
            if latest_image_id != state.get('last_notified_failure_image_id'):
                self._send_update_failure_notification(
                    container,
                    image,
                    current_version,
                    latest_version,
                    update_result['message']
                )
                new_state['last_notified_failure_image_id'] = latest_image_id
                new_state['last_failure_notified_at'] = time.time()

        self.state_store.set_container_state(container, new_state)

    def _wait_for_watchtower(self):
        logger.info('正在等待 Watchtower 容器启动...')
        for _ in range(60):
            try:
                result = subprocess.run(
                    ['docker', 'inspect', '-f', '{{.State.Running}}', 'watchtower'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and 'true' in result.stdout:
                    logger.info('Watchtower 已启动')
                    time.sleep(3)
                    return
            except Exception:
                pass
            time.sleep(2)
        logger.warning('Watchtower 启动超时，继续监控')

    def _process_log_line(self, line: str):
        try:
            if 'Stopping /' in line:
                container = self._extract_container_name(line, 'Stopping /')
                if container and self.config.is_monitored(container):
                    logger.info(f'→ 捕获到停止: {container}')
                    self._store_old_state(container)
            elif 'Session done' in line:
                match = re.search(r'Updated=(\d+)', line)
                if match:
                    updated = int(match.group(1))
                    logger.info(f'→ Session 完成: Updated={updated}')
                    if updated > 0 and self.session_data:
                        self._process_updates()
            elif 'level=error' in line.lower() or 'level=fatal' in line.lower():
                self._process_error(line)
        except Exception as e:
            logger.error(f'处理日志行失败: {e}')

    def _extract_container_name(self, line: str, prefix: str) -> Optional[str]:
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
        try:
            info = self.docker.get_container_info(container)
            if info:
                self.session_data[container] = {
                    'image': info.get('image', 'unknown'),
                    'image_id': info.get('image_id', 'unknown'),
                    'version': self.docker.get_danmu_version(container)
                }
                logger.info(f'  → 已暂存 {container} 的旧信息')
        except Exception as e:
            logger.error(f'存储旧状态失败: {e}')

    def _process_updates(self):
        logger.info(f'→ 发现 {len(self.session_data)} 个更新，开始处理...')
        for container, old_state in self.session_data.items():
            try:
                if not self.config.is_monitored(container):
                    logger.info(f'→ {container} 已被排除，跳过处理')
                    continue
                logger.info(f'→ 处理容器: {container}')
                time.sleep(5)
                for _ in range(60):
                    info = self.docker.get_container_info(container)
                    if info.get('running'):
                        logger.info('  → 容器已启动')
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
                logger.error(f'处理容器 {container} 更新失败: {e}')
        self.session_data.clear()
        logger.info('→ 所有更新处理完成')

    def _format_version(self, state: Dict, container: str) -> str:
        image_id = state.get('image_id', 'unknown')
        id_short = image_id.replace('sha256:', '')[:12]
        if 'danmu' in container.lower() and state.get('version'):
            return f"v{state['version']} ({id_short})"
        tag = state.get('image', 'unknown:latest').split(':')[-1]
        return f'{tag} ({id_short})'

    def _send_update_notification(self, container: str, image: str,
                                  old_ver: str, new_ver: str, running: bool):
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if running:
            message = f'''<b>[{escape_html(self.bot.server_name)}]</b> ✨ <b>容器更新成功</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器名称</b>
  <code>{escape_html(container)}</code>

🎯 <b>镜像信息</b>
  <code>{escape_html(image)}</code>

🔄 <b>版本变更</b>
  <code>{escape_html(old_ver)}</code>
  ➜
  <code>{escape_html(new_ver)}</code>

⏰ <b>更新时间</b>
  <code>{current_time}</code>
━━━━━━━━━━━━━━━━━━━━

✅ 容器已成功启动并运行正常'''
        else:
            message = f'''<b>[{escape_html(self.bot.server_name)}]</b> ❌ <b>容器启动失败</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器名称</b>
  <code>{escape_html(container)}</code>

🎯 <b>镜像信息</b>
  <code>{escape_html(image)}</code>

🔄 <b>版本变更</b>
  旧: <code>{escape_html(old_ver)}</code>
  新: <code>{escape_html(new_ver)}</code>

⏰ <b>更新时间</b>
  <code>{current_time}</code>
━━━━━━━━━━━━━━━━━━━━

⚠️ 更新后无法启动
💡 检查: <code>docker logs {escape_html(container)}</code>'''
        self.bot.send_message(message)

    def _process_error(self, line: str):
        if any(keyword in line.lower() for keyword in ['skipping', 'already up to date', 'no new images', 'connection refused', 'timeout']):
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
        if container and container not in ['watchtower', 'watchtower-notifier'] and self.config.is_monitored(container):
            error_msg = line[:200]
            self.bot.send_message(f'''<b>[{escape_html(self.bot.server_name)}]</b> ⚠️ <b>Watchtower 严重错误</b>

━━━━━━━━━━━━━━━━━━━━
📦 <b>容器</b>: <code>{escape_html(container)}</code>
🔴 <b>错误</b>: <code>{escape_html(error_msg)}</code>
🕐 <b>时间</b>: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━''')

def main():
    if not SERVER_NAME:
        logger.error("错误: 必须设置 SERVER_NAME 环境变量")
        sys.exit(1)

    if not CHAT_ID or not os.getenv('BOT_TOKEN'):
        logger.error("错误: 必须设置 BOT_TOKEN 和 CHAT_ID 环境变量")
        sys.exit(1)

    print('=' * 50)
    print(f"Docker 容器监控通知服务 v{VERSION}")
    print(f"服务器: {SERVER_NAME}")
    print(f"主服务器: {PRIMARY_SERVER}")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python 版本: {sys.version.split()[0]}")
    print('=' * 50)
    print()

    health = HealthReporter(HEALTH_FILE, SERVER_NAME)
    health.beat('main', status='starting')

    bot = TelegramBot(os.getenv('BOT_TOKEN'), CHAT_ID, SERVER_NAME)
    docker = DockerManager()
    config = ConfigManager(MONITOR_CONFIG, SERVER_NAME)
    registry = ServerRegistry(SERVER_REGISTRY, SERVER_NAME, PRIMARY_SERVER)
    coordinator = CommandCoordinator(SERVER_NAME, PRIMARY_SERVER, SERVER_REGISTRY)

    monitor = WatchtowerMonitor(bot, docker, config, health)
    resolved_mode = monitor._resolve_mode()
    registry.set_mode(resolved_mode)
    registry.register()

    if not PRIMARY_SERVER:
        logger.info("从服务器等待 0.5 秒...")
        time.sleep(0.5)

    handler = CommandHandler(bot, docker, config, registry)

    if PRIMARY_SERVER:
        bot_poller = BotPoller(handler, bot, coordinator, health)
        bot_poller.start()
    else:
        health.beat('bot_poller', status='disabled', details={'primary_server': False})

    remote_worker = RemoteCommandWorker(handler, handler.command_queue, SERVER_NAME, health)
    remote_worker.start()

    heartbeat = HeartbeatThread(registry, health)
    heartbeat.start()

    all_containers = docker.get_all_containers()
    monitored = [c for c in all_containers if config.is_monitored(c)]
    excluded = config.get_excluded_containers()

    logger.info(f"总容器: {len(all_containers)}, 监控: {len(monitored)}, 排除: {len(excluded)}")

    if PRIMARY_SERVER:
        time.sleep(1)
        registry_data = safe_read_json(SERVER_REGISTRY, default={})
        servers = registry.get_active_servers()
        primary_server = next(
            (server for server in servers if registry_data.get(server, {}).get('is_primary', False)),
            SERVER_NAME if PRIMARY_SERVER else '未知'
        )
        server_list = "\n".join([
            f"   • <code>{server}</code>{' 🌟' if registry_data.get(server, {}).get('is_primary', False) else ''}"
            for server in servers
        ]) or "   • <code>暂无活跃服务器</code>"

        startup_msg = f"""🚀 <b>监控服务启动成功</b>

━━━━━━━━━━━━━━━━━━━━
📊 <b>服务信息</b>
   版本: <code>v{VERSION}</code>
   主服务器: <code>{primary_server}</code> 🌟
   当前服务器: <code>{SERVER_NAME}</code>
   更新模式: <code>{resolved_mode}</code>
   语言: <code>Python {sys.version.split()[0]}</code>

🎯 <b>监控状态</b>
   总容器: <code>{len(all_containers)}</code>
   监控中: <code>{len(monitored)}</code>
   已排除: <code>{len(excluded)}</code>

🌐 <b>已连接服务器 ({len(servers)})</b>
{server_list}

🤖 <b>机器人功能</b>
   /status - 查看服务器状态
   /servers - 查看所有服务器概览
   /update - 更新容器镜像
   /restart - 重启容器
   /monitor - 监控管理
   /help - 显示帮助

💡 <b>本版重点</b>
   • 默认使用独立更新检测模式
   • 检测到 watchtower 时自动兼容旧模式
   • 仅在真正有变化时发送通知

⏰ <b>启动时间</b>
   <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━

✅ 服务正常运行中"""

        bot.send_message(startup_msg)
    else:
        logger.info("从服务器已启动，等待主服务器协调")

    def signal_handler(signum, frame):
        logger.info("收到退出信号，正在关闭...")
        health.beat('main', status='stopping', details={'signal': signum})
        shutdown_flag.set()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    exit_code = 0

    try:
        health.beat('main', status='running')
        monitor.start()
    except KeyboardInterrupt:
        logger.info("用户中断")
    except Exception as e:
        exit_code = 1
        logger.exception(f"监控异常: {e}")
    finally:
        shutdown_flag.set()
        health.beat('main', status='stopped', details={'exit_code': exit_code})
        logger.info("服务已停止")

    sys.exit(exit_code)

if __name__ == "__main__":
    main()
