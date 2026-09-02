# GodOfHomelabing

**Anton — the god-tier self-hosted homelab intelligence platform.**

A monorepo consolidating every service that powers Anton, the home-lab "god of homelabbing". Each top-level directory is a deployable service with its own README, and every service is also maintained as an independent repo (mirrored to Gitea and GitHub).

## Services

| Service | Description |
|---------|-------------|
| [`observatory`](observatory/) | Next.js observatory frontend for the Anton fleet |
| [`argus`](argus/) | Internet ingestion + intelligence subsystem |
| [`forge`](forge/) | Agent execution / tool relay engine |
| [`hermes`](hermes/) | Central event bus, notifications & Telegram bot (includes `connector/`) |
| [`legacy`](legacy/) | Self-hosted journaling / memory platform (LEGACY) |
| [`oracle`](oracle/) | AI gateway — model routing + agent loop |
| [`phoenix`](phoenix/) | Monitoring orchestration & health observers |
| [`pulse`](pulse/) | Pulse exporter — Prometheus metrics + Hermes events |
| [`sentinel`](sentinel/) | AI monitor / manager over the fleet |
| [`watcher`](watcher/) | LAN device intelligence & presence detection |

## Layout

```
GodOfHomelabing/
├── observatory/   # Next.js web dashboard
├── argus/         # internet intelligence
├── forge/         # agent execution
├── hermes/        # event bus + bot (+ hermes/connector)
├── legacy/        # journaling platform
├── oracle/        # AI gateway
├── phoenix/       # monitoring orchestration
├── pulse/         # metrics exporter
├── sentinel/      # AI fleet manager
└── watcher/       # LAN monitoring
```

Each service is self-contained with its own dependencies, config (via environment variables / `.env.example`), and deployment files. No secrets are committed — only `.env.example` templates.

## Repositories

Each service is also published as its own repo and may be developed independently:

- Gitea mirror: `git.redclove.space/ak/<service>`
- GitHub mirror: `github.com/aryank2212/<service>`

## Getting started

Deploy services individually from their own folders — see each service's `README.md` for service-specific setup, configuration, and environment variables.
