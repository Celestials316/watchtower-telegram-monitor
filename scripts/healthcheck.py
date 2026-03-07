#!/usr/bin/env python3

import json
import os
import re
import sys
import time
from pathlib import Path

MAX_AGE = int(os.getenv('HEALTHCHECK_MAX_AGE', '120'))
REQUIRED_COMPONENTS = ('main', 'bot_poller', 'heartbeat', 'update_monitor')


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


def resolve_health_file() -> Path:
    explicit_path = os.getenv('HEALTHCHECK_FILE', '').strip()
    if explicit_path:
        return Path(explicit_path)

    server_name = os.getenv('SERVER_NAME', 'default')
    server_key = sanitize_file_component(server_name)
    return Path(f'/data/health_status.{server_key}.json')


HEALTH_FILE = resolve_health_file()


def fail(message: str) -> int:
    print(message)
    return 1


def load_health() -> dict:
    last_error = None
    for _ in range(3):
        try:
            if not HEALTH_FILE.exists():
                raise FileNotFoundError(f'健康状态文件不存在: {HEALTH_FILE}')
            return json.loads(HEALTH_FILE.read_text(encoding='utf-8'))
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f'读取健康状态失败: {last_error}')


def ensure_process_alive(pid: int):
    try:
        os.kill(pid, 0)
    except OSError as exc:
        raise RuntimeError(f'主进程不可用: {exc}') from exc


def main() -> int:
    try:
        data = load_health()
    except Exception as exc:
        return fail(str(exc))

    pid = data.get('pid')
    if not isinstance(pid, int) or pid <= 0:
        return fail('健康状态缺少有效 pid')

    try:
        ensure_process_alive(pid)
    except Exception as exc:
        return fail(str(exc))

    now = time.time()
    updated_at = data.get('updated_at', 0)
    if now - updated_at > MAX_AGE:
        return fail(f'全局心跳超时: {now - updated_at:.0f}s')

    components = data.get('components', {})
    for name in REQUIRED_COMPONENTS:
        component = components.get(name)
        if not component:
            return fail(f'缺少组件心跳: {name}')

        status = component.get('status', 'unknown')
        component_updated_at = component.get('updated_at', 0)

        if status == 'error':
            return fail(f'组件异常: {name}')

        if now - component_updated_at > MAX_AGE:
            return fail(f'组件心跳超时: {name} {now - component_updated_at:.0f}s')

    print('ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
