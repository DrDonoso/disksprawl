#!/bin/sh
set -eu
# The database, the scans' logs and the session key are readable by this user only.
umask 077
# One worker: the scan queue and the scheduler live in the application process. The database is
# migrated at startup.
exec python -m uvicorn --factory disksprawl.app:app --host 0.0.0.0 --port 7780 --workers 1 \
    --no-access-log --proxy-headers --forwarded-allow-ips "${TRUSTED_PROXY_IPS:-127.0.0.1}"
