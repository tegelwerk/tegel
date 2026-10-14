# Edge deployment

One `podman compose` stack behind Caddy. Caddy is the only published port. Copy `edge.env.example` to `edge.env` and edit that file. `compose.yml` and the `Caddyfile` take every port, origin, and path from it.

`PUBLIC_ORIGIN` is the scheme, host, and port with no path, for example `https://xxx.yy.gov:8082`. The services are paths after that origin:

| Path (default) | Service |
| --- | --- |
| `PATH_DATA` (`/data`) | data server, prefix stripped |
| `PATH_AUTH` (`/auth`) | Keycloak, prefix kept |
| `PATH_PREFECT` (`/prefect`) | Prefect UI and API, prefix stripped |

`TEGEL_BIND` is the host port published to Caddy. It can differ from the port in `PUBLIC_ORIGIN` when a forwarder sits in front.

`TLS_MODE=terminate` makes Caddy speak HTTPS with `tls/cert.pem` and `tls/key.pem`. The hostname from `PUBLIC_ORIGIN` is a network alias of Caddy, so issuer and discovery stay on the container network. `TLS_MODE=behind` means an outer proxy already terminated TLS and forwards HTTP. In that mode the public hostname is left to the outer proxy.

From this directory:

```bash
cp edge.env.example edge.env
# set PUBLIC_ORIGIN, TEGEL_BIND, TEGEL_OUTPUT, TEGEL_PROFILE, TEGEL_SCHEMA_ROOT, and TEGEL_RECORDS
./up
```

`./up` checks that every value in `edge.env` is set, derives the Caddy listen port from `PUBLIC_ORIGIN`, and runs `podman compose up --build`.

- `TEGEL_PROFILE` is the incarnation's profile YAML. Its directory is mounted read-only at `/spec`.
- `TEGEL_SCHEMA_ROOT` is a checkout of the schema's own repository. The profile's `schema_path` is relative to it. It is mounted read-only at `/schemas` in the data server and the ETL worker.
- `TEGEL_RECORDS` is the directory the ingest flow walks, mounted read-only at `/records`.
- `TEGEL_OUTPUT` holds the production catalog, staging catalogs, the ingest error log, and the credentials file. Keep it outside the git checkout.
- `TEGEL_ON_ERROR` is `skip` (log a bad record and continue) or `fail` (stop the run at the first bad record).
- `TEGEL_UPSTREAMS` is optional: the host path of an upstreams YAML that lists the other Tiled servers data parts may point at (see "Remote parts" in [`dataserver.md`](../../tegel-core/specs/dataserver.md)). Its directory is mounted read-only at `/upstreams` in the data server and the ETL worker. Put the secret each upstream names (`api_key_env`, `client_secret_env`) in `edge.env`, never in the YAML. Leave it empty for no upstreams.

The data server and the ETL worker both set `TEGEL_READABLE_STORAGE=/records`. Tiled reads registered array and table parts only from there, and staging rejects a part outside it.

The data server, the pipeline workers, and the one-shot setup services use the image `tegel-dataserver:local`, built from `xfm_ssrl_tegel/Containerfile` in the directory that contains this repository.

## Services

| Service | Runs |
| --- | --- |
| `caddy` | the edge proxy |
| `credentials` | `python -m tegel.actions.credentials`, once, writing `$TEGEL_OUTPUT/credentials` |
| `keycloak` | Keycloak 26.4 with its data in the `keycloak_data` volume |
| `realm-init` | `python -m tegel.actions.realm`, once |
| `prefect` | Prefect 3 server with its data in the `prefect_data` volume |
| `dataserver` | `python -m tegel.dataserver` after the issuer's discovery document answers; catalog `sqlite:////output/catalog.db`; serves data parts from `/records` and proxies remote parts |
| `catalog-updater` | `python -m tegel.actions.update` |
| `etl` | `python -m tegel.actions.ingest`, serving the ingest deployment |
| `ingest` | one ingest run over `/records` |

The ingest flow stages records, the catalog updater merges them into the production catalog, and the ingest flow then calls `POST /refresh-catalog` on the data server. Submit another run with:

```bash
podman compose exec etl python -m tegel.actions.ingest directories /records
```

Records that staging could not ingest are listed in `$TEGEL_OUTPUT/ingest-errors.jsonl` on the host. Show the latest run's:

```bash
podman compose exec etl python -m tegel.actions.errors --last
```

The file only grows. Rotating it is the operator's job.
