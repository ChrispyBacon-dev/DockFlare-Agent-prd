# Technical Walkthrough: Agent Lifecycle & API Interactions

This document provides a detailed technical walkthrough of how DockFlare Agent interacts with the Master, handles Docker Swarm events, and manages tunnel lifecycles.

## Complete Agent Lifecycle

### Phase 1: Agent Startup & Registration

**1.1 Agent Initialization**
```python
# Agent startup sequence
agent = UnifiedDockFlareAgent()
success = agent.initialize()

# What happens internally:
docker_client = docker.from_env()
mode_info = get_docker_mode_info()
# mode_info = DockerModeInfo(
#   mode='swarm',
#   node_id='ktnuv2s9qi5vdx6n8mrl0ug8z',
#   node_role='worker'
# )

docker_manager = DockerManagerFactory.create_manager(docker_client, mode_info)
# Returns: SwarmDockerManager instance
```

**1.2 Master Registration API Call**
```http
POST /api/v2/agents/register
Host: dockflare.example.com
Authorization: Bearer agent_api_key_12345
Content-Type: application/json

{
  "display_name": "Production Swarm Worker 01",
  "version": "2.0.0",
  "mode": "swarm",
  "node_info": {
    "node_id": "ktnuv2s9qi5vdx6n8mrl0ug8z",
    "node_role": "worker"
  }
}
```

**1.3 Master Response**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "agent_id": "agent-ktnuv2s9qi5vdx6n8mrl0ug8z",
  "status": "registered",
  "assigned_region": "us-east-1",
  "capabilities": [
    "tunnel_management",
    "service_discovery",
    "swarm_orchestration"
  ]
}
```

**1.4 Agent State After Registration**
```python
# Agent internal state
self.agent_id = "agent-ktnuv2s9qi5vdx6n8mrl0ug8z"
self.mode_info = DockerModeInfo(mode='swarm', node_id='ktnuv2s9qi5vdx6n8mrl0ug8z')
self.docker_manager = SwarmDockerManager(client, mode_info)
```

### Phase 2: Service Discovery & Event Reporting

**2.1 Docker Service Deployment**
```bash
# DevOps team deploys a new service
docker service create \
  --name web-app \
  --label dockflare.enable=true \
  --label dockflare.domain=app.example.com \
  --label dockflare.port=8080 \
  --network cloudflare-net \
  --replicas 3 \
  nginx:latest
```

**2.2 Agent Detects Service Start**
```python
# SwarmDockerManager.listen_for_events() picks up service creation
def _process_task_event(self, event):
    # Event data from Docker API:
    event = {
        "Type": "task",
        "Action": "start",
        "Actor": {
            "ID": "task_abc123",
            "Attributes": {
                "com.docker.swarm.service.name": "web-app",
                "com.docker.swarm.node.id": "ktnuv2s9qi5vdx6n8mrl0ug8z"
            }
        }
    }

    # Only process if it's on our node
    if attributes["com.docker.swarm.node.id"] == self.node_id:
        # Get service details and check if DockFlare enabled
        service = self.client.services.get("web-app")
        labels = service.attrs["Spec"]["Labels"]

        if self.is_dockflare_enabled(labels):
            container_info = ContainerInfo(
                id="container_xyz789",
                name="web-app",
                labels=labels,
                status="running",
                service_id="service_def456",
                task_id="task_abc123",
                node_id="ktnuv2s9qi5vdx6n8mrl0ug8z"
            )

            self.callback("container_start", container_info)
```

**2.3 Event Reporting to Master**
```http
POST /api/v2/agents/agent-ktnuv2s9qi5vdx6n8mrl0ug8z/events
Host: dockflare.example.com
Authorization: Bearer agent_api_key_12345
Content-Type: application/json

{
  "type": "container_start",
  "timestamp": "2024-01-15T10:30:45.123Z",
  "mode": "swarm",
  "container": {
    "id": "container_xyz789",
    "name": "web-app",
    "labels": {
      "dockflare.enable": "true",
      "dockflare.domain": "app.example.com",
      "dockflare.port": "8080"
    },
    "status": "running",
    "image": "nginx:latest",
    "service_id": "service_def456",
    "task_id": "task_abc123",
    "node_id": "ktnuv2s9qi5vdx6n8mrl0ug8z"
  }
}
```

**2.4 Master Processing & Response**
```javascript
// Master-side event processing
async function processAgentEvent(agentId, event) {
  const container = event.container;
  const domain = container.labels['dockflare.domain'];

  if (domain && !tunnelExists(domain)) {
    // Create new tunnel
    const tunnel = await createCloudflaredTunnel({
      name: `tunnel-${domain.replace(/\./g, '-')}`,
      domain: domain
    });

    // Store tunnel assignment
    await assignTunnelToAgent(agentId, tunnel.id, {
      token: tunnel.token,
      name: tunnel.name,
      domain: domain,
      target: `http://${container.name}:${container.labels['dockflare.port']}`
    });

    // Queue command for agent
    await queueCommand(agentId, {
      action: "start_tunnel",
      tunnel_id: tunnel.id,
      tunnel_name: tunnel.name,
      token: tunnel.token,
      rules: {
        [domain]: {
          service: container.name,
          port: parseInt(container.labels['dockflare.port'])
        }
      }
    });
  }
}
```

### Phase 3: Command Polling & Tunnel Management

**3.1 Agent Polls for Commands**
```http
GET /api/v2/agents/agent-ktnuv2s9qi5vdx6n8mrl0ug8z/commands
Host: dockflare.example.com
Authorization: Bearer agent_api_key_12345
```

**3.2 Master Returns Tunnel Assignment**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "commands": [
    {
      "id": "cmd_789abc",
      "action": "start_tunnel",
      "tunnel_id": "tunnel_def456",
      "tunnel_name": "tunnel-app-example-com",
      "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
      "rules": {
        "app.example.com": {
          "service": "web-app",
          "port": 8080
        }
      },
      "timestamp": "2024-01-15T10:31:00.000Z"
    }
  ]
}
```

**3.3 Agent Processes Tunnel Command**
```python
def process_command(self, cmd):
    if cmd["action"] == "start_tunnel":
        tunnel_token = cmd["token"]
        tunnel_name = cmd["tunnel_name"]
        tunnel_id = cmd["tunnel_id"]

        # Update internal state
        self.current_tunnel_token = tunnel_token
        self.current_tunnel_id = tunnel_id
        self.current_tunnel_name = tunnel_name
        self.desired_tunnel_state = "running"
        self.save_tunnel_state()

        # Create tunnel service configuration
        config = create_tunnel_container_config(
            name='dockflare-agent-tunnel',
            token=tunnel_token,
            image=self.get_cloudflared_image(),
            network_name='cloudflare-net',
            constraints=[f"node.id=={self.mode_info.node_id}"]
        )

        # Deploy tunnel service
        tunnel_info = self.docker_manager.create_tunnel_container(config)
```

**3.4 Tunnel Service Creation (Docker Swarm)**
```python
# SwarmDockerManager.create_tunnel_container()
def create_tunnel_container(self, config):
    # Remove existing service if it exists
    self._remove_existing_service(config.name)

    # Create Docker service
    service = self.client.services.create(
        image=config.image,
        command=config.command,
        name=config.name,
        env=config.environment,
        labels=config.labels,
        networks=[config.network_name],
        mode=ServiceMode('replicated', replicas=1),
        restart_policy=RestartPolicy(condition='any'),
        placement=PlacementSpec(
            constraints=[f"node.id=={self.node_id}"]
        )
    )

    # Wait for task to start
    time.sleep(5)

    # Get task information
    tasks = service.tasks()
    for task in tasks:
        if task.get('NodeID') == self.node_id:
            return ContainerInfo(
                id=task['Status']['ContainerStatus']['ContainerID'],
                name=config.name,
                status=task['Status']['State'],
                service_id=service.id,
                task_id=task['ID'],
                node_id=task['NodeID']
            )
```

**3.5 Tunnel Status Reporting**
```http
POST /api/v2/agents/agent-ktnuv2s9qi5vdx6n8mrl0ug8z/events
Host: dockflare.example.com
Authorization: Bearer agent_api_key_12345
Content-Type: application/json

{
  "type": "tunnel_status",
  "timestamp": "2024-01-15T10:31:15.456Z",
  "container": {
    "name": "tunnel-app-example-com",
    "status": "running",
    "version": "cloudflared version 2025.9.0",
    "mode": "swarm",
    "service_id": "service_tunnel_123",
    "task_id": "task_tunnel_456",
    "node_id": "ktnuv2s9qi5vdx6n8mrl0ug8z"
  }
}
```

### Phase 4: Periodic Status Reporting

**4.1 Heartbeat & Status Collection**
```python
def periodic_status_reporter(self):
    while True:
        # Send heartbeat
        self.report_event_to_master("heartbeat")

        # Collect all DockFlare-enabled containers
        containers = []
        for container_info in self.docker_manager.get_enabled_containers():
            containers.append({
                "id": container_info.id,
                "name": container_info.name,
                "labels": container_info.labels,
                "status": container_info.status,
                "service_id": container_info.service_id,
                "node_id": container_info.node_id
            })

        # Send status report
        self.report_event_to_master("status_report", {
            "containers": containers
        })

        time.sleep(30)  # Report every 30 seconds
```

**4.2 Status Report API Call**
```http
POST /api/v2/agents/agent-ktnuv2s9qi5vdx6n8mrl0ug8z/events
Host: dockflare.example.com
Authorization: Bearer agent_api_key_12345
Content-Type: application/json

{
  "type": "status_report",
  "timestamp": "2024-01-15T10:32:00.000Z",
  "container": {
    "containers": [
      {
        "id": "container_xyz789",
        "name": "web-app",
        "status": "running",
        "service_id": "service_def456",
        "node_id": "ktnuv2s9qi5vdx6n8mrl0ug8z",
        "labels": {
          "dockflare.enable": "true",
          "dockflare.domain": "app.example.com"
        }
      },
      {
        "id": "container_tunnel_abc",
        "name": "dockflare-agent-tunnel",
        "status": "running",
        "service_id": "service_tunnel_123",
        "node_id": "ktnuv2s9qi5vdx6n8mrl0ug8z",
        "labels": {
          "dockflare.managed": "true",
          "dockflare.type": "tunnel"
        }
      }
    ]
  }
}
```

### Phase 5: Service Scaling & Dynamic Updates

**5.1 Service Scaling Event**
```bash
# DevOps scales the service
docker service scale web-app=6
```

**5.2 Agent Detects Scaling**
```python
# New tasks starting triggers events
for event in self.client.events(decode=True):
    if event["Type"] == "task" and event["Action"] == "start":
        # Multiple new tasks detected on different nodes
        # Agent only reports tasks running on its node
        if event["Actor"]["Attributes"]["com.docker.swarm.node.id"] == self.node_id:
            self.process_task_event(event)
```

**5.3 Load Balancer Update**
```http
POST /api/v2/agents/agent-ktnuv2s9qi5vdx6n8mrl0ug8z/commands
Host: dockflare.example.com
Authorization: Bearer agent_api_key_12345
Content-Type: application/json

{
  "commands": [
    {
      "action": "update_tunnel_config",
      "tunnel_id": "tunnel_def456",
      "rules": {
        "app.example.com": {
          "service": "web-app",
          "port": 8080,
          "replicas": 6,
          "load_balancer": "round_robin"
        }
      }
    }
  ]
}
```

**5.4 Tunnel Configuration Update**
```python
def process_command(self, cmd):
    if cmd["action"] == "update_tunnel_config":
        rules = cmd["rules"]
        ingress_rules = cloudflare_api.generate_ingress_rules(rules)

        # Update tunnel configuration through master
        success = cloudflare_api.update_tunnel_config(
            self.master_url,
            self.api_key,
            self.current_tunnel_id,
            ingress_rules
        )
```

### Phase 6: High Availability & Failover

**6.1 Node Failure Scenario**
```
# Node swarm-worker-02 fails
[swarm-manager] Node swarm-worker-02 is now down
[swarm-manager] Rescheduling tasks from swarm-worker-02
[swarm-manager] Service web-app: moving 2 replicas to healthy nodes
```

**6.2 Service Migration Detection**
```python
# Agent on swarm-worker-01 detects new tasks
[agent-worker-01] Detected event 'task_start' for web-app
[agent-worker-01] New task scheduled on this node: task_xyz789
[agent-worker-01] Reporting container_start for migrated service
```

**6.3 Automatic Tunnel Adjustment**
```http
# Agent reports new container instance
POST /api/v2/agents/agent-worker-01/events
{
  "type": "container_start",
  "container": {
    "name": "web-app",
    "service_id": "service_def456",
    "task_id": "task_xyz789_migrated",
    "node_id": "worker-01-node-id",
    "migration_source": "swarm-worker-02"
  }
}
```

## State Management & Persistence

**Agent State Files:**
```bash
# /app/data/agent_id.txt
agent-ktnuv2s9qi5vdx6n8mrl0ug8z

# /app/data/tunnel_state.json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "id": "tunnel_def456",
  "name": "tunnel-app-example-com",
  "desired_state": "running"
}
```

**Master State Tracking:**
```javascript
// Master database entries
agents: {
  "agent-ktnuv2s9qi5vdx6n8mrl0ug8z": {
    display_name: "Production Swarm Worker 01",
    mode: "swarm",
    node_id: "ktnuv2s9qi5vdx6n8mrl0ug8z",
    node_role: "worker",
    last_heartbeat: "2024-01-15T10:32:00.000Z",
    status: "online",
    assigned_tunnels: ["tunnel_def456"],
    managed_services: ["web-app", "api-backend"]
  }
},

tunnels: {
  "tunnel_def456": {
    name: "tunnel-app-example-com",
    domain: "app.example.com",
    assigned_agent: "agent-ktnuv2s9qi5vdx6n8mrl0ug8z",
    target_service: "web-app",
    status: "active",
    created_at: "2024-01-15T10:31:00.000Z"
  }
}
```

This technical walkthrough shows the complete flow from service deployment to active tunnel, demonstrating how the unified DockFlare Agent seamlessly handles Docker Swarm orchestration while maintaining compatibility with existing workflows.