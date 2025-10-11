# Docker Swarm Deployment Guide

This guide covers deploying DockFlare Agent in Docker Swarm mode for distributed ingress management across multiple nodes.

## Prerequisites

1. **Docker Swarm cluster** with at least one manager and one or more worker nodes
2. **DockFlare Master v3.0+** running with Redis and HTTPS enabled
3. **Overlay network** for tunnel communication
4. **Agent API key** from the DockFlare Master UI

## Quick Start

### 1. Initialize Swarm (if not already done)

```bash
# On manager node
docker swarm init

# On worker nodes (use token from manager)
docker swarm join --token <worker-token> <manager-ip>:2377
```

### 2. Create Required Network

```bash
# Create overlay network for DockFlare tunnels
docker network create \
  --driver overlay \
  --attachable \
  cloudflare-net
```

### 3. Configure Environment

Copy the example environment file:

```bash
cp swarm-env-example .env
```

Edit `.env` with your configuration:

```env
DOCKFLARE_MASTER_URL=https://dockflare.example.com
DOCKFLARE_API_KEY=your_agent_api_key_here
AGENT_DISPLAY_NAME=Production Swarm
```

### 4. Deploy the Stack

Choose one of the deployment options:

#### Option A: Full Stack (Recommended)
Includes Docker socket proxy for enhanced security:

```bash
docker stack deploy -c docker-swarm-stack.yml dockflare
```

#### Option B: Simple Deployment
Direct Docker socket access (less secure):

```bash
docker stack deploy -c docker-swarm-simple.yml dockflare
```

### 5. Verify Deployment

```bash
# Check service status
docker service ls

# Check agent logs
docker service logs dockflare_dockflare-agent

# Check which nodes are running agents
docker service ps dockflare_dockflare-agent
```

## Deployment Options

### Global Mode (Default)

Deploys one agent per worker node:

```yaml
deploy:
  mode: global
  placement:
    constraints:
      - node.role == worker
```

### Replicated Mode

Deploy specific number of agent instances:

```yaml
deploy:
  mode: replicated
  replicas: 3
  placement:
    constraints:
      - node.role == worker
```

### Manager Node Deployment

Deploy agents on manager nodes (not recommended for production):

```yaml
deploy:
  placement:
    constraints:
      - node.role == manager
```

## Configuration Options

### Environment Variables

| Variable | Description | Swarm Specific |
|----------|-------------|---------------|
| `DOCKER_MODE` | Force swarm mode | `swarm` |
| `SWARM_NODE_ROLE` | Required node role | `manager`/`worker`/`any` |
| `SWARM_PLACEMENT_CONSTRAINTS` | Additional constraints | See examples below |
| `TUNNEL_PIN_TO_NODE` | Pin tunnels to agent's node | `true` (recommended) |

### Placement Constraints Examples

```yaml
# Deploy only on nodes with specific labels
environment:
  - SWARM_PLACEMENT_CONSTRAINTS=node.labels.environment==production

# Multiple constraints
environment:
  - SWARM_PLACEMENT_CONSTRAINTS=node.labels.environment==production,node.labels.zone==us-west-1

# Avoid specific nodes
placement:
  constraints:
    - node.labels.dockflare.exclude!=true
```

## Network Configuration

### Overlay Networks

DockFlare Agent in Swarm mode uses overlay networks:

```yaml
networks:
  cloudflare-net:
    driver: overlay
    attachable: true
    external: true
```

### Network Requirements

- **Attachable**: Allows standalone containers to join
- **External**: Network must be created before stack deployment
- **Overlay scope**: Spans across all swarm nodes

## Security Considerations

### Docker Socket Access

#### Option 1: Socket Proxy (Recommended)

Uses `tecnativa/docker-socket-proxy` to limit Docker API access:

```yaml
docker-socket-proxy:
  image: tecnativa/docker-socket-proxy:v0.4.1
  environment:
    - CONTAINERS=1
    - EVENTS=1
    - NETWORKS=1
    - SERVICES=1  # Required for Swarm
    - TASKS=1     # Required for Swarm
    - NODES=1     # Required for Swarm
```

#### Option 2: Direct Socket Access

Mount Docker socket directly (less secure):

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

### Service Permissions

Required Docker API permissions for Swarm mode:
- **Services**: Create/manage tunnel services
- **Tasks**: Monitor service tasks
- **Nodes**: Identify current node
- **Networks**: Manage overlay networks
- **Containers**: Monitor user containers
- **Events**: Listen for lifecycle events

## Tunnel Management

### Service-based Tunnels

In Swarm mode, tunnels are deployed as services rather than containers:

```yaml
# Tunnel service automatically created by agent
tunnel-service:
  image: cloudflare/cloudflared:2025.9.0
  deploy:
    mode: replicated
    replicas: 1
    placement:
      constraints:
        - node.id == <agent-node-id>
```

### Node Pinning

Tunnels are pinned to the agent's node by default:

- **Benefits**: Consistent routing, easier debugging
- **Drawbacks**: Single point of failure per node
- **Configure**: Set `TUNNEL_PIN_TO_NODE=false` to allow migration

### High Availability

For HA tunnels across nodes:

1. Set `TUNNEL_PIN_TO_NODE=false`
2. Use multiple agent replicas
3. Configure load balancing at DNS level
4. Monitor tunnel health across nodes

## Monitoring and Troubleshooting

### Service Status

```bash
# List all services
docker service ls

# Service details
docker service inspect dockflare_dockflare-agent

# Service logs
docker service logs -f dockflare_dockflare-agent

# Task status
docker service ps dockflare_dockflare-agent
```

### Node Information

```bash
# List nodes
docker node ls

# Node details
docker node inspect <node-id>

# Services on specific node
docker node ps <node-id>
```

### Common Issues

#### Agent Not Starting

```bash
# Check service events
docker service ps --no-trunc dockflare_dockflare-agent

# Check constraints
docker service inspect dockflare_dockflare-agent | grep -A5 Placement
```

#### Network Issues

```bash
# Verify overlay network exists
docker network ls | grep overlay

# Check network connectivity
docker run --rm --network cloudflare-net alpine ping -c3 <target>
```

#### Tunnel Services Not Creating

```bash
# Check agent logs for tunnel creation attempts
docker service logs dockflare_dockflare-agent | grep tunnel

# Verify Docker permissions
docker service logs dockflare_docker-socket-proxy
```

## Scaling and Updates

### Scaling Agents

```bash
# Scale to specific number of replicas (if using replicated mode)
docker service scale dockflare_dockflare-agent=5

# Update placement constraints
docker service update \
  --constraint-add node.labels.environment==production \
  dockflare_dockflare-agent
```

### Rolling Updates

```bash
# Update image
docker service update \
  --image alplat/dockflare-agent:latest \
  dockflare_dockflare-agent

# Update environment variables
docker service update \
  --env-add LOG_LEVEL=debug \
  dockflare_dockflare-agent
```

## Migration from Standalone

### From Compose to Swarm

1. **Initialize Swarm** on current host
2. **Create overlay networks** to replace bridge networks
3. **Convert compose files** to stack format
4. **Deploy as stack** using existing configuration
5. **Verify functionality** before adding more nodes

### Zero-downtime Migration

1. **Deploy Swarm agents** alongside existing standalone agents
2. **Verify new agents** are working correctly
3. **Update DNS** to point to Swarm-managed tunnels
4. **Remove standalone agents** once traffic is migrated

## Best Practices

1. **Use global mode** for consistent agent distribution
2. **Pin tunnels to nodes** for predictable routing
3. **Monitor agent health** across all nodes
4. **Use placement constraints** for resource isolation
5. **Implement proper logging** and monitoring
6. **Regular backup** of agent data volumes
7. **Test failover scenarios** periodically

## Next Steps

- Configure monitoring and alerting for Swarm services
- Set up centralized logging for distributed agents
- Implement automated backups for agent state
- Plan for disaster recovery scenarios