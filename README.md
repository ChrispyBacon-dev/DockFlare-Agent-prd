<p align="center">
  <a href="https://dockflare.app" title="DockFlare Agent">
    <img src="https://raw.githubusercontent.com/ChrispyBacon-dev/DockFlare/main/images/bannertr.png" width="480" alt="DockFlare Banner" />
  </a>
</p>

<h1 align="center">DockFlare Agent</h1>

<p align="center">
  <em>Lightweight workers that report Docker changes, run cloudflared tunnels, and obey the DockFlare Master.</em>
</p>

<p align="center">
  <a href="https://hub.docker.com/r/alplat/dockflare-agent"><img src="https://img.shields.io/docker/pulls/alplat/dockflare-agent?style=for-the-badge" alt="Docker Pulls"></a>
  <a href="https://github.com/ChrispyBacon-dev/DockFlare-Agent-prd"><img src="https://img.shields.io/badge/Status-Beta-blue?style=for-the-badge" alt="Status"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Made%20with-Python-1f425f.svg?style=for-the-badge" alt="Python"></a>
  <a href="LICENSE.MD"><img src="https://img.shields.io/badge/License-GPL--3.0-blue.svg?style=for-the-badge" alt="License"></a>
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
| `LOG_LEVEL` | ❌ | Python logging level (`INFO` by default). |
| `REPORT_INTERVAL_SECONDS` | ❌ | Cadence for status reports (defaults to `30`). |
| `CLOUDFLARED_NETWORK_NAME` | ❌ | Docker network used for the managed tunnel (`cloudflare-net` by default). |

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
  dockflare-agent:
    image: alplat/dockflare-agent:stable
    container_name: dockflare-agent
    restart: unless-stopped
    env_file:
      - .env
    environment:
      - LOG_LEVEL=info        # Optional logging override
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - agent_data:/app/data
    networks:
      - cloudflare-net

volumes:
  agent_data:

networks:
  cloudflare-net:
    external: true
```

- Mount the Docker socket **read-only** so the agent can monitor containers.
- Provide a persistent volume for `/app/data`.
- Ensure the network declared in `CLOUDFLARED_NETWORK_NAME` exists (create it once with `docker network create cloudflare-net`).

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
