
import os
import logging
import time
from typing import List, Optional, Callable, Dict, Any
from threading import Thread
import docker
from docker.errors import DockerException, NotFound, APIError
from docker.types import ServiceMode, RestartPolicy, PlacementSpec, TaskTemplate
from docker_manager import (
    DockerManager, ContainerInfo, NetworkInfo, TunnelContainerConfig,
    EventProcessor
)
from docker_mode import DockerModeInfo
class SwarmDockerManager(DockerManager):
    def __init__(self, client: docker.DockerClient, mode_info: DockerModeInfo):
        super().__init__(client, mode_info)
        self.logger = logging.getLogger(__name__)
        self.node_id = mode_info.node_id
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
            self.logger.error(f"Error getting regular containers: {e}")
        try:
            services = self.client.services.list()
            for service in services:
                spec = service.attrs.get('Spec', {})
                labels = spec.get('Labels', {}) or {}
                if self.is_dockflare_enabled(labels):
                    tasks = service.tasks(filters={'node': self.node_id})
                    for task in tasks:
                        task_state = task.get('Status', {}).get('State')
                        if task_state in ['running', 'starting']:
                            container_status = task.get('Status', {}).get('ContainerStatus', {})
                            container_info = ContainerInfo(
                                id=container_status.get('ContainerID', task.get('ID')),
                                name=spec.get('Name', 'unknown'),
                                labels=labels,
                                status=task_state,
                                image=spec.get('TaskTemplate', {}).get('ContainerSpec', {}).get('Image', 'unknown'),
                                service_id=service.id,
                                task_id=task.get('ID'),
                                node_id=task.get('NodeID')
                            )
                            containers.append(container_info)
        except Exception as e:
            self.logger.error(f"Error getting service tasks: {e}")
        return containers
    def listen_for_events(self, callback: Callable[[str, ContainerInfo], None]) -> None:
        def event_listener():
            self.logger.info("Starting Docker event listener for Swarm mode")
            try:
                for container_info in self.get_enabled_containers():
                    self.logger.info(f"Found existing container/task to report: {container_info.name}")
                    callback("container_start", container_info)
            except Exception as e:
                self.logger.error(f"Error during initial container scan: {e}")
            event_processor = EventProcessor(self, callback)
            try:
                for event in self.client.events(decode=True):
                    event_type = event.get("Type")
                    action = event.get("Action")
                    if event_type == "container" and action in ["start", "stop", "die"]:
                        event_processor.process_container_event(event)
                    elif event_type == "service" and action in ["create", "update", "remove"]:
                        event_processor.process_service_event(event)
                    elif event_type == "task" and action in ["start", "stop", "complete"]:
                        self._process_task_event(event, callback)
            except Exception as e:
                self.logger.error(f"Docker Swarm event listener error: {e}")
        Thread(target=event_listener, daemon=True).start()
    def create_tunnel_container(self, config: TunnelContainerConfig) -> Optional[ContainerInfo]:
        try:
            if not self.ensure_network_exists(config.network_name):
                self.logger.error(f"Failed to ensure network {config.network_name} exists")
                return None
            self._remove_existing_service(config.name)
            self.logger.info(f"Creating tunnel service '{config.name}'")
            constraints = list(config.constraints or [])
            if self.node_id and os.getenv('TUNNEL_PIN_TO_NODE', 'true').lower() == 'true':
                constraints.append(f"node.id=={self.node_id}")
            service = self.client.services.create(
                image=config.image,
                command=config.command,
                name=config.name,
                env=config.environment,
                labels=config.labels or {},
                networks=[config.network_name],
                mode=ServiceMode('replicated', replicas=1),
                restart_policy=RestartPolicy(
                    condition=config.restart_policy.get('Name', 'any')
                ),
                placement=PlacementSpec(constraints=constraints) if constraints else None
            )
            time.sleep(5)
            tasks = service.tasks()
            for task in tasks:
                if task.get('NodeID') == self.node_id:
                    task_state = task.get('Status', {}).get('State')
                    if task_state in ['running', 'starting']:
                        container_status = task.get('Status', {}).get('ContainerStatus', {})
                        return ContainerInfo(
                            id=container_status.get('ContainerID', task.get('ID')),
                            name=config.name,
                            labels=config.labels or {},
                            status=task_state,
                            image=config.image,
                            service_id=service.id,
                            task_id=task.get('ID'),
                            node_id=task.get('NodeID')
                        )
            self.logger.warning(f"Tunnel service '{config.name}' created but no task found on this node")
            return None
        except Exception as e:
            self.logger.error(f"Failed to create tunnel service '{config.name}': {e}")
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
            pass
        except Exception as e:
            self.logger.error(f"Error getting container '{name}': {e}")
        try:
            service = self.client.services.get(name)
            spec = service.attrs.get('Spec', {})
            tasks = service.tasks(filters={'node': self.node_id})
            for task in tasks:
                task_state = task.get('Status', {}).get('State')
                if task_state in ['running', 'starting']:
                    container_status = task.get('Status', {}).get('ContainerStatus', {})
                    return ContainerInfo(
                        id=container_status.get('ContainerID', task.get('ID')),
                        name=name,
                        labels=spec.get('Labels', {}) or {},
                        status=task_state,
                        image=spec.get('TaskTemplate', {}).get('ContainerSpec', {}).get('Image', 'unknown'),
                        service_id=service.id,
                        task_id=task.get('ID'),
                        node_id=task.get('NodeID')
                    )
        except NotFound:
            return None
        except Exception as e:
            self.logger.error(f"Error getting service '{name}': {e}")
        return None
    def remove_container(self, name: str) -> bool:
        if self._remove_existing_service(name):
            return True
        return self._remove_existing_container(name)
    def ensure_network_exists(self, network_name: str) -> bool:
        try:
            network = self.client.networks.get(network_name)
            self.logger.debug(f"Network '{network_name}' already exists")
            return True
        except NotFound:
            try:
                self.logger.info(f"Creating overlay network '{network_name}'")
                self.client.networks.create(
                    name=network_name,
                    driver="overlay",
                    attachable=True,
                    check_duplicate=True
                )
                return True
            except Exception as e:
                self.logger.error(f"Failed to create overlay network '{network_name}': {e}")
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
                scope=network.attrs.get('Scope', 'swarm'),
                attachable=network.attrs.get('Attachable', False),
                ingress=network.attrs.get('Ingress', False)
            )
        except NotFound:
            return None
        except Exception as e:
            self.logger.error(f"Error getting network info for '{network_name}': {e}")
            return None
    def _remove_existing_service(self, name: str) -> bool:
        try:
            service = self.client.services.get(name)
            self.logger.info(f"Removing existing service '{service.name}' ({service.short_id})")
            service.remove()
            return True
        except NotFound:
            self.logger.debug(f"No existing service '{name}' to remove")
            return False
        except Exception as e:
            self.logger.error(f"Failed to remove existing service '{name}': {e}")
            return False
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
            return False
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
    def _process_task_event(self, event: Dict[str, Any], callback: Callable[[str, ContainerInfo], None]):
        try:
            action = event.get('Action', '')
            actor = event.get('Actor', {})
            attributes = actor.get('Attributes', {})
            service_name = attributes.get('com.docker.swarm.service.name')
            task_id = actor.get('ID')
            node_id = attributes.get('com.docker.swarm.node.id')
            if node_id != self.node_id:
                return
            if service_name and task_id:
                try:
                    service = self.client.services.get(service_name)
                    spec = service.attrs.get('Spec', {})
                    labels = spec.get('Labels', {}) or {}
                    if self.is_dockflare_enabled(labels):
                        tasks = service.tasks(filters={'id': task_id})
                        if tasks:
                            task = tasks[0]
                            task_state = task.get('Status', {}).get('State')
                            container_status = task.get('Status', {}).get('ContainerStatus', {})
                            container_info = ContainerInfo(
                                id=container_status.get('ContainerID', task_id),
                                name=service_name,
                                labels=labels,
                                status=task_state,
                                image=spec.get('TaskTemplate', {}).get('ContainerSpec', {}).get('Image', 'unknown'),
                                service_id=service.id,
                                task_id=task_id,
                                node_id=node_id
                            )
                            if action == 'start':
                                callback("container_start", container_info)
                            elif action in ['stop', 'complete']:
                                callback("container_stop", container_info)
                except NotFound:
                    self.logger.warning(f"Service '{service_name}' not found for task event")
        except Exception as e:
            self.logger.error(f"Error processing task event: {e}")
    def get_service_logs(self, service_name: str, lines: int = 50) -> str:
        try:
            service = self.client.services.get(service_name)
            logs = service.logs(tail=lines, timestamps=True)
            return logs.decode('utf-8', errors='ignore')
        except Exception as e:
            self.logger.error(f"Error getting logs for service '{service_name}': {e}")
            return f"Error getting logs: {e}"
    def scale_service(self, service_name: str, replicas: int) -> bool:
        try:
            service = self.client.services.get(service_name)
            service.scale(replicas)
            self.logger.info(f"Scaled service '{service_name}' to {replicas} replicas")
            return True
        except Exception as e:
            self.logger.error(f"Error scaling service '{service_name}': {e}")
            return False
    def update_service_image(self, service_name: str, new_image: str) -> bool:
        try:
            service = self.client.services.get(service_name)
            task_template = service.attrs['Spec']['TaskTemplate']
            task_template['ContainerSpec']['Image'] = new_image
            service.update(task_template=TaskTemplate(**task_template))
            self.logger.info(f"Updated service '{service_name}' to use image '{new_image}'")
            return True
        except Exception as e:
            self.logger.error(f"Error updating service '{service_name}': {e}")
            return False
