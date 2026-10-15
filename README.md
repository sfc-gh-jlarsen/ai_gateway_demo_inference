# AI Gateway Demo

Call several frontier models through a single Snowflake endpoint using [Cortex AI Gateway](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-ai-gateway).

One credential, one endpoint, many model families:

| Model | API surface |
|---|---|
| Claude (Anthropic) | `/v1/messages` |
| OpenAI, etc. | `/v1/chat/completions` |

## Prerequisites

- A Snowflake account with AI Gateway enabled (`DESCRIBE AI GATEWAY SNOWFLAKE;` to verify)
- A [Personal Access Token (PAT)](https://docs.snowflake.com/en/user-guide/admin-personal-access-token) or OAuth token
- A connection entry in `~/.snowflake/connections.toml` with `account` and `token`/`password` fields
- Python 3.10+

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Commands

The CLI has three subcommands. All require `--connection <name>` (or the `SNOWFLAKE_CONNECTION` env var).

### test — sweep all models

Send a canned prompt to every default model and report timing/tokens:

```bash
python ai_gateway_demo.py -c my_account test
python ai_gateway_demo.py -c my_account test --models "claude-sonnet-5-5,openai-gpt-5.5"
```

### prompt — send a single prompt

```bash
python ai_gateway_demo.py -c my_account prompt --model claude-sonnet-5-5 "Explain AI Gateway in two sentences"
```

### check — connectivity diagnostics

Step-by-step checks for debugging connectivity (especially PrivateLink):

```bash
python ai_gateway_demo.py -c my_account check
```

Runs these checks in order, stopping on first failure:

1. **Config** — load the connection from `connections.toml`
2. **Token** — resolve the PAT/OAuth token
3. **URL** — derive the gateway URL (public or privatelink)
4. **DNS** — resolve the gateway hostname (`socket.getaddrinfo`)
5. **TCP** — connect to port 443
6. **TLS** — handshake and print certificate subject/issuer
7. **HTTP** — GET the gateway URL with Bearer token (404/405 = routing works)
8. **Model call** — fire a real completion to prove end-to-end

Use `--continue` to run all checks even after a failure:

```bash
python ai_gateway_demo.py -c my_account check --continue
```

## Environment variables

All optional when `--connection` is provided:

| Variable | Purpose |
|---|---|
| `SNOWFLAKE_CONNECTION` | Connection name (used when `--connection` is omitted) |
| `SNOWFLAKE_TOKEN` | Overrides the token from the connection file |
| `AI_GATEWAY_URL` | Overrides the auto-derived gateway URL |
| `AI_GATEWAY_PRIVATELINK` | Set to `1` to use the privatelink host |
| `AI_GATEWAY_MODELS` | Comma-separated model list (for `test` subcommand) |

## connections.toml example

```toml
[my_account]
account = "myorg-myaccount"
token = "ver:1:..."
```
