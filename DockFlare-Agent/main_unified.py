
import os
import re
import time
import json
import logging
import tempfile
from threading import Thread
from typing import Optional, Dict, Any
from dotenv import load_dotenv
import docker
import requests
from docker_mode import get_docker_mode_info, validate_swarm_requirements
from docker_manager import (
    DockerManagerFactory, create_tunnel_container_config,
    ContainerInfo
)
import cloudflare_api
DEFAULT_CLOUDFLARED_IMAGE = "cloudflare/cloudflared:2025.9.0"
_SHA256_HEX_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
class UnifiedDockFlareAgent:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.docker_client = None
        self.docker_manager = None
        self.mode_info = None
        self.agent_id = None
        self.master_url = None
        self.api_key = None
        self.current_tunnel_info = None
        self.current_tunnel_token = None
        self.current_tunnel_id = None
        self.current_tunnel_name = None
        self.current_tunnel_version = None
        self.desired_tunnel_state = "unknown"
        self.agent_id_file = "/app/data/agent_id.txt"
        self.tunnel_state_file = "/app/data/tunnel_state.json"
    def initialize(self) -> bool:
        try:
            load_dotenv()
            self.master_url = os.getenv("DOCKFLARE_MASTER_URL")
            self.api_key = os.getenv("DOCKFLARE_API_KEY")
            if not self.master_url or not self.api_key:
                self.logger.error("DOCKFLARE_MASTER_URL and DOCKFLARE_API_KEY must be set")
                return False
            self.docker_client = docker.from_env()
            self.mode_info = get_docker_mode_info()
            self.logger.info(f"Detected Docker mode: {self.mode_info.mode}")
            if self.mode_info.mode == 'swarm':
                self.logger.info(f"Swarm Node ID: {self.mode_info.node_id}")
                self.logger.info(f"Swarm Node Role: {self.mode_info.node_role}")
            if not validate_swarm_requirements(self.mode_info):
                return False
            self.docker_manager = DockerManagerFactory.create_manager(
                self.docker_client,
                self.mode_info
            )
            self.load_agent_id()
            self.load_tunnel_state()
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize agent: {e}")
            return False
    def load_agent_id(self):
        if os.path.exists(self.agent_id_file):
            try:
                with open(self.agent_id_file, 'r') as f:
                    agent_id = f.read().strip()
                    if agent_id:
                        self.agent_id = agent_id
                        self.logger.info(f"Loaded existing Agent ID: {self.agent_id}")
            except IOError as e:
                self.logger.error(f"Could not read agent ID file: {e}")
    def save_agent_id(self, agent_id: str):
        try:
            self._write_secure_file(self.agent_id_file, lambda fh: fh.write(agent_id))
            self.logger.info(f"Saved Agent ID to {self.agent_id_file}")
        except IOError as e:
            self.logger.error(f"Could not save agent ID file: {e}")
    def load_tunnel_state(self):
        if not os.path.exists(self.tunnel_state_file):
            return
        try:
            with open(self.tunnel_state_file, 'r') as f:
                data = json.load(f)
            self.current_tunnel_token = data.get("token")
            self.current_tunnel_id = data.get("id")
            self.current_tunnel_name = data.get("name")
            self.desired_tunnel_state = data.get("desired_state", "unknown")
            self.logger.info("Loaded tunnel state from disk")
        except Exception as e:
            self.logger.error(f"Failed to load tunnel state file: {e}")
    def save_tunnel_state(self):
        data = {
            "token": self.current_tunnel_token,
            "id": self.current_tunnel_id,
            "name": self.current_tunnel_name,
            "desired_state": self.desired_tunnel_state
        }
        try:
            self._write_secure_file(self.tunnel_state_file, lambda fh: json.dump(data, fh))
        except Exception as e:
            self.logger.error(f"Failed to persist tunnel state: {e}")
    def _write_secure_file(self, path: str, writer):
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile('w', dir=directory, delete=False) as tmp_file:
                writer(tmp_file)
                tmp_file.flush()
                try:
                    os.fsync(tmp_file.fileno())
                except (AttributeError, OSError):
                    pass
                temp_path = tmp_file.name
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            os.replace(temp_path, path)
        except Exception:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise
    def register_with_master(self) -> bool:
        endpoint = f"{self.master_url}/api/v2/agents/register"
        while True:
            self.logger.info(f"Attempting to register with master at {endpoint}")
            try:
                headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
                custom_display_name = os.getenv("AGENT_DISPLAY_NAME", "").strip()
                default_display_name = f"agent-{self.agent_id[:8]}" if self.agent_id else "dockflare-agent"
                payload = {
                    "display_name": custom_display_name or default_display_name,
                    "version": "2.0.0",
                    "mode": self.mode_info.mode,
                    "node_info": {
                        "node_id": self.mode_info.node_id,
                        "node_role": self.mode_info.node_role
                    } if self.mode_info.mode == 'swarm' else None
                }
                if self.agent_id:
                    payload["agent_id"] = self.agent_id
                response = requests.post(endpoint, json=payload, headers=headers, timeout=15)
                response.raise_for_status()
                data = response.json()
                new_agent_id = data.get("agent_id")
                if not new_agent_id:
                    self.logger.error("Master did not provide an agent_id. Retrying in 60s...")
                    time.sleep(60)
                    continue
                if self.agent_id and self.agent_id != new_agent_id:
                    self.logger.warning(f"Master assigned a new Agent ID ({new_agent_id}). Overwriting old one ({self.agent_id}).")
                self.agent_id = new_agent_id
                self.save_agent_id(self.agent_id)
                self.logger.info(f"Successfully registered with master. Agent ID: {self.agent_id}")
                return True
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Error registering with master: {e}. Retrying in 60s...")
                time.sleep(60)
    def report_event_to_master(self, event_type: str, container_data: Optional[Dict[str, Any]] = None):
        if not self.agent_id:
            self.logger.debug("report_event_to_master called but agent_id is missing; skipping.")
            return
        try:
            from datetime import datetime, timezone
            payload = {
                "type": event_type,
                "timestamp": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
                "mode": self.mode_info.mode
            }
            if container_data:
                payload["container"] = container_data
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            endpoint = f"{self.master_url}/api/v2/agents/{self.agent_id}/events"
            self.logger.debug(f"Reporting to master endpoint={endpoint} payload_type={event_type}")
            response = requests.post(endpoint, json=payload, headers=headers, timeout=15)
            response.raise_for_status()
            self.logger.info(f"Successfully reported event to master: {event_type} (status={response.status_code})")
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error reporting event to master: {e}")
    def handle_container_event(self, event_type: str, container_info: ContainerInfo):
        self.logger.info(f"Handling event '{event_type}' for container {container_info.name}")
        container_data = {
            "id": container_info.id,
            "name": container_info.name,
            "labels": container_info.labels,
            "status": container_info.status,
            "image": container_info.image
        }
        if container_info.service_id:
            container_data["service_id"] = container_info.service_id
        if container_info.task_id:
            container_data["task_id"] = container_info.task_id
        if container_info.node_id:
            container_data["node_id"] = container_info.node_id
        self.report_event_to_master(event_type, container_data)
    def ensure_tunnel_running(self):
        if (self.desired_tunnel_state != "running" or
            not self.current_tunnel_token or
            not self.current_tunnel_name):
            return
        existing = self.docker_manager.get_container_by_name('dockflare-agent-tunnel')
        if existing and existing.status in ['running', 'starting']:
            self.current_tunnel_info = existing
            if not self.current_tunnel_version:
                self.current_tunnel_version = self.fetch_cloudflared_version(existing)
            return
        self.logger.info("Starting tunnel container/service...")
        config = create_tunnel_container_config(
            name='dockflare-agent-tunnel',
            token=self.current_tunnel_token,
            image=self.get_cloudflared_image(),
            network_name=self.get_network_name(),
            constraints=self.get_placement_constraints()
        )
        self.current_tunnel_info = self.docker_manager.create_tunnel_container(config)
        if self.current_tunnel_info:
            time.sleep(2)
            self.current_tunnel_version = self.fetch_cloudflared_version(self.current_tunnel_info)
            self.report_event_to_master("tunnel_status", {
                "name": self.current_tunnel_name,
                "status": "running",
                "version": self.current_tunnel_version,
                "mode": self.mode_info.mode
            })
    def fetch_cloudflared_version(self, container_info: ContainerInfo) -> Optional[str]:
        try:
            if self.mode_info.mode == 'standalone' and container_info.id:
                container = self.docker_client.containers.get(container_info.id)
                exec_result = container.exec_run("cloudflared --version")
                output = getattr(exec_result, 'output', b'') or b''
                version_text = output.decode('utf-8', errors='ignore').strip()
                if version_text:
                    first_line = version_text.splitlines()[0]
                    self.logger.info(f"Detected cloudflared version: {first_line}")
                    return first_line
            elif self.mode_info.mode == 'swarm':
                pass
        except Exception as e:
            self.logger.error(f"Failed to fetch cloudflared version: {e}")
        return None
    def get_cloudflared_image(self) -> str:
        image = os.getenv("CLOUDFLARED_IMAGE", DEFAULT_CLOUDFLARED_IMAGE)
        return self._normalize_cloudflared_image(image, DEFAULT_CLOUDFLARED_IMAGE)
    def get_network_name(self) -> str:
        return os.getenv("CLOUDFLARED_NETWORK_NAME", "cloudflare-net")
    def get_placement_constraints(self) -> list:
        if self.mode_info.mode != 'swarm':
            return []
        constraints = []
        user_constraints = os.getenv("SWARM_PLACEMENT_CONSTRAINTS", "").strip()
        if user_constraints:
            constraints.extend([c.strip() for c in user_constraints.split(",")])
        return constraints
    def _normalize_cloudflared_image(self, raw_value: str, default_image: str) -> str:
        if not raw_value:
            return default_image
        candidate = raw_value.strip()
        if not candidate:
            self.logger.warning(f"CLOUDFLARED_IMAGE is blank after trimming; falling back to default {default_image}")
            return default_image
        candidate = candidate.split()[0]
        if "#" in candidate:
            candidate = candidate.split("#", 1)[0].strip()
            if not candidate:
                self.logger.warning(f"CLOUDFLARED_IMAGE only contained a comment; falling back to default {default_image}")
                return default_image
        if "@sha256:" in candidate:
            repository, digest = candidate.split("@sha256:", 1)
            if not repository:
                self.logger.error(f"CLOUDFLARED_IMAGE is missing the repository before @sha256; falling back to default {default_image}")
                return default_image
            if not _SHA256_HEX_RE.fullmatch(digest):
                self.logger.error(f"CLOUDFLARED_IMAGE digest is not a valid 64 char hex string; falling back to default {default_image}")
                return default_image
            return f"{repository}@sha256:{digest.lower()}"
        return candidate
    def manage_tunnels(self):
        self.logger.info("Tunnel management thread started")
        while True:
            if not self.agent_id:
                time.sleep(10)
                continue
            try:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                endpoint = f"{self.master_url}/api/v2/agents/{self.agent_id}/commands"
                response = requests.get(endpoint, headers=headers, timeout=15)
                response.raise_for_status()
                commands = response.json().get("commands", [])
                for cmd in commands:
                    self.process_command(cmd)
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Could not poll commands from master: {e}")
            except Exception as e:
                self.logger.error(f"Error in tunnel management: {e}")
            time.sleep(30)
    def process_command(self, cmd: Dict[str, Any]):
        action = cmd.get("action")
        if action == "start_tunnel":
            tunnel_token = cmd.get("token")
            tunnel_name = cmd.get("tunnel_name")
            tunnel_id = cmd.get("tunnel_id")
            if tunnel_token and tunnel_name and tunnel_id:
                self.logger.info(f"Received command to start tunnel '{tunnel_name}'")
                self.current_tunnel_token = tunnel_token
                self.current_tunnel_id = tunnel_id
                self.current_tunnel_name = tunnel_name
                self.desired_tunnel_state = "running"
                self.save_tunnel_state()
                self.ensure_tunnel_running()
        elif action == "stop_tunnel":
            self.logger.info("Received command to stop tunnel")
            self.desired_tunnel_state = "stopped"
            self.save_tunnel_state()
            if self.docker_manager.remove_container('dockflare-agent-tunnel'):
                self.report_event_to_master("tunnel_status", {
                    "name": self.current_tunnel_name,
                    "status": "stopped"
                })
            self.current_tunnel_token = None
            self.current_tunnel_id = None
            self.current_tunnel_name = None
            self.current_tunnel_info = None
            self.save_tunnel_state()
        elif action == "update_tunnel_config":
            if not self.current_tunnel_id:
                self.logger.error("Cannot update tunnel config: no tunnel ID available")
                return
            rules = cmd.get("rules", {})
            ingress_rules = cloudflare_api.generate_ingress_rules(rules)
            success = cloudflare_api.update_tunnel_config(
                self.master_url,
                self.api_key,
                self.current_tunnel_id,
                ingress_rules
            )
            if success:
                self.logger.info("Tunnel configuration updated successfully")
            else:
                self.logger.error("Failed to update tunnel configuration")
    def tunnel_health_monitor(self):
        self.logger.info("Tunnel health monitor thread started")
        while True:
            try:
                self.ensure_tunnel_running()
            except Exception as e:
                self.logger.error(f"Tunnel health monitor encountered an error: {e}")
            time.sleep(30)
    def periodic_status_reporter(self):
        self.logger.info("Status reporter thread started")
        report_interval = int(os.getenv("REPORT_INTERVAL_SECONDS", "30"))
        while True:
            if not self.agent_id:
                time.sleep(5)
                continue
            try:
                self.logger.info("Sending heartbeat to master")
                self.report_event_to_master("heartbeat")
                containers = []
                for container_info in self.docker_manager.get_enabled_containers():
                    container_data = {
                        "id": container_info.id,
                        "name": container_info.name,
                        "labels": container_info.labels,
                        "status": container_info.status,
                        "image": container_info.image
                    }
                    if container_info.service_id:
                        container_data["service_id"] = container_info.service_id
                    if container_info.task_id:
                        container_data["task_id"] = container_info.task_id
                    if container_info.node_id:
                        container_data["node_id"] = container_info.node_id
                    containers.append(container_data)
                self.logger.info(f"Sending status_report to master (containers={len(containers)})")
                self.report_event_to_master("status_report", {"containers": containers})
            except Exception as e:
                self.logger.error(f"Periodic reporter error: {e}")
            time.sleep(report_interval)
    def listen_for_events(self):
        self.logger.info("Starting Docker event listener")
        self.docker_manager.listen_for_events(self.handle_container_event)
    def run(self):
        if not self.initialize():
            self.logger.critical("Agent initialization failed")
            return False
        if not self.register_with_master():
            self.logger.critical("Agent registration failed")
            return False
        try:
            self.ensure_tunnel_running()
            Thread(target=self.manage_tunnels, daemon=True).start()
            Thread(target=self.periodic_status_reporter, daemon=True).start()
            Thread(target=self.listen_for_events, daemon=True).start()
            Thread(target=self.tunnel_health_monitor, daemon=True).start()
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.cleanup()
        except Exception as e:
            self.logger.critical(f"Critical error: {e}")
            self.cleanup()
        return True
    def cleanup(self):
        self.logger.info("Shutting down agent")
        if self.current_tunnel_info and self.docker_manager:
            self.logger.info("Stopping tunnel...")
            try:
                self.docker_manager.remove_container('dockflare-agent-tunnel')
            except Exception as e:
                self.logger.error(f"Could not stop tunnel: {e}")
def main():
    log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    logging.basicConfig(level=log_level, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.getLogger().info(f"DockFlare Agent starting - Logging level: {log_level_str}")
    agent = UnifiedDockFlareAgent()
    return agent.run()
if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
