# Astra release bundle

## Start

Prerequisites: Docker Engine with Docker Compose v2.

```bash
./install.sh
```

Open <http://127.0.0.1:8080>. The default configuration uses Astra's mock
model so the service can be verified without credentials.

To use a real OpenAI-compatible model, edit `.env`, set `MODEL_PROVIDER`,
`MODEL_NAME`, `MODEL_API_KEY`, and `MODEL_BASE_URL`, then run:

```bash
docker compose up -d
```

## Upgrade

Pass the new release version without or with a leading `v`:

```bash
./install.sh v0.2.0
```

Application state is stored under `./data`. Back it up before a major upgrade.

## Stop

```bash
docker compose down
```

The Compose endpoint binds to `127.0.0.1` by default. Place an authenticated
TLS reverse proxy in front of Astra before exposing it to a network.
