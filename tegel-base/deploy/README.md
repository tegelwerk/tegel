# Caddy is the only published port. Copy edge.env.example to edge.env and edit that file. compose.yml and the Caddyfile take every port, origin, and path from it.

`PUBLIC_ORIGIN` is the scheme, host, and port with no path, for example `https://xxx.yy.gov:8082`. The services are paths after that origin. `TEGEL_BIND` is the host port published to Caddy. It can differ from the port in `PUBLIC_ORIGIN` when a forwarder sits in front.

`TLS_MODE=terminate` makes Caddy speak HTTPS with `tls/cert.pem` and `tls/key.pem`. The hostname from `PUBLIC_ORIGIN` is a network alias of Caddy, so issuer and discovery stay on the container network. `TLS_MODE=behind` means an outer proxy already terminated TLS and forwards HTTP. In that mode the public hostname is left to the outer proxy.

From this directory:

```bash
cp edge.env.example edge.env
# set PUBLIC_ORIGIN, TEGEL_BIND, TEGEL_OUTPUT, TEGEL_PROFILE, TEGEL_SCHEMA_ROOT, and TEGEL_RECORDS
./up
```

`TEGEL_SCHEMA_ROOT` is a checkout of the schema's own repository. The profile's `schema_path` is relative to it, and it is mounted read-only into the data server and the ETL worker.

`./up` derives the Caddy listen port from `PUBLIC_ORIGIN` and starts the stack. The data server, the ETL worker, and the catalog updater use the image built from [xfm_ssrl_tegel/Containerfile](../../../xfm_ssrl_tegel/Containerfile).

Keycloak, Prefect, and the catalog live in named volumes or in `TEGEL_OUTPUT`. Credentials are written to `$TEGEL_OUTPUT/credentials` on first run. That directory stays outside the git checkout.

Prefect serves two deployments. The ingest flow stages records, the catalog updater is the only writer of the production catalog, and the ingest flow then calls `POST /refresh-catalog` on the data server. `./up` submits one ingest run after those processes are registered. Submit it again with:

```bash
podman compose exec etl python -m tegel.actions.ingest directories /records
```

The local [start_all.sh](../../../xfm_ssrl_tegel/dataserver/start_all.sh) is unchanged and does not use this edge.
