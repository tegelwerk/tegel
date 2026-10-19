# tegel-actionserver

The standalone Tegel action server. It runs registered actions on request, stores every finished run as a resource in its own results catalog, and serves that catalog with the data server read API, so a dashboard reads data and results with one client. The contract is [`tegel-core/specs/actionserver.md`](../tegel-core/specs/actionserver.md).

It needs no data server deployment, no Prefect and no Keycloak: one process, one directory, one auth provider.

```
POST /runs → validate → plan inputs → queue → stage → work → destage → results catalog
             400, 403    409 if slow           inputs   action  outputs, RO-Crate, Tegel row
```

A run waits in the queue for a worker and for every resource its card names (`gpu`, `catalog-writer`). Stage reads inputs with the caller's token; the token is dropped before the action runs.

## Run it

```bash
cd tegel-actionserver && uv sync
uv run tegel-actionserver key --subject ana --scopes actions:read,actions:run > keys.yaml.part
# the key itself is printed on stderr; keep it for the client
printf 'keys:\n' > keys.yaml && cat keys.yaml.part >> keys.yaml

export TEGEL_PUBLIC_ORIGIN=http://127.0.0.1:8010
export TEGEL_RESULTS_DIR=$PWD/var
export TEGEL_API_KEYS=$PWD/keys.yaml
export TEGEL_READABLE_STORAGE=/data/experiments   # where file: inputs may come from
uv run tegel-actionserver serve
```

```bash
KEY=...   # from the key command
curl -H "Authorization: Bearer $KEY" http://127.0.0.1:8010/actions
curl -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
     -d '{"action": "array.stats", "inputs": {"data": "file:///data/experiments/x.npy"}}' \
     http://127.0.0.1:8010/runs
curl -X POST -H "Authorization: Bearer $KEY" "http://127.0.0.1:8010/runs/<id>/wait?timeout=60"
curl -H "Authorization: Bearer $KEY" http://127.0.0.1:8010/resources/<id>/data
```

Every variable is listed under Configuration in the spec. With Keycloak, set `TEGEL_AUTH=keycloak` and the `TEGEL_OIDC_*` variables; scopes come from the token's `scope` claim.

## Write an action

```python
from tegel.actionserver import Quantity, action, port

@action("spectra.peak", version="0.1.0",
        inputs=[port("spectrum", "table")],
        outputs=[port("position", "quantity", unit="eV"), port("fit", "table")],
        resources=["gpu"], software=["numpy"])
def peak(ctx, spectrum, *, window: float = 2.0):
    """Position of the strongest peak."""
    ...
    return {"position": Quantity(x0, uncertainty=dx, method="Gaussian fit"), "fit": frame}
```

Register it on the `tegel.actions` entry-point group of your package and install the package next to the server. Inputs arrive as arrays, data frames, parsed JSON or local paths, by port kind; outputs go back by port name. `examples/battery` is a complete incarnation package (XRD, ASE relax and MD, MSD diffusion).

## Layout

| Module | Role |
| --- | --- |
| `app.py` | builds Tiled on the results catalog, the data server read routes, and the action routes |
| `routes.py` | the HTTP API and the `actions:read` gate on the read side |
| `service.py` | validation, plans, caching, the run lifecycle and its three phases |
| `staging.py` | input routes (`local`, `tiled`, `bytes`, `bind`), size estimates, reads |
| `destage.py` | outputs to files and Tiled nodes, the RO-Crate, the `{record, tegel}` row |
| `executor.py` | worker threads with resource slots |
| `runs.py` | the run table and measured throughput (SQLite) |
| `tokens.py` | the in-memory token vault, forwarding and RFC 8693 exchange |
| `auth.py` | the `apikey` provider and scope checks |
| `authoring.py` | `@action`, `port`, `Quantity` |
| `results/` | the packaged results profile and LinkML schema |

## Develop

```bash
uv sync --group dev && uv run pytest
uv run --with "tiled[server,formats]==0.2.18" pytest   # the Tiled release tegel-base locks
uv run python scripts/export_openapi.py                 # after changing a route
```

BSD-3-Clause — see [LICENSE](LICENSE).
