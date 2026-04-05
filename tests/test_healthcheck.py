import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "healthcheck.py"


def load_healthcheck_module(env=None):
    patched_env = {
        "SERVER_NAME": "test-server",
        "PRIMARY_SERVER": "false",
        "ENABLE_BOT_POLLING": "true",
    }
    if env:
        patched_env.update(env)
    spec = importlib.util.spec_from_file_location("watchtower_healthcheck", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    with mock.patch.dict(os.environ, patched_env, clear=False):
        spec.loader.exec_module(module)
    return module


class HealthcheckTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.health_file = Path(self.tempdir.name) / "health.json"

    def write_health(self, payload):
        self.health_file.write_text(json.dumps(payload), encoding="utf-8")

    def test_main_component_is_lifecycle_only(self):
        module = load_healthcheck_module()
        now = time.time()
        self.write_health(
            {
                "pid": os.getpid(),
                "updated_at": now,
                "components": {
                    "main": {"status": "running", "updated_at": now - (module.MAX_AGE + 300)},
                    "heartbeat": {"status": "ok", "updated_at": now},
                    "update_monitor": {"status": "ok", "updated_at": now},
                    "remote_worker": {"status": "ok", "updated_at": now},
                },
            }
        )

        with mock.patch.object(module, "HEALTH_FILE", self.health_file):
            self.assertEqual(module.main(), 0)

    def test_periodic_component_timeout_still_fails(self):
        module = load_healthcheck_module()
        now = time.time()
        self.write_health(
            {
                "pid": os.getpid(),
                "updated_at": now,
                "components": {
                    "main": {"status": "running", "updated_at": now},
                    "heartbeat": {"status": "ok", "updated_at": now - (module.MAX_AGE + 300)},
                    "update_monitor": {"status": "ok", "updated_at": now},
                    "remote_worker": {"status": "ok", "updated_at": now},
                },
            }
        )

        with mock.patch.object(module, "HEALTH_FILE", self.health_file):
            self.assertEqual(module.main(), 1)

    def test_primary_server_can_disable_bot_poller_requirement(self):
        module = load_healthcheck_module({
            "PRIMARY_SERVER": "true",
            "ENABLE_BOT_POLLING": "false",
        })
        now = time.time()
        self.write_health(
            {
                "pid": os.getpid(),
                "updated_at": now,
                "components": {
                    "main": {"status": "running", "updated_at": now},
                    "heartbeat": {"status": "ok", "updated_at": now},
                    "update_monitor": {"status": "ok", "updated_at": now},
                    "remote_worker": {"status": "ok", "updated_at": now},
                },
            }
        )

        with mock.patch.object(module, "HEALTH_FILE", self.health_file):
            self.assertEqual(module.main(), 0)


if __name__ == "__main__":
    unittest.main()
