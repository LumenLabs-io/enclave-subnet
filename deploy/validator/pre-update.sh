#!/bin/sh
set -eu

STATE_ROOT="${ENCLAVE_STATE_ROOT:-/var/lib/enclave}"

if enclave-validator status --state-root "$STATE_ROOT" --quiet; then
    echo "enclave-pre-update: no round in flight, update may proceed" >&2
    exit 0
fi

echo "enclave-pre-update: a round is in flight, deferring the update" >&2
exit 1
