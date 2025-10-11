
import logging
import time
from typing import List, Optional, Callable, Dict, Any
from threading import Thread
import docker
from docker.errors import DockerException, NotFound, APIError
from docker_manager import (
    DockerManager, ContainerInfo, NetworkInfo, TunnelContainerConfig,
    EventProcessor
)
from docker_mode import DockerModeInfo
class StandaloneDockerManager(DockerManager):
    def __init__(self, client: docker.DockerClient, mode_info: DockerModeInfo):
        super().__init__(client, mode_info)
        self.logger = logging.getLogger(__name__)
    def get_enabled_containers(self) -> List[ContainerInfo]:
        containers = []
        try:
            for container in self.client.containers.list():
                labels = getattr(container, 'labels', {}) or {}
                if self.is_dockflare_enabled(labels):
                    container_info = ContainerInfo(
                        id=container.id,
                        name=container.name,
                        labels=labels,
                        status=getattr(container, 'status', 'unknown'),
                        image=container.image.tags[0] if container.image.tags else "unknown",
                        network_mode=self._get_network_mode(container)
                    )
                    containers.append(container_info)
        except Exception as e:
            self.logger.error(f"Error getting enabled containers: {e}")
        return containers
    def listen_for_events(self, callback: Callable[[str, ContainerInfo], None]) -> None:
        def event_listener():
            self.logger.info("Starting Docker event listener for standalone mode")
            try:
                for container_info in self.get_enabled_containers():
                    self.logger.info(f"Found existing container to report: {container_info.name}")
                    callback("container_start", container_info)
            except Exception as e:
                self.logger.error(f"Error during initial container scan: {e}")
            event_processor = EventProcessor(self, callback)
            try:
                for event in self.client.events(decode=True):
                    if event.get("Type") == "container" and event.get("Action") in ["start", "stop", "die"]:
                        event_processor.process_container_event(event)
            except Exception as e:
                self.logger.error(f"Docker event listener error: {e}")
        Thread(target=event_listener, daemon=True).start()
    def create_tunnel_container(self, config: TunnelContainerConfig) -> Optional[ContainerInfo]:
        try:
            if not self.ensure_network_exists(config.network_name):
                self.logger.error(f"Failed to ensure network {config.network_name} exists")
                return None
            self._remove_existing_container(config.name)
            self.logger.info(f"Creating tunnel container '{config.name}'")
            container = self.client.containers.run(
                config.image,
                command=config.command,
                detach=True,
                name=config.name,
                network=config.network_name,
                restart_policy=config.restart_policy,
                environment=config.environment,
                labels=config.labels or {}
            )
            time.sleep(2)
            container.reload()
            return ContainerInfo(
                id=container.id,
                name=container.name,
                labels=container.labels or {},
                status=container.status,
                image=config.image,
                network_mode=config.network_name
            )
        except Exception as e:
            self.logger.error(f"Failed to create tunnel container '{config.name}': {e}")
            return None
    def get_container_by_name(self, name: str) -> Optional[ContainerInfo]:
        try:
            container = self.client.containers.get(name)
            return ContainerInfo(
                id=container.id,
                name=container.name,
                labels=container.labels or {},
                status=container.status,
                image=container.image.tags[0] if container.image.tags else "unknown",
                network_mode=self._get_network_mode(container)
            )
        except NotFound:
            return None
        except Exception as e:
            self.logger.error(f"Error getting container '{name}': {e}")
            return None
    def remove_container(self, name: str) -> bool:
        return self._remove_existing_container(name)
    def ensure_network_exists(self, network_name: str) -> bool:
        try:
            network = self.client.networks.get(network_name)
            self.logger.debug(f"Network '{network_name}' already exists")
            return True
        except NotFound:
            try:
                self.logger.info(f"Creating network '{network_name}'")
                self.client.networks.create(
                    name=network_name,
                    driver="bridge",
                    check_duplicate=True
                )
                return True
            except Exception as e:
                self.logger.error(f"Failed to create network '{network_name}': {e}")
                return False
        except Exception as e:
            self.logger.error(f"Error checking network '{network_name}': {e}")
            return False
    def get_network_info(self, network_name: str) -> Optional[NetworkInfo]:
        try:
            network = self.client.networks.get(network_name)
            return NetworkInfo(
                id=network.id,
                name=network.name,
                driver=network.attrs.get('Driver', 'unknown'),
                scope=network.attrs.get('Scope', 'local'),
                attachable=network.attrs.get('Attachable', False)
            )
        except NotFound:
            return None
        except Exception as e:
            self.logger.error(f"Error getting network info for '{network_name}': {e}")
            return None
    def _remove_existing_container(self, name: str) -> bool:
        try:
            container = self.client.containers.get(name)
            self.logger.info(f"Stopping existing container '{container.name}' ({container.short_id})")
            if container.status == 'running':
                container.stop(timeout=10)
            container.remove()
            return True
        except NotFound:
            self.logger.debug(f"No existing container '{name}' to remove")
            return True
        except Exception as e:
            self.logger.error(f"Failed to remove existing container '{name}': {e}")
            return False
    def _get_network_mode(self, container) -> Optional[str]:
        try:
            network_settings = container.attrs.get('NetworkSettings', {})
            networks = network_settings.get('Networks', {})
            if networks:
                return list(networks.keys())[0]
            host_config = container.attrs.get('HostConfig', {})
            return host_config.get('NetworkMode')
        except Exception:
            return None
    def get_container_logs(self, name: str, lines: int = 50) -> str:
        try:
            container = self.client.containers.get(name)
            logs = container.logs(tail=lines, timestamps=True)
            return logs.decode('utf-8', errors='ignore')
        except Exception as e:
            self.logger.error(f"Error getting logs for container '{name}': {e}")
            return f"Error getting logs: {e}"
    def exec_command_in_container(self, name: str, command: List[str]) -> tuple[int, str]:
        try:
            container = self.client.containers.get(name)
            exec_result = container.exec_run(command)
            output = ""
            if hasattr(exec_result, 'output') and exec_result.output:
                output = exec_result.output.decode('utf-8', errors='ignore')
            exit_code = getattr(exec_result, 'exit_code', 0)
            return exit_code, output
        except Exception as e:
            self.logger.error(f"Error executing command in container '{name}': {e}")
            return 1, f"Error: {e}"
    def get_container_stats(self, name: str) -> Dict[str, Any]:
        try:
            container = self.client.containers.get(name)
            stats = container.stats(stream=False)
            return stats
        except Exception as e:
            self.logger.error(f"Error getting stats for container '{name}': {e}")
            return {}
