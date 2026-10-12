#!/bin/sh
# Secrets stay in the output directory. The rest of the configuration is already in the environment.
set -eu
. /output/credentials
export TEGEL_OIDC_CLIENT_SECRET="$client_secret"
export TEGEL_SESSION_SECRET="$session_secret"
python - <<'PY'
import os
import time
import urllib.request

issuer = os.environ.get("TEGEL_OIDC_ISSUER", "").rstrip("/")
url = issuer + "/.well-known/openid-configuration"
deadline = time.time() + 180
last = ""
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status < 500:
                break
        last = f"status {response.status}"
        time.sleep(1)
    except Exception as exc:
        last = str(exc)
        print(f"issuer not ready: {last}", flush=True)
        time.sleep(1)
        continue
else:
    raise SystemExit(f"issuer not ready: {url} ({last})")
PY
exec python -m tegel.dataserver
