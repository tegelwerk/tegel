#!/bin/sh
# Listen on the port inside PUBLIC_ORIGIN. Ports and paths come from the environment.
set -eu
routes=/edge/Caddyfile
out=/tmp/Caddyfile
if [ "$TLS_MODE" = "terminate" ]; then
  if [ ! -f /certs/cert.pem ] || [ ! -f /certs/key.pem ]; then
    echo "TLS_MODE=terminate needs cert.pem and key.pem mounted at /certs" >&2
    exit 1
  fi
  tls_line="tls /certs/cert.pem /certs/key.pem"
elif [ "$TLS_MODE" = "behind" ]; then
  tls_line="trusted_proxies static private_ranges"
else
  echo "TLS_MODE must be terminate or behind" >&2
  exit 1
fi
cat > "$out" <<EOF
:${CADDY_LISTEN} {
	${tls_line}
	import ${routes}
}
EOF
exec caddy run --config "$out" --adapter caddyfile
