FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/enclave

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --no-cache-dir ".[validator]"

COPY deploy/validator/pre-update.sh /usr/local/bin/enclave-pre-update
RUN chmod +x /usr/local/bin/enclave-pre-update

# Prices arrive in the signed directive, so there is no snapshot file to mount.
ENV ENCLAVE_LEDGER_ROOT=/var/lib/enclave/ledger \
    ENCLAVE_SOCKET_ROOT=/var/lib/enclave/sockets

VOLUME ["/var/lib/enclave"]

ENTRYPOINT ["enclave-validator"]
CMD ["preflight"]
