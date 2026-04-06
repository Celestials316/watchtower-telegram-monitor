import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "monitor.py"


def load_monitor_module(env=None):
    patched_env = {
        "BOT_TOKEN": "token",
        "CHAT_ID": "1",
        "SERVER_NAME": "test-server",
        "PRIMARY_SERVER": "false",
        "ENABLE_BOT_POLLING": "true",
    }
    if env:
        patched_env.update(env)

    spec = importlib.util.spec_from_file_location(
        f"watchtower_monitor_{uuid.uuid4().hex}",
        MODULE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    with mock.patch("pathlib.Path.mkdir", autospec=True):
        with mock.patch.dict(os.environ, patched_env, clear=False):
            spec.loader.exec_module(module)
    return module


class MonitorLogicTests(unittest.TestCase):
    def test_compose_metadata_builds_compose_command(self):
        module = load_monitor_module()
        metadata = module.DockerManager.get_compose_metadata(
            {
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": "demo",
                        "com.docker.compose.service": "web",
                        "com.docker.compose.project.working_dir": "/srv/demo",
                        "com.docker.compose.project.config_files": "docker-compose.yml,compose.override.yml",
                    }
                }
            }
        )

        self.assertIsNotNone(metadata)
        self.assertEqual(
            metadata["config_files"],
            ["/srv/demo/docker-compose.yml", "/srv/demo/compose.override.yml"],
        )
        self.assertEqual(
            module.DockerManager.build_compose_command(metadata, ["pull", "web"]),
            [
                "docker",
                "compose",
                "--project-name",
                "demo",
                "--project-directory",
                "/srv/demo",
                "-f",
                "/srv/demo/docker-compose.yml",
                "-f",
                "/srv/demo/compose.override.yml",
                "pull",
                "web",
            ],
        )

    def test_remote_queue_reclaims_stale_processing_job_and_marks_failure(self):
        module = load_monitor_module()
        with tempfile.TemporaryDirectory() as tempdir:
            queue_path = Path(tempdir) / "queue.json"
            queue = module.RemoteCommandQueue(queue_path)
            job_id = queue.enqueue("srv-a", "confirm_update", {"container": "demo"})

            claimed = queue.claim("srv-a")
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["id"], job_id)
            self.assertIsNone(queue.claim("srv-a"))

            data = json.loads(queue_path.read_text(encoding="utf-8"))
            data["jobs"][0]["claimed_at"] = time.time() - (module.REMOTE_JOB_PROCESSING_TIMEOUT + 5)
            queue_path.write_text(json.dumps(data), encoding="utf-8")

            reclaimed = queue.claim("srv-a")
            self.assertIsNotNone(reclaimed)
            self.assertEqual(reclaimed["id"], job_id)

            queue.fail(job_id, "boom")
            failed_data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(failed_data["jobs"][0]["status"], "failed")
            self.assertEqual(failed_data["jobs"][0]["error"], "boom")

    def test_remote_queue_enqueue_returns_none_when_write_fails(self):
        module = load_monitor_module()
        with tempfile.TemporaryDirectory() as tempdir:
            queue = module.RemoteCommandQueue(Path(tempdir) / "queue.json")
            with mock.patch.object(module, "safe_update_json", return_value=None):
                self.assertIsNone(
                    queue.enqueue("srv-a", "confirm_update", {"container": "demo"})
                )

    def test_remote_queue_claim_does_not_rewrite_empty_queue(self):
        module = load_monitor_module()
        with tempfile.TemporaryDirectory() as tempdir:
            queue_path = Path(tempdir) / "queue.json"
            queue_path.write_text(json.dumps({"jobs": []}), encoding="utf-8")
            queue = module.RemoteCommandQueue(queue_path)

            before = queue_path.stat().st_mtime_ns
            time.sleep(0.01)
            claimed = queue.claim("srv-a")
            after = queue_path.stat().st_mtime_ns

            self.assertIsNone(claimed)
            self.assertEqual(before, after)
            self.assertEqual(
                json.loads(queue_path.read_text(encoding="utf-8")),
                {"jobs": []},
            )

    def test_remote_queue_claim_does_not_rewrite_when_target_server_has_no_job(self):
        module = load_monitor_module()
        with tempfile.TemporaryDirectory() as tempdir:
            queue_path = Path(tempdir) / "queue.json"
            queue_path.write_text(
                json.dumps(
                    {
                        "jobs": [
                            {
                                "id": "job-1",
                                "target_server": "srv-b",
                                "action": "confirm_update",
                                "payload": {"container": "demo"},
                                "status": "pending",
                                "created_at": time.time(),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            queue = module.RemoteCommandQueue(queue_path)

            before = queue_path.stat().st_mtime_ns
            original = queue_path.read_text(encoding="utf-8")
            time.sleep(0.01)
            claimed = queue.claim("srv-a")
            after = queue_path.stat().st_mtime_ns

            self.assertIsNone(claimed)
            self.assertEqual(before, after)
            self.assertEqual(queue_path.read_text(encoding="utf-8"), original)

    def test_parse_remote_servers_config_preserves_ssh_and_queue_modes(self):
        remote_servers = json.dumps(
            [
                {
                    "name": "srv-ssh",
                    "transport": "ssh",
                    "host": "100.64.0.10",
                    "user": "root",
                    "monitor_container": "watchtower-notifier-154",
                },
                {
                    "name": "srv-queue",
                    "transport": "queue",
                },
            ]
        )
        module = load_monitor_module({"REMOTE_SERVERS_JSON": remote_servers})

        self.assertEqual(module.REMOTE_SERVER_CONFIGS["srv-ssh"]["transport"], "ssh")
        self.assertEqual(module.REMOTE_SERVER_CONFIGS["srv-ssh"]["host"], "100.64.0.10")
        self.assertEqual(
            module.REMOTE_SERVER_CONFIGS["srv-ssh"]["monitor_container"],
            "watchtower-notifier-154",
        )
        self.assertEqual(module.REMOTE_SERVER_CONFIGS["srv-queue"]["transport"], "queue")

    def test_remote_server_controller_fetches_inventory_via_ssh(self):
        remote_servers = json.dumps(
            [
                {
                    "name": "srv-ssh",
                    "transport": "ssh",
                    "host": "100.64.0.10",
                    "user": "root",
                }
            ]
        )
        module = load_monitor_module({"REMOTE_SERVERS_JSON": remote_servers})
        docker = mock.Mock()
        config = mock.Mock()
        registry = mock.Mock()
        registry.get_active_servers.return_value = []
        registry.registry_file = Path("/tmp/server_registry.json")
        controller = module.RemoteServerController("local", docker, config, registry)

        with mock.patch.object(
            controller,
            "_run_remote_rpc",
            return_value={
                "ok": True,
                "server_name": "srv-ssh",
                "mode": "independent",
                "collected_at": 123.0,
                "total_containers": 1,
                "monitored_containers": ["demo"],
                "excluded_containers": [],
                "containers": {"demo": {"running": True}},
            },
        ) as rpc_mock:
            inventory = controller.get_inventory("srv-ssh", force_refresh=True)

        rpc_mock.assert_called_once_with("srv-ssh", "inventory", timeout=60)
        self.assertEqual(inventory["transport"], "ssh")
        self.assertEqual(inventory["monitored_containers"], ["demo"])

    def test_enqueue_remote_action_uses_ssh_when_configured(self):
        remote_servers = json.dumps(
            [
                {
                    "name": "srv-ssh",
                    "transport": "ssh",
                    "host": "100.64.0.10",
                    "user": "root",
                }
            ]
        )
        module = load_monitor_module({"REMOTE_SERVERS_JSON": remote_servers})
        bot = mock.Mock()
        bot.server_name = "local"
        docker = mock.Mock()
        config = mock.Mock()
        registry = mock.Mock()
        registry.get_active_servers.return_value = []
        registry.registry_file = Path("/tmp/server_registry.json")
        handler = module.CommandHandler(bot, docker, config, registry)

        with mock.patch.object(handler, "_run_async") as async_mock:
            handler._enqueue_remote_action(
                "confirm_update",
                "srv-ssh",
                "demo",
                "1",
                "2",
            )

        bot.edit_message.assert_called()
        async_mock.assert_called_once_with(
            handler._execute_remote_action_via_ssh,
            "confirm_update",
            "1",
            "2",
            "srv-ssh",
            "demo",
        )

    def test_notify_only_mode_cleans_up_pulled_image(self):
        module = load_monitor_module()

        bot = mock.Mock()
        bot.server_name = "srv-a"
        docker = mock.Mock()
        docker.get_container_info.return_value = {
            "image": "demo:latest",
            "image_id": "sha256:old",
            "running": True,
            "health": "healthy",
        }
        docker._format_version_info.return_value = "latest (old)"
        docker.pull_image.return_value = {
            "success": True,
            "image_id": "sha256:new",
        }
        config = mock.Mock()
        config.is_monitored.return_value = True
        health = mock.Mock()

        monitor = module.WatchtowerMonitor(bot, docker, config, health)
        monitor.state_store = mock.Mock()
        monitor.state_store.get_container_state.return_value = {}

        with mock.patch.object(module, "AUTO_UPDATE", False):
            with mock.patch.object(module.DockerManager, "cleanup_image_if_unused") as cleanup_mock:
                with mock.patch.object(monitor, "_send_update_available_notification"):
                    monitor._check_container_update("demo")

        cleanup_mock.assert_called_once_with("sha256:new", keep_image_ids={"sha256:old"})

    def test_compose_update_path_uses_docker_compose(self):
        module = load_monitor_module()
        compose_metadata = {
            "project": "demo",
            "service": "web",
            "working_dir": "/srv/demo",
            "config_files": [],
            "oneoff": False,
        }
        container_config = {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "demo",
                    "com.docker.compose.service": "web",
                    "com.docker.compose.project.working_dir": "/srv/demo",
                }
            }
        }
        old_info = {
            "image": "demo:latest",
            "image_id": "sha256:old",
        }

        calls = []

        def fake_run(command, timeout=30):
            calls.append(command)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(module.DockerManager, "validate_compose_metadata", return_value=None):
            with mock.patch.object(module.DockerManager, "_run", side_effect=fake_run):
                with mock.patch.object(module.DockerManager, "get_image_id", return_value="sha256:new"):
                    with mock.patch.object(module.DockerManager, "wait_container_ready", return_value=True):
                        with mock.patch.object(module.DockerManager, "get_container_info", return_value={
                            "image": "demo:latest",
                            "image_id": "sha256:new",
                        }):
                            with mock.patch.object(module.DockerManager, "_format_version_info", side_effect=["latest (old)", "latest (new)"]):
                                result = module.DockerManager._update_compose_container(
                                    "web-1",
                                    container_config,
                                    old_info,
                                    compose_metadata,
                                )

        self.assertTrue(result["success"])
        self.assertTrue(any(command[:2] == ["docker", "compose"] and "pull" in command for command in calls))
        self.assertTrue(any(command[:2] == ["docker", "compose"] and "up" in command for command in calls))
        self.assertFalse(any(command[:2] == ["docker", "run"] for command in calls))

    def test_missing_compose_file_falls_back_to_direct_container_recreate(self):
        module = load_monitor_module()
        container_config = {
            "Config": {
                "Image": "demo:latest",
                "Env": ["A=1"],
                "Labels": {
                    "com.docker.compose.project": "demo",
                    "com.docker.compose.service": "web",
                    "com.docker.compose.project.working_dir": "/opt/demo",
                    "com.docker.compose.project.config_files": "/opt/demo/docker-compose.yml",
                },
            },
            "HostConfig": {
                "NetworkMode": "bridge",
                "RestartPolicy": {},
                "PortBindings": {},
            },
            "Mounts": [],
        }
        old_info = {
            "image": "demo:latest",
            "image_id": "sha256:old",
            "running": True,
            "health": None,
        }

        calls = []

        def fake_run(command, timeout=30):
            calls.append(command)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(module.DockerManager, "get_container_info", side_effect=[old_info, {
            "image": "demo:latest",
            "image_id": "sha256:new",
            "running": True,
            "health": None,
        }]):
            with mock.patch.object(module.DockerManager, "get_container_inspect", return_value=container_config):
                with mock.patch.object(
                    module.DockerManager,
                    "validate_compose_metadata",
                    return_value="Compose 配置文件不存在: /opt/demo/docker-compose.yml",
                ):
                    with mock.patch.object(module.DockerManager, "_run", side_effect=fake_run):
                        with mock.patch.object(module.DockerManager, "pull_image", return_value={
                            "success": True,
                            "image_id": "sha256:new",
                        }):
                            with mock.patch.object(module.DockerManager, "wait_container_ready", return_value=True):
                                with mock.patch.object(
                                    module.DockerManager,
                                    "_format_version_info",
                                    side_effect=["latest (old)", "latest (new)"],
                                ):
                                    result = module.DockerManager._update_container_internal("web", None)

        self.assertTrue(result["success"])
        self.assertTrue(any(command[:2] == ["docker", "stop"] for command in calls))
        self.assertTrue(any(command[:2] == ["docker", "rm"] for command in calls))
        self.assertTrue(any(command[:2] == ["docker", "run"] for command in calls))
        self.assertFalse(any(command[:2] == ["docker", "compose"] for command in calls))

    def test_run_rpc_inventory_emits_json_payload(self):
        module = load_monitor_module()
        inventory_payload = {
            "ok": True,
            "server_name": "test-server",
            "transport": "local",
            "mode": "independent",
            "collected_at": 123.0,
            "total_containers": 1,
            "monitored_containers": ["demo"],
            "excluded_containers": [],
            "static_monitored_containers": [],
            "containers": {"demo": {"running": True}},
        }

        with mock.patch.object(module, "build_inventory_payload", return_value=inventory_payload):
            with mock.patch.object(module, "resolve_update_mode", return_value="independent"):
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    exit_code = module.run_rpc(["inventory"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue().strip())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["server_name"], "test-server")


if __name__ == "__main__":
    unittest.main()
