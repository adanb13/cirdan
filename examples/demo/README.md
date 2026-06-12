# Cirdan demo

A tiny "real" system to watch Cirdan work: nginx → api → postgres/redis, a
worker that crash-loops on purpose, and one service that is declared but never
started. Everything Cirdan does — fingerprint, static+live graph merge,
dependency inference, drift, incidents, actions, responder briefs — has
something to find here.

## Run it

```bash
cd examples/demo
docker compose up -d        # pulls only small alpine images
```

## Watch Cirdan work

```bash
# 1. Map: static graph (compose file) + live graph (running containers), merged
cirdan map .
open cirdan-out/infra.html           # interactive topology; xdg-open on Linux

# 2. Ask the graph things
cirdan query "what depends on postgres?"
cirdan query "which services are exposed publicly?"
cirdan query "what broke in the last hour?"

# 3. Generate views on demand
cirdan show "show api as a dependency graph"

# 4. Incidents: flaky-worker is crash-looping with error logs
cirdan incidents                     # opens an incident (restart loop / error cluster)
cirdan explain <incident-id>         # evidence, blast radius, timeline

# 5. Actions through your own access
cirdan actions list api
cirdan actions run docker.restart:demo-api-1 --yes   # executed, recorded, verified

# 6. The responder brief an agent would receive
cirdan respond <incident-id> --dry-run

# 7. Live stream (Ctrl-C to stop); in another terminal: docker kill demo-redis-1
cirdan watch .
```

What to look for in `cirdan map .` output:

- `api → postgres / redis` CONNECTS_TO edges marked **INFERRED**, with the
  `DATABASE_URL`/`REDIS_HOST` env vars as evidence
- `web` ROUTES_TO `api` from both the compose `depends_on` and nginx.conf
- **drift**: `cron-reporter is declared (docker-compose) but nothing matching is running`
- **unhealthy**: the flaky-worker container exited/restarting

## Tear down

```bash
docker compose down
```
