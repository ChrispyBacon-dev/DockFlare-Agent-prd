# DockFlare Agent - Real World Scenarios

This document walks through practical deployment scenarios for DockFlare Agent in Docker Swarm environments, showing the complete lifecycle from initial deployment to production operations.

## Scenario 1: E-Commerce Platform with Multi-Node Swarm

### Business Context
- **Company**: TechMart Online Store
- **Infrastructure**: 5-node Docker Swarm cluster
- **Requirements**: High availability web services with automatic tunnel management
- **Goal**: Zero-downtime deployments with distributed ingress

### Infrastructure Setup

```
┌─────────────────────────────────────────────────────────────────┐
│                    TechMart Swarm Cluster                      │
├─────────────────────────────────────────────────────────────────┤
│ swarm-manager-01 (Manager)    │ DockFlare Master + Redis        │
│ swarm-worker-01 (Worker)      │ DockFlare Agent + Web Services  │
│ swarm-worker-02 (Worker)      │ DockFlare Agent + Web Services  │
│ swarm-worker-03 (Worker)      │ DockFlare Agent + Database      │
│ swarm-worker-04 (Worker)      │ DockFlare Agent + Cache/Queue   │
└─────────────────────────────────────────────────────────────────┘
```

### Step 1: Initial Swarm Setup

**On Manager Node (swarm-manager-01):**
```bash
# Initialize Swarm
docker swarm init --advertise-addr 10.0.1.10

# Create overlay networks
docker network create --driver overlay --attachable cloudflare-net
docker network create --driver overlay --attachable app-network

# Deploy DockFlare Master (separate stack)
docker stack deploy -c dockflare-master-stack.yml master
```

**Worker nodes join automatically or manually:**
```bash
# On each worker node
docker swarm join --token SWMTKN-1-xxx... 10.0.1.10:2377
```

### Step 2: Agent Deployment

**Create environment configuration:**
```bash
# On manager node - create agent configuration
cat > agent-config.env << EOF
DOCKFLARE_MASTER_URL=http://10.0.1.10:8080
DOCKFLARE_API_KEY=swarm-cluster-key-2024
DOCKER_MODE=auto
SWARM_NODE_ROLE=worker
AGENT_DISPLAY_NAME=TechMart Production
CLOUDFLARED_NETWORK_NAME=cloudflare-net
LOG_LEVEL=info
TUNNEL_PIN_TO_NODE=true
EOF
```

**Deploy agents to all worker nodes:**
```bash
# Deploy DockFlare Agent stack
env $(cat agent-config.env) docker stack deploy \
  -c docker-swarm-stack.yml \
  dockflare-agents
```

### Step 3: Agent Enrollment Process

**What happens automatically:**

1. **Agent Startup Sequence:**
   ```
   [swarm-worker-01] DockFlare Agent starting - Logging level: INFO
   [swarm-worker-01] Detected Docker mode: swarm
   [swarm-worker-01] Swarm Node ID: ktnuv2s9qi5vdx6n8mrl0ug8z
   [swarm-worker-01] Swarm Node Role: worker
   [swarm-worker-01] Swarm requirements validated
   [swarm-worker-01] Attempting to register with master at http://10.0.1.10:8080/api/v2/agents/register
   ```

2. **Master Side Registration:**
   ```
   [master] New agent registration request from 10.0.1.20
   [master] Agent payload: {
     "display_name": "TechMart Production",
     "version": "2.0.0",
     "mode": "swarm",
     "node_info": {
       "node_id": "ktnuv2s9qi5vdx6n8mrl0ug8z",
       "node_role": "worker"
     }
   }
   [master] Assigned Agent ID: agent-ktnuv2s9
   ```

3. **Agent Confirmation:**
   ```
   [swarm-worker-01] Successfully registered with master. Agent ID: agent-ktnuv2s9
   [swarm-worker-01] Status reporter thread started
   [swarm-worker-01] Starting Docker event listener for Swarm mode
   ```

### Step 4: Service Deployment & Auto-Discovery

**Deploy web application services:**
```yaml
# techmart-app-stack.yml
version: '3.8'
services:
  web-frontend:
    image: techmart/frontend:v1.2.3
    labels:
      - "dockflare.enable=true"
      - "dockflare.domain=shop.techmart.com"
      - "dockflare.port=3000"
    networks:
      - app-network
      - cloudflare-net
    deploy:
      mode: replicated
      replicas: 3
      placement:
        constraints:
          - node.labels.tier==web

  api-backend:
    image: techmart/api:v2.1.0
    labels:
      - "dockflare.enable=true"
      - "dockflare.domain=api.techmart.com"
      - "dockflare.port=8000"
      - "dockflare.path=/api/*"
    networks:
      - app-network
      - cloudflare-net
    deploy:
      mode: replicated
      replicas: 2
      placement:
        constraints:
          - node.labels.tier==api
```

**Deploy the application:**
```bash
docker stack deploy -c techmart-app-stack.yml techmart
```

### Step 5: Automatic Tunnel Assignment

**What happens behind the scenes:**

1. **Service Discovery:**
   ```
   [agent-ktnuv2s9] Detected event 'container_start' for service techmart_web-frontend
   [agent-ktnuv2s9] Reporting to master: {
     "type": "container_start",
     "container": {
       "name": "techmart_web-frontend",
       "labels": {
         "dockflare.enable": "true",
         "dockflare.domain": "shop.techmart.com"
       },
       "service_id": "xyz789",
       "task_id": "abc123",
       "node_id": "ktnuv2s9qi5vdx6n8mrl0ug8z"
     },
     "mode": "swarm"
   }
   ```

2. **Master Processing:**
   ```
   [master] Processing container event from agent-ktnuv2s9
   [master] New domain requested: shop.techmart.com
   [master] Creating Cloudflare tunnel: tunnel-shop-techmart
   [master] Updating DNS: shop.techmart.com -> tunnel-shop-techmart
   [master] Sending tunnel assignment to agent-ktnuv2s9
   ```

3. **Agent Receives Tunnel Command:**
   ```
   [agent-ktnuv2s9] Received command to start tunnel 'tunnel-shop-techmart'
   [agent-ktnuv2s9] Creating tunnel service 'dockflare-agent-tunnel'
   [agent-ktnuv2s9] Service created with constraints: [node.id==ktnuv2s9qi5vdx6n8mrl0ug8z]
   [agent-ktnuv2s9] Tunnel service started: service-id-456
   [agent-ktnuv2s9] Successfully reported tunnel_status to master: running
   ```

### Step 6: Production Traffic Flow

**End-to-end request flow:**
```
Internet User
    │
    ▼
Cloudflare Edge (shop.techmart.com)
    │
    ▼
Cloudflare Tunnel (running on swarm-worker-01)
    │
    ▼
Docker Swarm Ingress Network (cloudflare-net)
    │
    ▼
Load Balancer (3 replicas of web-frontend)
    │
    ▼
Frontend Container (on any worker node)
    │
    ▼
API Backend (via app-network)
```

### Step 7: Scaling & High Availability

**Scaling the application:**
```bash
# Scale frontend to handle more traffic
docker service scale techmart_web-frontend=6

# Add new worker node
docker swarm join --token SWMTKN-1-xxx... 10.0.1.10:2377
```

**What happens automatically:**
```
[swarm-worker-05] DockFlare Agent starting on new node
[swarm-worker-05] Detected Docker mode: swarm
[swarm-worker-05] Successfully registered with master. Agent ID: agent-abc12345
[agent-abc12345] Found existing containers to report: techmart_web-frontend
[agent-abc12345] Status reporter thread started
```

---

## Scenario 2: Multi-Tenant SaaS Platform

### Business Context
- **Company**: CloudApp SaaS Provider
- **Infrastructure**: 12-node Swarm across 3 data centers
- **Requirements**: Tenant isolation, custom domains, automated provisioning
- **Challenge**: Dynamic tenant onboarding with instant domain activation

### Infrastructure Design

```
Data Center 1 (US-East)          Data Center 2 (US-West)         Data Center 3 (EU)
┌─────────────────────────┐     ┌─────────────────────────┐     ┌─────────────────────────┐
│ swarm-us-east-mgr-01   │────▶│ swarm-us-west-mgr-01   │────▶│ swarm-eu-mgr-01        │
│ swarm-us-east-wkr-01   │     │ swarm-us-west-wkr-01   │     │ swarm-eu-wkr-01        │
│ swarm-us-east-wkr-02   │     │ swarm-us-west-wkr-02   │     │ swarm-eu-wkr-02        │
│ swarm-us-east-wkr-03   │     │ swarm-us-west-wkr-03   │     │ swarm-eu-wkr-03        │
└─────────────────────────┘     └─────────────────────────┘     └─────────────────────────┘
    │ DockFlare Agents          │ DockFlare Agents              │ DockFlare Agents
    │ Regional Master           │ Cluster Agents                │ Cluster Agents
```

### Deployment Configuration

**Regional agent deployment:**
```yaml
# cloudapp-agents-us-east.yml
version: '3.8'
services:
  dockflare-agent:
    image: alplat/dockflare-agent:latest
    environment:
      - DOCKFLARE_MASTER_URL=https://master.cloudapp.internal
      - DOCKFLARE_API_KEY=${REGION_API_KEY}
      - DOCKER_MODE=swarm
      - AGENT_DISPLAY_NAME=CloudApp US-East ${NODE_NAME}
      - SWARM_NODE_ROLE=worker
      - SWARM_PLACEMENT_CONSTRAINTS=node.labels.datacenter==us-east,node.labels.tier==app
      - LOG_LEVEL=info
      - TUNNEL_PIN_TO_NODE=true
    deploy:
      mode: global
      placement:
        constraints:
          - node.role == worker
          - node.labels.datacenter == us-east
    networks:
      - cloudflare-net
    volumes:
      - agent_data:/app/data
```

### Tenant Onboarding Workflow

**New tenant signs up (api.cloudapp.com):**
```json
POST /api/v1/tenants
{
  "tenant_id": "acme-corp",
  "domain": "acme.cloudapp.com",
  "region": "us-east",
  "plan": "enterprise"
}
```

**Automatic provisioning sequence:**

1. **Tenant Service Creation:**
   ```bash
   # CloudApp control plane creates tenant stack
   docker stack deploy -c tenant-acme-corp.yml tenant-acme-corp
   ```

2. **Service Definition:**
   ```yaml
   # tenant-acme-corp.yml
   version: '3.8'
   services:
     acme-app:
       image: cloudapp/tenant-app:latest
       labels:
         - "dockflare.enable=true"
         - "dockflare.domain=acme.cloudapp.com"
         - "dockflare.port=8080"
         - "dockflare.tenant=acme-corp"
       environment:
         - TENANT_ID=acme-corp
         - DATABASE_URL=postgres://tenant-acme@db-cluster/acme_db
       networks:
         - cloudflare-net
         - tenant-network
       deploy:
         mode: replicated
         replicas: 3
         placement:
           constraints:
             - node.labels.datacenter==us-east
             - node.labels.tier==app
   ```

3. **Agent Detection & Reporting:**
   ```
   [agent-us-east-wkr-01] Detected event 'container_start' for service tenant-acme-corp_acme-app
   [agent-us-east-wkr-01] Container labels: {
     "dockflare.enable": "true",
     "dockflare.domain": "acme.cloudapp.com",
     "dockflare.tenant": "acme-corp"
   }
   [agent-us-east-wkr-01] Reporting container_start to master
   ```

4. **Master Orchestration:**
   ```
   [master] New tenant service detected: acme.cloudapp.com
   [master] Tenant: acme-corp, Region: us-east
   [master] Creating dedicated tunnel: tunnel-acme-cloudapp
   [master] Configuring DNS: acme.cloudapp.com -> tunnel-acme-cloudapp
   [master] Sending tunnel assignment to agent-us-east-wkr-01
   ```

5. **Tunnel Activation:**
   ```
   [agent-us-east-wkr-01] Received start_tunnel command for tunnel-acme-cloudapp
   [agent-us-east-wkr-01] Creating tunnel service with tenant constraints
   [agent-us-east-wkr-01] Service placement: node.labels.datacenter==us-east
   [agent-us-east-wkr-01] Tunnel service active: acme.cloudapp.com → running
   ```

6. **Instant Activation:**
   ```
   [master] Tunnel tunnel-acme-cloudapp is now active
   [master] DNS propagation complete
   [master] Sending webhook: tenant acme-corp domain activated
   [cloudapp-api] Tenant provisioning complete: acme.cloudapp.com LIVE
   ```

**Total time from API call to live domain: ~45 seconds**

### Production Operations

**Daily Operations Dashboard:**
```
CloudApp SaaS Platform - Production Status
═══════════════════════════════════════════

Swarm Clusters: 3 regions (12 nodes total)
Active Agents: 9 workers
Managed Tunnels: 847 active
Tenant Services: 2,341 containers across 847 tenants

Recent Activity (Last 1 hour):
- New tenants onboarded: 12
- Tunnel assignments: 12
- Service scaling events: 45
- Failed tunnel attempts: 0

Regional Distribution:
- US-East: 312 tenants, 3 agents
- US-West: 298 tenants, 3 agents
- EU: 237 tenants, 3 agents

Top Resource Consumers:
1. enterprise-client-xyz: 12 replicas, 3 tunnels
2. mega-corp-platform: 8 replicas, 2 tunnels
3. startup-app-2024: 6 replicas, 1 tunnel
```

---

## Scenario 3: DevOps CI/CD Pipeline Integration

### Business Context
- **Company**: DevCorp Software House
- **Use Case**: Automated preview environments for pull requests
- **Challenge**: Dynamic environment creation/destruction with custom URLs
- **Requirements**: GitOps integration, automatic cleanup

### Workflow Integration

**GitHub Actions Workflow:**
```yaml
# .github/workflows/preview-deploy.yml
name: Deploy Preview Environment

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  deploy-preview:
    runs-on: self-hosted
    steps:
      - name: Generate Preview Domain
        id: domain
        run: |
          BRANCH_NAME=$(echo ${{ github.head_ref }} | sed 's/[^a-zA-Z0-9]/-/g' | tr '[:upper:]' '[:lower:]')
          PREVIEW_DOMAIN="pr-${BRANCH_NAME}-${{ github.event.number }}.preview.devcorp.com"
          echo "domain=$PREVIEW_DOMAIN" >> $GITHUB_OUTPUT

      - name: Deploy Preview Stack
        run: |
          envsubst < preview-stack-template.yml > preview-stack-${{ github.event.number }}.yml
          docker stack deploy -c preview-stack-${{ github.event.number }}.yml \
            preview-pr-${{ github.event.number }}
        env:
          PREVIEW_DOMAIN: ${{ steps.domain.outputs.domain }}
          GIT_SHA: ${{ github.sha }}
          PR_NUMBER: ${{ github.event.number }}

      - name: Wait for Deployment
        run: |
          timeout 300 bash -c 'until curl -f https://${{ steps.domain.outputs.domain }}/health; do sleep 10; done'

      - name: Comment PR
        uses: actions/github-script@v6
        with:
          script: |
            github.rest.issues.createComment({
              issue_number: ${{ github.event.number }},
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: '🚀 Preview environment deployed!\n\n**Preview URL:** https://${{ steps.domain.outputs.domain }}\n\n_This environment will be automatically destroyed when the PR is closed._'
            })
```

**Preview Stack Template:**
```yaml
# preview-stack-template.yml
version: '3.8'
services:
  preview-app:
    image: devcorp/app:${GIT_SHA}
    labels:
      - "dockflare.enable=true"
      - "dockflare.domain=${PREVIEW_DOMAIN}"
      - "dockflare.port=3000"
      - "preview.pr-number=${PR_NUMBER}"
      - "preview.git-sha=${GIT_SHA}"
      - "preview.auto-cleanup=true"
    environment:
      - NODE_ENV=preview
      - DATABASE_URL=postgres://preview-${PR_NUMBER}@preview-db/app
      - API_BASE_URL=https://${PREVIEW_DOMAIN}/api
    networks:
      - cloudflare-net
      - preview-network
    deploy:
      mode: replicated
      replicas: 1
      placement:
        constraints:
          - node.labels.tier==preview
      resources:
        limits:
          memory: 512M
          cpus: '0.5'
```

**Agent Response to PR Deployment:**
```
[agent-preview-01] Detected event 'container_start' for service preview-pr-123_preview-app
[agent-preview-01] Preview environment labels detected:
  - Domain: pr-feature-auth-123.preview.devcorp.com
  - PR Number: 123
  - Git SHA: abc123def456
[agent-preview-01] Reporting container_start to master with preview context
[master] Creating temporary tunnel for preview environment
[master] Tunnel tunnel-pr-123-preview created and assigned
[agent-preview-01] Preview tunnel active: pr-feature-auth-123.preview.devcorp.com → running
```

**Automatic Cleanup on PR Close:**
```yaml
# .github/workflows/preview-cleanup.yml
name: Cleanup Preview Environment

on:
  pull_request:
    types: [closed]

jobs:
  cleanup:
    runs-on: self-hosted
    steps:
      - name: Remove Preview Stack
        run: |
          docker stack rm preview-pr-${{ github.event.number }}

      - name: Cleanup Tunnel
        run: |
          curl -X DELETE \
            -H "Authorization: Bearer $MASTER_API_TOKEN" \
            $DOCKFLARE_MASTER_URL/api/v2/tunnels/preview-pr-${{ github.event.number }}
```

**Agent Response to Cleanup:**
```
[agent-preview-01] Detected event 'container_stop' for service preview-pr-123_preview-app
[agent-preview-01] Preview environment shutdown detected
[agent-preview-01] Reporting container_stop with cleanup context
[master] Removing tunnel tunnel-pr-123-preview
[master] DNS record removed: pr-feature-auth-123.preview.devcorp.com
[agent-preview-01] Tunnel cleanup completed
```

---

## Key Benefits Demonstrated

### 1. **Zero-Configuration Discovery**
- Agents automatically detect and report new services
- No manual tunnel configuration required
- Label-based service classification

### 2. **Multi-Node Orchestration**
- Services can run on any node in the cluster
- Tunnels intelligently placed based on constraints
- Load balancing across service replicas

### 3. **Dynamic Scaling**
- Automatic tunnel management as services scale
- Node failure resilience with service migration
- Resource-aware placement decisions

### 4. **Operational Simplicity**
- Single deployment model across environments
- Unified monitoring and logging
- GitOps-friendly automation

### 5. **Enterprise Features**
- Multi-tenant isolation
- Regional deployment support
- Fine-grained access control
- Audit trail and compliance

These scenarios demonstrate how the unified DockFlare Agent seamlessly integrates into real-world Docker Swarm environments, providing automated ingress management that scales from simple applications to complex multi-tenant platforms.