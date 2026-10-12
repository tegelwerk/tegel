#!/bin/sh
# The worker uses the client secret from the output directory and then runs its command.
set -eu
. /output/credentials
export TEGEL_OIDC_CLIENT_SECRET="$client_secret"
exec "$@"
