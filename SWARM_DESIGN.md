# DockFlare Agent - Unified Docker Mode Design

## Overview

This document outlines the design for a unified DockFlare Agent that automatically detects and adapts to both standalone Docker and Docker Swarm environments.

## Architecture

### Core Components

1. **DockerModeDetector**: Detects current Docker environment
2. **DockerManager** (Abstract): Base class for Docker operations
3. **StandaloneDockerManager**: Handles standalone Docker operations
4. **SwarmDockerManager**: Handles Docker Swarm operations
5. **UnifiedAgent**: Main agent orchestrator

### Mode Detection Strategy

```python
def detect_docker_mode():
    """
    Detection logic:
    1. Check if Docker daemon is in Swarm mode
    2. Verify current node information
    3. Fall back to standalone mode
    """
    try:
        client = docker.from_env()
        swarm_info = client.swarm.attrs
        if swarm_info and swarm_info.get('NodeID'):
            return 'swarm', swarm_info
    except Exception:
        pass
    return 'standalone', None
```

### Docker Manager Interface

```python
class DockerManager(ABC):
    @abstractmethod
    def get_enabled_containers(self) -> List[ContainerInfo]

    @abstractmethod
    def listen_for_events(self, callback) -> None

    @abstractmethod
    def create_tunnel_container(self, name, token, network) -> Container

    @abstractmethod
    def get_container_by_name(self, name) -> Container

    @abstractmethod
    def remove_container(self, container) -> None
```

## Mode-Specific Implementations

### Standalone Mode (Current Behavior)
- Direct container management via Docker API
- Container event listening
- Bridge network usage
- Simple container lifecycle

### Swarm Mode (New Implementation)
- Service-based container management
- Task and service event monitoring
- Overlay network support
- Node-aware operations
- Service constraint handling

## Configuration

### Environment Variables

| Variable | Description | Standalone | Swarm |
|----------|-------------|------------|-------|
| `DOCKER_MODE` | Force specific mode | `standalone` | `swarm` |
| `SWARM_NODE_ROLE` | Required node role | N/A | `manager`/`worker`/`any` |
| `SWARM_PLACEMENT_CONSTRAINTS` | Service constraints | N/A | `node.role==worker` |
| `CLOUDFLARED_NETWORK_NAME` | Network name | Bridge network | Overlay network |

### Deployment Examples

#### Standalone (docker-compose.yml)
```yaml
version: '3.8'
services:
  dockflare-agent:
    image: alplat/dockflare-agent:latest
    environment:
      - DOCKER_MODE=auto  # or 'standalone'
```

#### Swarm (docker-stack.yml)
```yaml
version: '3.8'
services:
  dockflare-agent:
    image: alplat/dockflare-agent:latest
    deploy:
      mode: global
      placement:
        constraints:
          - node.role == worker
    environment:
      - DOCKER_MODE=auto  # or 'swarm'
```

## Implementation Plan

### Phase 1: Core Abstraction
1. Create DockerManager base class
2. Implement mode detection
3. Create factory pattern for manager selection

### Phase 2: Standalone Refactor
1. Extract current logic into StandaloneDockerManager
2. Maintain backward compatibility
3. Test existing functionality

### Phase 3: Swarm Implementation
1. Implement SwarmDockerManager
2. Add service discovery logic
3. Handle overlay networks
4. Implement service-based tunnel management

### Phase 4: Integration
1. Update main.py to use unified system
2. Add configuration validation
3. Create deployment examples
4. Update documentation

## Key Considerations

### Network Management
- **Standalone**: Create/manage bridge networks as needed
- **Swarm**: Use existing overlay networks, handle attachable networks

### Container Discovery
- **Standalone**: Direct container enumeration
- **Swarm**: Filter tasks by current node, handle service replicas

### Event Handling
- **Standalone**: Container start/stop/die events
- **Swarm**: Service update events, task state changes, node events

### Tunnel Management
- **Standalone**: Direct container creation/management
- **Swarm**: Service creation with placement constraints

### High Availability
- **Standalone**: Single point of failure per host
- **Swarm**: Service can reschedule on node failure

### Security Considerations
- Both modes use same socket proxy pattern
- Swarm mode requires additional service permissions
- Node constraints prevent unauthorized deployments

## Migration Path

1. **Immediate**: Current deployments continue working unchanged
2. **Testing**: New unified agent can be tested in both modes
3. **Gradual**: Users can migrate to Swarm deployment when ready
4. **Future**: Single image supports both deployment patterns

## Benefits

- **Operational Simplicity**: One image, one configuration approach
- **Flexibility**: Easy migration between deployment types
- **Feature Parity**: Same capabilities in both modes
- **Maintenance**: Single codebase to maintain
- **User Experience**: Consistent behavior regardless of deployment