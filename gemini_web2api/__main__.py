"""Entry point: python -m gemini_web2api"""
import argparse
import os

from .config import CONFIG, load_config, find_config
from .models import MODELS
from .gemini import HAS_HTTPX
from .logging import configure_logging, log
from .server import GeminiHandler, ThreadedServer
from . import __version__

def _cookie_status():
    accounts = CONFIG.get("accounts") or []
    if any(a.get("cookie") or a.get("cookie_file") for a in accounts if isinstance(a, dict)):
        return "yes (accounts)"
    if CONFIG.get("cookie_file"):
        return "yes"
    return "none (anonymous)"


def main():
    parser = argparse.ArgumentParser(description="Gemini Web to OpenAI API")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--cookie-file", type=str, default=None)
    parser.add_argument("--proxy", type=str, default=None, help="HTTP proxy, e.g. http://127.0.0.1:7890")
    parser.add_argument("--version", action="version", version=f"gemini-web2api {__version__}")
    args = parser.parse_args()

    config_path = args.config or os.environ.get("GEMINI_WEB2API_CONFIG") or find_config()
    if config_path:
        load_config(config_path)

    if args.port:
        CONFIG["port"] = args.port
    if args.cookie_file:
        CONFIG["cookie_file"] = args.cookie_file
    if args.proxy:
        CONFIG["proxy"] = args.proxy

    configure_logging(CONFIG)

    port = CONFIG["port"]
    server = ThreadedServer((CONFIG["host"], port), GeminiHandler)
    print(f"gemini-web2api v{__version__}")
    print(f"  Listening: http://0.0.0.0:{port}")
    print(f"  Base URL:  http://localhost:{port}/v1")
    print(f"  Models:    {', '.join(MODELS.keys())}")
    print(f"  Cookie:    {_cookie_status()}")
    print(f"  Accounts:  {len(CONFIG.get('accounts') or []) or 'legacy/single'}")
    print(f"  Proxy:     {CONFIG.get('proxy') or 'system env'}")
    print(f"  Streaming: {'httpx (true streaming)' if HAS_HTTPX else 'urllib (buffered)'}")
    print()
    log(
        "Server started: host={} port={} accounts={} cookie={} proxy={} streaming={}".format(
            CONFIG["host"],
            port,
            len(CONFIG.get("accounts") or []) or "legacy/single",
            _cookie_status(),
            CONFIG.get("proxy") or "system env",
            "httpx" if HAS_HTTPX else "urllib",
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
