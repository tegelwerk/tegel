#!/bin/sh
# Load the operator credential file, then start Keycloak on HTTP for Caddy.
set -eu
. /output/credentials
export KC_BOOTSTRAP_ADMIN_USERNAME=admin
export KC_BOOTSTRAP_ADMIN_PASSWORD="$admin_password"
exec /opt/keycloak/bin/kc.sh start
