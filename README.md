<p align="center">
  <a href="https://dockflare.app" title="DockFlare Agent">
    <img src="https://raw.githubusercontent.com/ChrispyBacon-dev/DockFlare/main/images/bannertr.png" width="480" alt="DockFlare Banner" />
  </a>
</p>

> **Note:** This repository contains the **DockFlare Agent**, which is designed to work as a worker node in a multi-server setup. It is not a standalone project and requires the main [DockFlare application](https://github.com/ChrispyBacon-dev/DockFlare) to function.

<h1 align="center">DockFlare Agent</h1>

<p align="center">
  <em>Lightweight workers that report Docker changes, run cloudflared tunnels, and obey the DockFlare Master.</em>
</p>

<p align="center">
  <a href="https://hub.docker.com/r/alplat/dockflare-agent"><img src="https://img.shields.io/docker/pulls/alplat/dockflare-agent?style=for-the-badge" alt="Docker Pulls"></a>
  <a href="https://github.com/ChrispyBacon-dev/DockFlare-Agent-prd"><img src="https://img.shields.io/badge/Status-Beta-blue?style=for-the-badge" alt="Status"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Made%20with-Python-1f425f.svg?style=for-the-badge" alt="Python"></a>
  <a href="LICENSE.MD"><img src="https://img.shields.io/badge/License-GPL--3.0-blue.svg?style=for-the-badge" alt="License"></a>
  <a href="#"><img src="https://img.shields.io/badge/Swiss_Made-FFFFFF?style=for-the-badge&labelColor=FF0000&logo=data:image/svg%2bxml;base64,PHN2ZyB2ZXJzaW9uPSIxIiB3aWR0aD0iNTEyIiBoZWlnaHQ9IjUxMiIgdmlld0JveD0iMCAwIDMyIDMyIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPgogIDxyZWN0IHdpZHRoPSIzMiIgaGVpZHRoPSIzMiIgZmlsbD0idHJhbnNwYXJlbnQiLz4KICA8cGF0aCBkPSJtMTMgNmg2djdoN3Y2aC03djdoLTZ2LTdoLTd2LTZoN3oiIGZpbGw9IiNmZmYiLz4KPC9zdmc+" alt="Swiss Made"></a>
</p>

<p align="center">
  <a href="https://dockflare.app">🌐 Website</a> ·
  <a href="https://dockflare.app/docs/agent">📚 Agent Docs</a> ·
  <a href="https://github.com/ChrispyBacon-dev/DockFlare/issues">🐛 Report a Bug</a> ·
  <a href="https://github.com/sponsors/ChrispyBacon-dev">❤️ Sponsor</a>
</p>

---

## Overview

DockFlare 3.0 introduces a distributed control plane: a central **DockFlare Master** coordinates ingress, while lightweight **DockFlare Agents** sit next to workloads and keep their Cloudflare tunnels in sync. The agent is a headless Python service that watches Docker events, reacts to commands from the master, and supervises a dedicated `cloudflared` container.

Deploy agents on any Docker-capable host to extend DockFlare beyond a single server. Each agent maintains its own ingress rules, reports health, and continues serving traffic using the last known configuration even if the master becomes temporarily unavailable.

### Highlights

- **Distributed ingress** – manage tunnels on remote hosts without exposing raw credentials.
- **Real-time visibility** – agents stream lifecycle events, periodic status reports, and tunnel metrics back to the master.
- **Least privilege** – per-agent API keys can be rotated or revoked without affecting the rest of the fleet.
- **Resilient execution** – cached tunnel state lets agents ride out transient master outages.

---

## Architecture Snapshot

| Component | Responsibility |
|-----------|----------------|
| **DockFlare Master** | Stores desired state, reconciles DNS/Access policies, issues commands via HTTPS. |
| **Redis** | Provides the backplane for heartbeats, command queues, and shared caches. |
| **DockFlare Agent** | Runs on the managed host, watches Docker events, manages `cloudflared`, and reports status. |
| **cloudflared** | The Cloudflare tunnel process launched and supervised by the agent. |

### Repository Layout

```
.
├── DockFlare-Agent/
│   ├── __init__.py
│   ├── cloudflare_api.py
│   └── main.py
├── Dockerfile
├── docker-compose.yml
├── env-example
├── overview.json
├── requirements.txt
└── README.md
```

---

## Runtime Flow

1. **Bootstrapping** – environment variables are loaded, logging is configured, and cached agent identity/tunnel data are restored from `/app/data`.
2. **Registration** – the agent authenticates with the master using `DOCKFLARE_API_KEY`, receives (or refreshes) its Agent ID, and persists it locally.
3. **Thread fan-out** – shared Docker client powers four background workers:
   - `manage_tunnels` polls for commands (`start_tunnel`, `stop_tunnel`, `update_tunnel_config`).
   - `periodic_status_reporter` emits heartbeats and summaries of labelled containers every `REPORT_INTERVAL_SECONDS`.
   - `listen_for_docker_events` streams container lifecycle events for `dockflare.enable=true` workloads.
   - `tunnel_health_monitor` verifies the managed `cloudflared` container remains healthy.
4. **Shutdown** – `cleanup()` stops and removes the managed tunnel container before the agent exits.

### Cloudflare Helper Module

`DockFlare-Agent/cloudflare_api.py` provides the thin wrapper that the agent uses to proxy Cloudflare API calls through the master:

- `get_account_id(master_url, api_key)` – resolves the Cloudflare account the master exposes to agents.
- `generate_ingress_rules(rules)` – converts desired ingress records into a tunnel configuration payload.
- `update_tunnel_config(master_url, api_key, tunnel_id, ingress_rules)` – pushes ingress updates via the master’s API.

---

## Requirements

- DockFlare Master **v3.0 or later** running with Redis and HTTPS enabled.
- Docker Engine on every host that will run the agent.
- Network reachability from the agent to the master (public HTTPS or a private network/VPN).
- Cloudflare account + API token (managed by the master; agents never handle raw Cloudflare credentials).

---

## Configuration

Populate the following environment variables (see `env-example` for a template):

| Variable | Required | Description |
|----------|----------|-------------|
| `DOCKFLARE_MASTER_URL` | ✅ | Base URL of the DockFlare Master (`https://dockflare.example.com`). |
| `DOCKFLARE_API_KEY` | ✅ | Agent API key generated in the master UI (`Agents → Generate Key`). |
| `CLOUDFLARED_IMAGE` | ✅ | Preferred Cloudflared release (`cloudflare/cloudflared:2025.9.0`) or digest (`cloudflare/cloudflared@sha256:...`). |
| `DOCKER_HOST` | ✅ | Address of the Docker socket proxy (`tcp://docker-socket-proxy:2375`). |
| `CLOUDFLARED_NETWORK_NAME` | ❌ | Docker network used for the managed tunnel (`cloudflare-net` by default). |
| `LOG_LEVEL` | ❌ | Python logging level (`INFO` by default). |
| `REPORT_INTERVAL_SECONDS` | ❌ | Cadence for status reports (defaults to `30`). |
| `TZ` | ❌ | Host timezone exposed to the container (`UTC` by default). |

The agent persists lightweight state inside `/app/data`:

- `agent_id.txt` – the master-issued identifier for the node.
- `tunnel_state.json` – cached tunnel token, ID, name, and desired state.

Bind-mount a volume to `/app/data` in production so identity survives container restarts.

---

## Deploying the Agent

### Docker Compose (Recommended)

```yaml
version: '3.8'

services:
  docker-socket-proxy:
    image: tecnativa/docker-socket-proxy:v0.4.1
    container_name: docker-socket-proxy
    restart: unless-stopped
    environment:
      - DOCKER_HOST=unix:///var/run/docker.sock
      - CONTAINERS=1
      - EVENTS=1
      - NETWORKS=1
      - IMAGES=1
      - POST=1
      - INFO=1
      - PING=1
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock

  dockflare-agent:
    image: alplat/dockflare-agent:latest
    container_name: dockflare-agent
    restart: unless-stopped
    env_file:
      - .env
    environment:
      - DOCKER_HOST=${DOCKER_HOST:-tcp://docker-socket-proxy:2375}
      - LOG_LEVEL=${LOG_LEVEL:-info}
      - TZ=${TZ:-UTC}
    volumes:
      - agent_data:/app/data
    depends_on:
      - docker-socket-proxy
    networks:
      - cloudflare-net

volumes:
  agent_data:

networks:
  cloudflare-net:
    name: cloudflare-net
    external: true
```

- The proxy limits the Docker API surface the agent can reach; only the variables set to `1` are exposed. Attach both services to the same external network so the agent can resolve `docker-socket-proxy`.
- Granting `IMAGES=1` allows the agent to pull the managed `cloudflared` image while keeping other Docker APIs disabled.
- The agent image already runs as the unprivileged `dockflare` user (UID/GID 65532). Override with `DOCKFLARE_UID/DOCKFLARE_GID` build args if your environment requires a different mapping.
- Provide a persistent volume for `/app/data` so cached agent identity survives restarts.
- Ensure the external network declared in `CLOUDFLARED_NETWORK_NAME` exists (`docker network create cloudflare-net`).

### Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp env-example .env  # populate with master URL + API key
python DockFlare-Agent/main.py
```

The provided `docker-compose.yml` mirrors the production setup for quick validation on a workstation.

---

## Security Model & Hardening

- **Master API key** protects administrative APIs; only expose it when enrolling trusted agents.
- **Per-agent API keys** are revocable—delete the key in the master UI to immediately cut off a compromised host.
- **Transport security** – front the master with HTTPS (or Cloudflare Access) so agent traffic is encrypted end-to-end.
- **Redis** should reside on a trusted network segment and require authentication when deployed outside a lab environment.
- **Docker access** is mediated through the bundled socket proxy so the agent can only list containers, stream events, manage networks, and operate its tunnel container.
- **Least privilege container** – the agent image runs as the `dockflare` user (UID/GID 65532); no root processes remain once start-up is complete.

Recommended practices:

1. Store agent keys in a password manager and rotate them regularly.
2. Use dedicated Cloudflare tunnels per agent for blast-radius isolation.
3. Monitor heartbeat gaps on the master’s Agents page; prune offline nodes promptly.

---

## Troubleshooting

| Symptom | Resolution |
|---------|------------|
| Agent stuck in `pending` | Verify the API key, ensure the agent can reach the master, and enrol it from the UI. |
| Commands never clear | Confirm Redis connectivity and that host clocks are in sync. |
| DNS or Access policies not updating | Check agent logs (`docker logs dockflare-agent`) and confirm cloudflared is running. |
| Heartbeat offline | Inspect network path and TLS configuration between agent and master. |

The `overview.json` sample captures the telemetry an active agent reports back to the master and can be used as a reference when debugging payloads.

---

## Next Steps

- Follow the DockFlare Master [Quick Start](https://dockflare.app/docs) to prepare the control plane.
- Generate an agent key in the master UI and deploy this container on remote hosts.
- Track upcoming releases from the [DockFlare Agent Docker Hub repository](https://hub.docker.com/r/alplat/dockflare-agent).

---

## License

DockFlare Agent is open-source software licensed under the [GPL-3.0 license](LICENSE.MD).
