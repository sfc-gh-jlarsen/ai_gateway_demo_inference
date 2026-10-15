"""
Call several frontier models through Snowflake Cortex AI Gateway.

One endpoint, one Snowflake credential, many model families:
  - Claude models  -> Anthropic Messages API   (<gateway>/v1/messages)
  - Everything else -> OpenAI Chat Completions (<gateway>/v1/chat/completions)

Usage:
  python ai_gateway_demo.py --connection <name> test
  python ai_gateway_demo.py --connection <name> prompt --model claude-sonnet-5-5 "Explain AI Gateway"
  python ai_gateway_demo.py --connection <name> check

Get the authoritative URLs for any account with:  DESCRIBE AI GATEWAY SNOWFLAKE;
"""

import argparse
import logging
import os
import socket
import ssl
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import anthropic
import tomli
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("ai_gateway_demo")

DEFAULT_MODELS = [
    "claude-haiku-5-5",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "openai-gpt-5.5",
]

DEFAULT_PROMPT = "In one sentence, how does a snowflake get its unique pattern?"
MAX_TOKENS = 1024


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def load_connection(name: str) -> dict:
    path = Path.home() / ".snowflake" / "connections.toml"
    with path.open("rb") as f:
        conns = tomli.load(f)
    if name not in conns:
        sys.exit(f"Connection '{name}' not found in {path}")
    return conns[name]


def gateway_url(conn: dict) -> str:
    if os.environ.get("AI_GATEWAY_URL"):
        return os.environ["AI_GATEWAY_URL"].rstrip("/")
    host = conn["account"].lower()
    domain = ("privatelink.snowflakecomputing.com"
              if os.environ.get("AI_GATEWAY_PRIVATELINK") == "1"
              else "snowflakecomputing.com")
    return f"https://{host}.{domain}/api/v2/aigateways/snowflake"


def resolve_token(conn: dict) -> str:
    token = os.environ.get("SNOWFLAKE_TOKEN") or conn.get("token") or conn.get("password")
    if not token:
        sys.exit("No token: set SNOWFLAKE_TOKEN or put a PAT in the connection's password/token field")
    return token


def call_claude(base_url: str, token: str, model: str, prompt: str) -> tuple[str, int, int]:
    client = anthropic.Anthropic(
        api_key="not-used",
        base_url=base_url,
        default_headers={"Authorization": f"Bearer {token}"},
    )
    resp = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    return text, resp.usage.input_tokens, resp.usage.output_tokens


def call_openai_compatible(base_url: str, token: str, model: str, prompt: str) -> tuple[str, int, int]:
    client = OpenAI(api_key=token, base_url=f"{base_url}/v1")
    resp = client.chat.completions.create(
        model=model,
        max_completion_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content, resp.usage.prompt_tokens, resp.usage.completion_tokens


def call_model(base_url: str, token: str, model: str, prompt: str) -> tuple[str, int, int]:
    if model.startswith("claude"):
        return call_claude(base_url, token, model, prompt)
    return call_openai_compatible(base_url, token, model, prompt)


# ---------------------------------------------------------------------------
# Subcommand: test
# ---------------------------------------------------------------------------

def cmd_test(args: argparse.Namespace) -> None:
    conn = load_connection(args.connection)
    base_url = gateway_url(conn)
    token = resolve_token(conn)

    if args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]
    else:
        models = [m.strip() for m in
                  os.environ.get("AI_GATEWAY_MODELS", ",".join(DEFAULT_MODELS)).split(",")
                  if m.strip()]

    prompt = DEFAULT_PROMPT
    log.info("Gateway: %s", base_url)
    log.info("Prompt:  %s\n", prompt)

    for model in models:
        api = "messages" if model.startswith("claude") else "chat/completions"
        start = time.perf_counter()
        try:
            text, tin, tout = call_model(base_url, token, model, prompt)
            log.info("[%s] via /v1/%s  (%.1fs, %d in / %d out tokens)\n  %s\n",
                     model, api, time.perf_counter() - start, tin, tout, (text or "").strip())
        except Exception as e:
            log.error("[%s] via /v1/%s  FAILED: %s\n", model, api, e)


# ---------------------------------------------------------------------------
# Subcommand: prompt
# ---------------------------------------------------------------------------

def cmd_prompt(args: argparse.Namespace) -> None:
    conn = load_connection(args.connection)
    base_url = gateway_url(conn)
    token = resolve_token(conn)

    model = args.model
    prompt = args.prompt
    api = "messages" if model.startswith("claude") else "chat/completions"

    log.info("Gateway: %s", base_url)
    log.info("Model:   %s  (via /v1/%s)", model, api)
    log.info("Prompt:  %s\n", prompt)

    start = time.perf_counter()
    text, tin, tout = call_model(base_url, token, model, prompt)
    elapsed = time.perf_counter() - start
    log.info("%s\n", (text or "").strip())
    log.info("(%.1fs, %d in / %d out tokens)", elapsed, tin, tout)


# ---------------------------------------------------------------------------
# Subcommand: check
# ---------------------------------------------------------------------------

def _step(label: str, fn, stop_on_fail: bool) -> bool:
    try:
        detail = fn()
        log.info("  PASS  %s — %s", label, detail)
        return True
    except Exception as e:
        log.error("  FAIL  %s — %s", label, e)
        if stop_on_fail:
            log.error("\nStopping at first failure. Use --continue to run all checks.")
        return False


def cmd_check(args: argparse.Namespace) -> None:
    stop = not args.keep_going

    # Step 1: config
    conn = None
    def check_config():
        nonlocal conn
        conn = load_connection(args.connection)
        return f"connection '{args.connection}' loaded, account={conn.get('account', '?')}"
    log.info("Checking connectivity to AI Gateway...\n")
    if not _step("Config", check_config, stop) and stop:
        return

    # Step 2: token
    token = None
    def check_token():
        nonlocal token
        token = resolve_token(conn)
        return f"token found ({len(token)} chars)"
    if not _step("Token", check_token, stop) and stop:
        return

    # Step 3: gateway URL
    base_url = None
    host = None
    def check_url():
        nonlocal base_url, host
        base_url = gateway_url(conn)
        host = urlparse(base_url).hostname
        return f"{base_url}  (host: {host})"
    if not _step("URL", check_url, stop) and stop:
        return

    # Step 4: DNS
    resolved_ip = None
    def check_dns():
        nonlocal resolved_ip
        results = socket.getaddrinfo(host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
        resolved_ip = results[0][4][0]
        return f"{host} -> {resolved_ip}"
    if not _step("DNS", check_dns, stop) and stop:
        return

    # Step 5: TCP
    def check_tcp():
        sock = socket.create_connection((host, 443), timeout=5)
        sock.close()
        return f"TCP connection to {host}:443 succeeded"
    if not _step("TCP", check_tcp, stop) and stop:
        return

    # Step 6: TLS
    def check_tls():
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=5) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert()
                subject = dict(x[0] for x in cert.get("subject", ()))
                issuer = dict(x[0] for x in cert.get("issuer", ()))
                cn = subject.get("commonName", "?")
                issuer_cn = issuer.get("commonName", "?")
                return f"CN={cn}, issuer={issuer_cn}, protocol={tls.version()}"
    if not _step("TLS", check_tls, stop) and stop:
        return

    # Step 7: HTTP
    def check_http():
        req = urllib.request.Request(
            base_url,
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            return f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            if e.code in (404, 405):
                return f"HTTP {e.code} (expected — no GET endpoint, but routing + auth work)"
            raise Exception(f"HTTP {e.code}: {e.reason}")
    if not _step("HTTP", check_http, stop) and stop:
        return

    # Step 8: model call
    def check_model():
        test_model = DEFAULT_MODELS[0]
        start = time.perf_counter()
        text, tin, tout = call_model(base_url, token, test_model, "Say 'hello' in one word.")
        elapsed = time.perf_counter() - start
        return f"{test_model} responded in {elapsed:.1f}s ({tin}+{tout} tokens)"
    _step("Model call", check_model, stop)

    log.info("")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_gateway_demo",
        description="Snowflake Cortex AI Gateway CLI — test, prompt, and diagnose connectivity.",
    )
    parser.add_argument(
        "--connection", "-c",
        default=os.environ.get("SNOWFLAKE_CONNECTION"),
        help="Connection name in ~/.snowflake/connections.toml (or set SNOWFLAKE_CONNECTION).",
    )

    sub = parser.add_subparsers(dest="command")

    # test
    p_test = sub.add_parser("test", help="Sweep all default models with a canned prompt.")
    p_test.add_argument("--models", "-m", help="Comma-separated model list override.")

    # prompt
    p_prompt = sub.add_parser("prompt", help="Send a prompt to a specific model.")
    p_prompt.add_argument("--model", required=True, help="Model name (e.g. claude-sonnet-5-5).")
    p_prompt.add_argument("prompt", help="The prompt text.")

    # check
    p_check = sub.add_parser("check", help="Step-by-step connectivity diagnostics.")
    p_check.add_argument("--continue", dest="keep_going", action="store_true",
                         help="Run all checks even if an earlier step fails.")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if not args.connection:
        sys.exit(
            "Error: no connection specified.\n"
            "  Use --connection <name> or set SNOWFLAKE_CONNECTION env var."
        )

    dispatch = {"test": cmd_test, "prompt": cmd_prompt, "check": cmd_check}
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
