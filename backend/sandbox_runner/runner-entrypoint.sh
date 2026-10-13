#!/bin/sh
set -eu

# The trusted entrypoint starts with only CHOWN/SETUID/SETGID capabilities.
# It fixes ownership on the ephemeral tmpfs workspace, then drops every
# capability and switches the actual untrusted command to the nobody UID.
chown -R 65534:65534 /workspace
exec setpriv --reuid=65534 --regid=65534 --clear-groups --bounding-set=-all "$@"
