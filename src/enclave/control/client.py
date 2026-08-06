from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from enclave.constants import MECHANISM_VERSION, NETUID
from enclave.control.directive import Directive, verify_directive
from enclave.errors import ConfigError, ProtocolError

__all__ = ["ControlPlane", "Signer", "request_signature_headers"]

Signer = Callable[[bytes], bytes]

_TIMEOUT = 20.0


def signing_bytes(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return "\0".join((method.upper(), path, timestamp, nonce, digest)).encode()


def request_signature_headers(
    hotkey: str, sign: Signer, method: str, path: str, body: bytes = b""
) -> dict[str, str]:
    timestamp = str(time.time())
    nonce = secrets.token_hex(16)
    signature = sign(signing_bytes(method, path, timestamp, nonce, body))
    return {
        "x-enclave-hotkey": hotkey,
        "x-enclave-signature": "0x" + signature.hex(),
        "x-enclave-timestamp": timestamp,
        "x-enclave-nonce": nonce,
    }


class ControlPlane:
    def __init__(
        self,
        base_url: str,
        hotkey: str,
        sign: Signer,
        owner_public_key: str,
        netuid: int = NETUID,
        mechanism_version: int = MECHANISM_VERSION,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url:
            raise ConfigError("control plane base url is required")
        self._base = base_url.rstrip("/")
        self._hotkey = hotkey
        self._sign = sign
        self._owner = owner_public_key
        self._netuid = netuid
        self._mechanism_version = mechanism_version
        self._client = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=False)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ControlPlane:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, path: str) -> dict[str, Any]:
        headers = request_signature_headers(self._hotkey, self._sign, "GET", path)
        response = self._client.get(f"{self._base}{path}", headers=headers)
        return self._decode(response, path)

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw = json.dumps(dict(payload), separators=(",", ":")).encode()
        headers = request_signature_headers(self._hotkey, self._sign, "POST", path, raw)
        headers["content-type"] = "application/json"
        response = self._client.post(f"{self._base}{path}", headers=headers, content=raw)
        if response.status_code == 204:
            return {}
        return self._decode(response, path)

    def _decode(self, response: httpx.Response, path: str) -> dict[str, Any]:
        if response.status_code == 403:
            raise ConfigError(
                f"this hotkey is not admitted to validate: {self._detail(response)}"
            )
        if response.status_code == 401:
            raise ConfigError(f"control plane rejected the signature: {self._detail(response)}")
        if response.status_code >= 400:
            raise ProtocolError(f"{path} returned {response.status_code}: {self._detail(response)}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ProtocolError(f"{path} returned a non-object body")
        return payload

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        try:
            body = response.json()
            return str(body.get("detail", body))
        except Exception:
            return response.text[:200]

    def directive(self) -> Directive:
        payload = self._get("/v1/directive")
        directive = verify_directive(
            payload.get("payload", {}), str(payload.get("signature", "")), self._owner
        )
        directive.assert_runnable(self._mechanism_version, self._netuid)
        return directive

    def admitted(self) -> bool:
        return bool(self._get("/v1/admission").get("admitted", False))

    def heartbeat(self, round_id: str = "", note: str = "", ok: bool = True) -> None:
        self._post(
            "/v1/heartbeat",
            {
                "mechanism_version": self._mechanism_version,
                "round_id": round_id,
                "note": note,
                "ok": ok,
            },
        )
