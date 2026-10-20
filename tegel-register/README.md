# tegel-register

Register a URI as a node on a Tiled server. Workers then read that node with a Tiled client and do not import tegel.register.

In Tegel, the action server's stage phase uses it to bind an action's inputs, and `plan()` reports the bytes a bind would move before it moves them. The specification is [`tegel-core/specs/register.md`](../tegel-core/specs/register.md).

```bash
cd tegel-register && uv sync --group dev && uv run pytest
```

Optional extras install these packages. `tiled` installs `tiled[client]` and PyYAML. `server` installs `tiled[server]` and PyYAML. `globus` installs `globus-sdk` and PyYAML. `dev` installs the test tools, `tiled[all]`, numpy, pandas, pyarrow, and globus-sdk. `examples/reference.py` imports numpy and `tiled.server`. Numpy is part of the `dev` extra.

The examples are in the repository. A source distribution includes `examples/site.example.yaml`.

## List the built-in binders

```python
from tegel.register.context import BindContext
from tegel.register.site import Site

ctx = BindContext(site=Site.from_mapping({"version": 1}))
print(" ".join(sorted(ctx.binders.protocols())))
```

## Describe a landing area

```python
from tegel.register.site import Site

site = Site.from_mapping({
    "version": 1,
    "readable": [{"local": "/data/real", "server": "/data/srv"}],
    "landing": {"local": "/data/real/incoming", "layout": "{protocol}/{sha12}/{name}"},
})
print(site.landing.layout)
```

## Ask which modes exist

```python
from tegel.register import Mode

print(" ".join(mode.value for mode in Mode))
```

`examples/reference.py` registers a file on a local Tiled server and reads those bytes back. `examples/proxy.py` skips unless `TEGEL_REGISTER_SITE`, `TEGEL_REGISTER_TILED_URI`, and `TEGEL_REGISTER_INTO` are set. `examples/acquire_globus.py` skips unless `TEGEL_REGISTER_GLOBUS_LIVE` is `1` and the site, URI, and into values are set. `examples/acquire_zenodo.py` skips unless `TEGEL_REGISTER_ZENODO` is `1` and the site, URI, and into values are set. The design is in [register.md](../tegel-core/specs/register.md). Schemes are in [SCHEMES.md](SCHEMES.md).

BSD-3-Clause — see [LICENSE](LICENSE).
