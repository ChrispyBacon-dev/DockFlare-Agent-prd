
import os
import logging
import time
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable, Iterator
from dataclasses import dataclass
from threading import Thread
import docker
from docker import DockerClient
from docker.errors import DockerException, NotFound, APIError
from docker_mode import DockerModeInfo
@dataclass
class ContainerInfo:
    id: str
    name: str
    labels: Dict[str, str]
    status: str
    image: str
    network_mode: Optional[str] = None
    node_id: Optional[str] = None
    service_id: Optional[str] = None
    task_id: Optional[str] = None
@dataclass
class NetworkInfo:
    id: str
    name: str
    driver: str
    scope: str
    attachable: bool = False
    ingress: bool = False
@dataclass
class TunnelContainerConfig:
    name: str
    image: str
    token: str
    network_name: str
    command: List[str]
    environment: Dict[str, str]
    restart_policy: Dict[str, Any]
    labels: Optional[Dict[str, str]] = None
    constraints: Optional[List[str]] = None
class DockerManager(ABC):
    def __init__(self, client: DockerClient, mode_info: DockerModeInfo):
        self.client = client
        self.mode_info = mode_info
        self.logger = logging.getLogger(self.__class__.__name__)
    @abstractmethod
    def get_enabled_containers(self) -> List[ContainerInfo]:
        pass
    @abstractmethod
    def listen_for_events(self, callback: Callable[[str, ContainerInfo], None]) -> None:
        pass
    @abstractmethod
    def create_tunnel_container(self, config: TunnelContainerConfig) -> Optional[ContainerInfo]:
        pass
    @abstractmethod
    def get_container_by_name(self, name: str) -> Optional[ContainerInfo]:
        pass
    @abstractmethod
    def remove_container(self, name: str) -> bool:
        pass
    @abstractmethod
    def ensure_network_exists(self, network_name: str) -> bool:
        pass
    @abstractmethod
    def get_network_info(self, network_name: str) -> Optional[NetworkInfo]:
        pass
    def is_dockflare_enabled(self, labels: Dict[str, str]) -> bool:
        if not labels:
            return False
        return (labels.get("dockflare.enable") == "true" or
                labels.get("cloudflare.tunnel.enable") == "true")
class DockerManagerFactory:
    @staticmethod
    def create_manager(client: DockerClient, mode_info: DockerModeInfo) -> 'DockerManager':
        if mode_info.mode == 'swarm':
            from docker_swarm_manager import SwarmDockerManager
            return SwarmDockerManager(client, mode_info)
        else:
            from docker_standalone_manager import StandaloneDockerManager
            return StandaloneDockerManager(client, mode_info)
def create_tunnel_container_config(
    name: str,
    token: str,
    image: str = None,
    network_name: str = None,
    constraints: List[str] = None
) -> TunnelContainerConfig:
    if image is None:
        image = os.getenv("CLOUDFLARED_IMAGE", "cloudflare/cloudflared:2025.9.0")
    if network_name is None:
        network_name = os.getenv("CLOUDFLARED_NETWORK_NAME", "cloudflare-net")
    return TunnelContainerConfig(
        name=name,
        image=image,
        token=token,
        network_name=network_name,
        command=["tunnel", "--no-autoupdate", "run"],
        environment={"TUNNEL_TOKEN": token},
        restart_policy={"Name": "unless-stopped"},
        labels={
            "dockflare.managed": "true",
            "dockflare.type": "tunnel"
        },
        constraints=constraints or []
    )
def parse_event_type(action: str) -> str:
    if action in ["start", "create"]:
        return "container_start"
    elif action in ["stop", "die", "kill"]:
        return "container_stop"
    elif action in ["update"]:
        return "container_update"
    else:
        return f"container_{action}"
class EventProcessor:
    def __init__(self, manager: DockerManager, callback: Callable[[str, ContainerInfo], None]):
        self.manager = manager
        self.callback = callback
        self.logger = logging.getLogger(__name__)
    def process_container_event(self, event: Dict[str, Any]) -> None:
        try:
            action = event.get('Action', '')
            container_id = event.get('id', '')
            if not container_id:
                return
            try:
                container = self.manager.client.containers.get(container_id)
                container_info = ContainerInfo(
                    id=container.id,
                    name=container.name,
                    labels=container.labels or {},
                    status=container.status,
                    image=container.image.tags[0] if container.image.tags else "unknown"
                )
                if self.manager.is_dockflare_enabled(container_info.labels):
                    event_type = parse_event_type(action)
                    self.logger.info(f"Processing event '{event_type}' for container {container_info.name}")
                    self.callback(event_type, container_info)
            except NotFound:
                actor = event.get('Actor', {})
                attributes = actor.get('Attributes', {})
                if self.manager.is_dockflare_enabled(attributes):
                    container_info = ContainerInfo(
                        id=container_id,
                        name=attributes.get('name', 'unknown'),
                        labels=attributes,
                        status='removed',
                        image=attributes.get('image', 'unknown')
                    )
                    event_type = parse_event_type(action)
                    self.logger.info(f"Processing event '{event_type}' for removed container {container_info.name}")
                    self.callback(event_type, container_info)
        except Exception as e:
            self.logger.error(f"Error processing container event: {e}")
    def process_service_event(self, event: Dict[str, Any]) -> None:
        try:
            action = event.get('Action', '')
            service_id = event.get('id', '')
            if not service_id:
                return
            try:
                service = self.manager.client.services.get(service_id)
                spec = service.attrs.get('Spec', {})
                labels = spec.get('Labels', {}) or {}
                if self.manager.is_dockflare_enabled(labels):
                    tasks = service.tasks()
                    for task in tasks:
                        if task.get('NodeID') == self.manager.mode_info.node_id:
                            container_info = ContainerInfo(
                                id=task.get('Status', {}).get('ContainerStatus', {}).get('ContainerID', service_id),
                                name=spec.get('Name', 'unknown'),
                                labels=labels,
                                status=task.get('Status', {}).get('State', 'unknown'),
                                image=spec.get('TaskTemplate', {}).get('ContainerSpec', {}).get('Image', 'unknown'),
                                service_id=service_id,
                                task_id=task.get('ID'),
                                node_id=task.get('NodeID')
                            )
                            event_type = parse_event_type(action)
                            self.logger.info(f"Processing service event '{event_type}' for {container_info.name}")
                            self.callback(event_type, container_info)
            except NotFound:
                self.logger.warning(f"Service {service_id} not found for event processing")
        except Exception as e:
            self.logger.error(f"Error processing service event: {e}")
