
import os
import logging
from typing import Tuple, Optional, Dict, Any
from abc import ABC, abstractmethod
from dataclasses import dataclass
import docker
from docker import DockerClient
from docker.errors import APIError, DockerException
@dataclass
class DockerModeInfo:
    mode: str
    node_id: Optional[str] = None
    node_role: Optional[str] = None
    swarm_info: Optional[Dict[str, Any]] = None
    manager_address: Optional[str] = None
class DockerModeDetector:
    def __init__(self, client: DockerClient):
        self.client = client
        self.logger = logging.getLogger(__name__)
    def detect_mode(self, forced_mode: Optional[str] = None) -> DockerModeInfo:
        if forced_mode:
            if forced_mode.lower() in ['standalone', 'swarm']:
                self.logger.info(f"Docker mode forced to: {forced_mode}")
                if forced_mode.lower() == 'swarm':
                    return self._detect_swarm_forced()
                else:
                    return DockerModeInfo(mode='standalone')
            else:
                self.logger.warning(f"Invalid forced mode '{forced_mode}', auto-detecting")
        return self._auto_detect_mode()
    def _auto_detect_mode(self) -> DockerModeInfo:
        try:
            swarm_info = self.client.swarm.attrs
            if swarm_info and swarm_info.get('ID'):
                node_id = swarm_info.get('NodeID')
                if not node_id:
                    system_info = self.client.info()
                    node_id = system_info.get('Swarm', {}).get('NodeID')
                if node_id:
                    node_info = self._get_node_info(node_id)
                    self.logger.info(f"Detected Docker Swarm mode - Node: {node_id}")
                    return DockerModeInfo(
                        mode='swarm',
                        node_id=node_id,
                        node_role=node_info.get('role'),
                        swarm_info=swarm_info,
                        manager_address=node_info.get('manager_address')
                    )
        except (APIError, DockerException, AttributeError) as e:
            self.logger.debug(f"Swarm detection failed: {e}")
        self.logger.info("Detected standalone Docker mode")
        return DockerModeInfo(mode='standalone')
    def _detect_swarm_forced(self) -> DockerModeInfo:
        try:
            swarm_info = self.client.swarm.attrs
            system_info = self.client.info()
            node_id = system_info.get('Swarm', {}).get('NodeID')
            if not node_id:
                raise APIError("No Swarm node ID found - not in a swarm?")
            node_info = self._get_node_info(node_id)
            return DockerModeInfo(
                mode='swarm',
                node_id=node_id,
                node_role=node_info.get('role'),
                swarm_info=swarm_info,
                manager_address=node_info.get('manager_address')
            )
        except Exception as e:
            self.logger.error(f"Forced swarm mode failed: {e}")
            return DockerModeInfo(mode='standalone')
    def _get_node_info(self, node_id: str) -> Dict[str, Any]:
        try:
            nodes = self.client.nodes.list()
            for node in nodes:
                if node.id == node_id:
                    spec = node.attrs.get('Spec', {})
                    status = node.attrs.get('Status', {})
                    manager_status = node.attrs.get('ManagerStatus', {})
                    return {
                        'role': spec.get('Role', 'unknown'),
                        'availability': spec.get('Availability', 'unknown'),
                        'state': status.get('State', 'unknown'),
                        'manager_address': manager_status.get('Addr'),
                        'leader': manager_status.get('Leader', False)
                    }
        except Exception as e:
            self.logger.warning(f"Could not get node info for {node_id}: {e}")
        return {}
def get_docker_mode_info() -> DockerModeInfo:
    forced_mode = os.getenv('DOCKER_MODE', '').strip().lower()
    if forced_mode and forced_mode not in ['auto', '']:
        forced_mode = forced_mode
    else:
        forced_mode = None
    client = docker.from_env()
    detector = DockerModeDetector(client)
    return detector.detect_mode(forced_mode)
def validate_swarm_requirements(mode_info: DockerModeInfo) -> bool:
    if mode_info.mode != 'swarm':
        return True
    logger = logging.getLogger(__name__)
    required_role = os.getenv('SWARM_NODE_ROLE', '').strip().lower()
    if required_role and required_role != 'any':
        current_role = mode_info.node_role
        if current_role != required_role:
            logger.error(f"Agent requires node role '{required_role}' but current role is '{current_role}'")
            return False
    if not mode_info.node_id:
        logger.error("No node ID available in swarm mode")
        return False
    logger.info(f"Swarm requirements validated - Node: {mode_info.node_id}, Role: {mode_info.node_role}")
    return True
