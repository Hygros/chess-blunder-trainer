# Docker Deployment

## Quick Start

### Docker Run

```bash
docker run -p 8000:8000 -v $(pwd)/data:/app/data ghcr.io/mrlokans/blunder-tutor:latest
```

### Docker Compose

```bash
git clone https://github.com/MrLokans/chess-blunder-trainer.git
cd chess-blunder-trainer
docker compose up -d
```

Open http://localhost:8000 and enter your username.

Supports `linux/amd64` and `linux/arm64` (Apple Silicon, Raspberry Pi).

## Configuration

Optionally create a `.env` file (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `LICHESS_USERNAME` | — | Your Lichess username |
| `CHESSCOM_USERNAME` | — | Your Chess.com username |
| `STOCKFISH_DEPTH` | `13` | Engine search depth (10–20). Higher = more accurate, slower |
| `STOCKFISH_TIME` | `5` | Per-position time limit in seconds (max `5`) |
| `ENGINE_POOL_SIZE` | `4` | Number of Stockfish processes. Lower = less RAM, less throughput |
| `STOCKFISH_HASH_MB` | `128` | Hash memory per Stockfish process in MB |
| `CACHE_ENABLED` | `true` | Toggle in-memory API cache |
| `CACHE_DEFAULT_TTL` | `300` | Cache TTL in seconds |
| `PORT` | `8000` | Server port |

Usernames can also be set through the web UI on first launch.

## Balanced Performance Preset

If your container currently uses around 1.4 GB RAM, this preset usually keeps
analysis responsive while reducing peak memory:

```env
STOCKFISH_DEPTH=13
STOCKFISH_TIME=5
ENGINE_POOL_SIZE=2
STOCKFISH_HASH_MB=96
CACHE_ENABLED=true
CACHE_DEFAULT_TTL=240
```

For Docker Compose, also set service limits:

```yaml
mem_limit: 1600m
mem_reservation: 1100m
cpus: 2.0
```

Validate with:

```bash
docker stats blunder-tutor-local
```

## Data Persistence

All data lives in `/app/data` inside the container. The `-v $(pwd)/data:/app/data` mount keeps it across restarts.

**Backup:**

```bash
tar -czf blunder-tutor-backup-$(date +%Y%m%d).tar.gz data/
```

**Host ownership:** the image runs as UID/GID 1000 (`appuser`). If your host `./data` directory is owned by a different UID, either `chown -R 1000:1000 data/` once, override `user:` in `docker-compose.yml`, or run `docker run --user $(id -u):$(id -g) ...`.

## Updating

```bash
docker pull ghcr.io/mrlokans/blunder-tutor:latest
docker compose down && docker compose up -d
```

Database migrations run automatically on startup.

## Troubleshooting

**Container won't start** — check logs with `docker compose logs blunder-tutor`

**Analysis is slow** — lower `STOCKFISH_DEPTH` to 10–12 in `.env`

**Port conflict** — change the host port: `-p 8080:8000`
