"""Command line: plan, register, site check, and binders."""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from tegel.register.errors import AccessError, BindError, PluginError, SiteConfigError, URIResolverError
from tegel.register.redaction import redact_message


def main(argv: list[str] | None = None) -> int:
    """Run one command. Returns a process exit code."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 2
    try:
        return int(args.func(args))
    except (SiteConfigError, PluginError) as exc:
        _error(exc)
        return 5
    except AccessError as exc:
        _error(exc)
        return 4
    except (BindError, URIResolverError) as exc:
        _error(exc)
        return 3
    except Exception as exc:
        _error(exc)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tegel-register")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="describe a bind without writing")
    _bind_args(plan)
    plan.set_defaults(func=_plan)

    register = sub.add_parser("register", help="create the node described by a plan")
    _bind_args(register)
    register.add_argument("--on-conflict", default="return", choices=("return", "error", "replace"))
    register.add_argument("--acquire-timeout", type=float, default=None)
    register.set_defaults(func=_register)

    site = sub.add_parser("site", help="site configuration")
    site_sub = site.add_subparsers(dest="site_command", required=True)
    check = site_sub.add_parser(
        "check", help="load a site file and require readable and landing directories"
    )
    check.add_argument("path", nargs="?")
    check.set_defaults(func=_site_check)

    binders = sub.add_parser("binders", help="list built-in binders")
    binders.set_defaults(func=_binders)

    proxy = sub.add_parser("proxy", help="server-side proxy credentials")
    proxy_sub = proxy.add_subparsers(dest="proxy_command", required=True)
    credentials = proxy_sub.add_parser(
        "credentials",
        help="write the mode-0600 upstream key file the server reads",
    )
    credentials.add_argument("--base", required=True, help="upstream Tiled base URI")
    credentials.add_argument("--file", required=True, help="TEGEL_REGISTER_PROXY_CREDENTIALS path")
    credentials.add_argument(
        "--api-key",
        default=None,
        help="upstream key; default is TILED_UPSTREAM_API_KEY",
    )
    credentials.set_defaults(func=_proxy_credentials)
    return parser


def _bind_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("uri")
    parser.add_argument("--into", required=True, help="Tiled URL, or a path under target.base_uri")
    parser.add_argument("--key", default=None)
    parser.add_argument("--mode", default="auto")
    parser.add_argument("--site", default=None, help="site file; default is TEGEL_REGISTER_SITE or the user file")


def _plan(args: argparse.Namespace) -> int:
    from tegel.register import plan

    ctx = _context(args.site)
    into = _open_into(args.into, ctx)
    planned = plan(args.uri, into, key=args.key, mode=args.mode, context=ctx)
    print(planned.to_json())
    return 0


def _register(args: argparse.Namespace) -> int:
    import json

    from tegel.register import register

    ctx = _context(args.site)
    into = _open_into(args.into, ctx)
    binding = register(
        args.uri,
        into,
        key=args.key,
        mode=args.mode,
        on_conflict=args.on_conflict,
        acquire_timeout=args.acquire_timeout,
        context=ctx,
    )
    mode = binding.origin.mode.value if hasattr(binding.origin.mode, "value") else binding.origin.mode
    print(json.dumps({"path": binding.path, "created": binding.created, "mode": mode}, sort_keys=True))
    return 0


def _site_check(args: argparse.Namespace) -> int:
    from pathlib import Path

    from tegel.register.site import Site

    site = Site.load(args.path)
    for index, entry in enumerate(site.readable):
        _require_dir(Path(str(entry.local)), f"readable[{index}].local")
        _require_dir(Path(str(entry.server)), f"readable[{index}].server")
    if site.landing is not None:
        _require_dir(Path(str(site.landing.local)), "landing.local")
    print(f"ok sources={len(site.sources)} readable={len(site.readable)}")
    return 0


def _require_dir(path, key: str) -> None:
    if not path.is_dir():
        raise SiteConfigError(f"{key} is not a directory: {path}")


def _proxy_credentials(args: argparse.Namespace) -> int:
    from tegel.register.tiled_server.credentials import write_credentials

    api_key = args.api_key or os.environ.get("TILED_UPSTREAM_API_KEY")
    if not api_key:
        raise SiteConfigError("set --api-key or TILED_UPSTREAM_API_KEY")
    path = write_credentials(args.file, args.base, api_key)
    print(f"wrote {path}")
    return 0


def _binders(args: argparse.Namespace) -> int:
    del args
    from tegel.register.context import BindContext
    from tegel.register.site import Site

    ctx = BindContext(site=Site.from_mapping({"version": 1}))
    registry = ctx.binders
    for protocol in sorted(registry.protocols()):
        binder = registry.get(protocol)
        print(f"{protocol} {binder.api_version}")
    return 0


def _context(site_path: str | None):
    from tegel.register.context import BindContext
    from tegel.register.site import Site

    return BindContext(site=Site.load(site_path))


def _open_into(spec: str, ctx: Any) -> Any:
    from tiled.client import from_uri

    if "://" in spec:
        uri = spec
    else:
        base = ctx.site.target_base_uri if ctx.site is not None else None
        if not isinstance(base, str) or not base:
            raise SiteConfigError("--into path requires target.base_uri")
        uri = base.rstrip("/") + "/" + spec.strip("/")
    api_key = os.environ.get("TILED_API_KEY")
    secret_id = ctx.site.target_secret_id if ctx.site is not None else None
    if not api_key and secret_id and ctx.secrets is not None:
        secret = ctx.secrets.get_secret(secret_id)
        if isinstance(secret, dict) and isinstance(secret.get("api_key"), str):
            api_key = secret["api_key"]
    if api_key and "api_key=" not in uri:
        return from_uri(uri, api_key=api_key)
    return from_uri(uri)


def _error(exc: BaseException) -> None:
    print(redact_message(str(exc)), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
