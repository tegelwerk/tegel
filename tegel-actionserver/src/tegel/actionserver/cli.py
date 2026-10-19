"""``tegel-actionserver serve | key | actions``."""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tegel-actionserver")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the server; configuration comes from TEGEL_* variables")
    key = sub.add_parser("key", help="make an API key and the TEGEL_API_KEYS entry for it")
    key.add_argument("--subject", required=True)
    key.add_argument("--scopes", default="actions:read,actions:run")
    listing = sub.add_parser("actions", help="list the installed actions")
    listing.add_argument("--pattern", default="*")
    args = parser.parse_args(argv)

    if args.command == "key":
        from tegel.actionserver.auth import new_key

        plain, digest = new_key()
        scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
        print(f"key (give this to {args.subject}; it is not stored): {plain}", file=sys.stderr)
        print(f"  - sha256: {digest}\n    subject: {args.subject}\n    scopes: {scopes}")
        return 0
    if args.command == "actions":
        from tegel.actionserver.loading import load_actions

        for item in load_actions(args.pattern).all():
            print(json.dumps({"ref": item.card.ref, "title": item.card.title,
                              "scope": item.card.scope, "resources": item.card.resources}))
        return 0

    import uvicorn

    from tegel.actionserver.app import create_app
    from tegel.actionserver.config import ActionServerConfig

    config = ActionServerConfig.from_env()
    app = create_app(config)
    # Tiled's eager task factory conflicts with uvloop; the asyncio loop works with both.
    uvicorn.run(app, host=config.host, port=config.port, loop="asyncio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
