# Deployment Troubleshooting

Short notes to bypass common issues when deploying the DockFlare Agent. See [README](../README.md) for full setup.

---

## cloudflare-net

The `cloudflared-net-init` service ensures `cloudflare-net` exists before the agent starts. Agent or Master can deploy first; the init creates the network when missing via `docker-socket-proxy`.

```yaml
cloudflared-net-init:
  image: docker:cli
  environment:
    - DOCKER_HOST=${DOCKER_HOST:-tcp://docker-socket-proxy:2375}
  command:
    - sh
    - -c
    - |
      if [ -z "$(docker network ls -f name=cloudflare-net -q)" ]; then
        echo "Network cloudflare-net does not exist, creating..."
        docker network create cloudflare-net
      else
        echo "Network cloudflare-net exists, skipping creation."
      fi
  restart: "no"
  depends_on:
    docker-socket-proxy:
      condition: service_started
  networks:
    - docker-socket-proxy-net
```

Add to `dockflare-agent`:

```yaml
depends_on:
  docker-socket-proxy:
    condition: service_started
  cloudflared-net-init:
    condition: service_completed_successfully
networks:
  - cloudflare-net
  # ... other networks
```

Declare the network in the stack

```yaml
networks:
  cloudflare-net:
    name: cloudflare-net
```

Declare the network in conatiners managed by dockflare-agent:

```yaml
networks:
  cloudflare-net:
    name: cloudflare-net
    external: true
```

---

## Permissions (bind mounts)

With bind mounts, the agent may fail to persist state (re-enrolment at each restart). Add an init that fixes ownership before the agent starts:

```yaml
dockflare-agent-init:
  image: alpine:3.20
  command: ["sh", "-c", "echo 'checking permissions...' && chown -R ${DOCKFLARE_UID:-65532}:${DOCKFLARE_GID:-65532} /app/data"]
  volumes:
    - ${DOCKFLARE_DATA_PATH}:/app/data
  networks:
    - dockflare-internal
  restart: "no"
```

Set `DOCKFLARE_UID` and `DOCKFLARE_GID` in `.env` (default 65532). Add to `dockflare-agent`:

```yaml
depends_on:
  dockflare-agent-init:
    condition: service_completed_successfully
```

---

## Master behind Cloudflare Access

Use a Service Token or IP allow policy so the agent can reach the Master. See [Cloudflare Access Service Token](../README.md#cloudflare-access-service-token) in the README.

---

## DNS in wrong zone

If records are wrongly created in another zone (e.g. main domain instead of lab domain), scope the zone:

On the **managed container**:

```yaml
labels:
  - "dockflare.zonename=<lab_domain>.tld"
```

Or via **environment** on the agent / Master:

```yaml
environment:
  - TUNNEL_DNS_SCAN_ZONE_NAMES="<lab_domain>.tld"
```

---

## docker-socket-proxy-net

Use a dedicated network for the socket proxy so it can be shared across stacks (Master, Agent, others). Move the proxy off `dockflare-internal`:

```yaml
docker-socket-proxy:
  # ...
  networks:
    - docker-socket-proxy-net
```

Attach `docker-socket-proxy-net` to services that need Docker API access (`cloudflared-net-init`, `dockflare-agent`):

```yaml
dockflare-agent:
  # ...
  networks:
    - cloudflare-net
    - dockflare-internal
    - docker-socket-proxy-net
```

Declare:

```yaml
networks:
  docker-socket-proxy-net:
    name: docker-socket-proxy-net
```
