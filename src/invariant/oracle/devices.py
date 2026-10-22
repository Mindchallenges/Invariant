"""The devices paired with the oracle's web page, and how one gets paired.

The web page (``oracle/web``) is reachable only over the person's tailnet, and
that network is the first gate. This module is the second (ADR-008 D3): each
device holds a long-lived token, and a request without a valid one gets
nothing.

* **Pairing.** ``invariant oracle web pair`` on the Mac issues a one-time
  code. It is short enough to type, lives a few minutes, and is spent by the
  first redeem. Too many wrong codes in a row cancel every pending code.
* **Tokens.** Redeeming a code mints a 256-bit token that the device keeps as
  a cookie. The store keeps only its SHA-256, so the file cannot be replayed
  as a credential. A token is that random, so a fast hash is enough; the
  code's hash is weaker, which is why a code is single-use and short-lived.
* **Revoking** one device removes its hash and leaves the others alone.

The store is ``<oracle home>/web/devices.json``. It is a source of truth, not
derived state, so it lives outside the watcher's ``oracle/`` directory, which
is safe to delete. Like the project index it refuses a newer version and a
malformed shape instead of rewriting them, keeps keys it does not know, and
is written under the home's lock. It is readable by its owner only.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.errors import InvariantError, StateError, UsageError
from .index import HomeStore

DEVICES_FILE = "web/devices.json"
VERSION = 1

#: The cookie a paired browser sends.
COOKIE_NAME = "invariant_oracle"

#: How long a pairing code lives unless ``pair --ttl`` says otherwise.
PAIRING_TTL = 300.0

#: Wrong codes in a row that cancel every pending code.
MAX_FAILED_REDEEMS = 10

#: Crockford-style: no 0/O, no 1/I, so a code read off a screen is typed right.
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
#: 10 characters of 32 is 50 bits.
CODE_LENGTH = 10

#: Bytes of entropy in a device token.
TOKEN_BYTES = 32

#: A token longer than this is not one of ours, and is not hashed.
MAX_TOKEN_LENGTH = 128


class PairingError(InvariantError):
    """A pairing code that is unknown, spent or expired."""


@dataclass(frozen=True)
class Pairing:
    """A code just issued. Only ``pair`` ever sees ``code`` in the clear."""

    code: str
    name: str
    expires: float


@dataclass(frozen=True)
class Device:
    """One paired device. The token's hash never leaves the store."""

    id: str
    name: str
    paired: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "paired": self.paired}


def _hash(secret: str) -> str:
    return "sha256:" + hashlib.sha256(secret.encode("utf-8")).hexdigest()


def normalise_code(code: str) -> str:
    """Upper case, without the spaces and dashes a person types between groups."""
    return "".join(ch for ch in code.upper() if ch.isalnum())


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


class DeviceStore:
    """``<home>/web/devices.json``: paired devices and pending codes."""

    def __init__(
        self,
        home: Path,
        clock: Callable[[], float] = time.time,
        token: Callable[[int], str] = secrets.token_urlsafe,
    ) -> None:
        self.home = Path(home)
        self.store = HomeStore(self.home)
        self.clock = clock
        self.token = token

    # -- reads -----------------------------------------------------------

    def _document(self) -> dict[str, Any]:
        return _checked(self.store.read(DEVICES_FILE, {}))

    def devices(self) -> list[Device]:
        return [_device(row) for row in self._document().get("devices", [])]

    def pending(self) -> int:
        now = self.clock()
        return sum(1 for p in self._document().get("pairings", []) if p.get("expires", 0) > now)

    def verify(self, token: str | None) -> Device | None:
        """The device ``token`` belongs to, else ``None``.

        Fails closed: a store that cannot be read verifies nobody, so a broken
        file locks every device out rather than letting one in.
        """
        if not token or len(token) > MAX_TOKEN_LENGTH:
            return None
        try:
            rows = self._document().get("devices", [])
        except StateError:
            return None
        wanted = _hash(token)
        found = None
        for row in rows:
            # Compare against every row, so the time taken says nothing.
            if hmac.compare_digest(str(row.get("hash", "")), wanted):
                found = row
        return _device(found) if found is not None else None

    # -- writes ----------------------------------------------------------

    def _locked(self):
        return self.store.locked(DEVICES_FILE, {"version": VERSION})

    def _close(self) -> None:
        try:
            os.chmod(self.home / "web", 0o700)
            os.chmod(self.home / DEVICES_FILE, 0o600)
        except OSError as exc:
            raise StateError(f"cannot restrict {DEVICES_FILE} to its owner: {exc}") from exc

    def issue(self, name: str, ttl: float = PAIRING_TTL) -> Pairing:
        """A new one-time code for a device to be called ``name``."""
        if not ttl > 0:
            raise UsageError(f"a pairing code must live a positive time, not {ttl:g}s")
        name = name.strip()
        if not name:
            raise UsageError("a device needs a name")
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        now = self.clock()
        with self._locked() as document:
            _checked(document)
            pairings = _live(document, now)
            pairings.append({"hash": _hash(code), "name": name, "expires": now + ttl})
        self._close()
        return Pairing(code=code, name=name, expires=now + ttl)

    def redeem(self, code: str) -> tuple[Device, str]:
        """Spend ``code``: a new device, and the token it is the only holder of."""
        wanted = _hash(normalise_code(code))
        now = self.clock()
        outcome: dict[str, Any] = {}
        with self._locked() as document:
            _checked(document)
            pairings = _live(document, now)
            match = next(
                (p for p in pairings if hmac.compare_digest(str(p.get("hash", "")), wanted)),
                None,
            )
            if match is None or not normalise_code(code):
                failed = int(document.get("failed", 0)) + 1
                document["failed"] = failed
                if failed >= MAX_FAILED_REDEEMS:
                    pairings.clear()
                    document["failed"] = 0
                outcome["error"] = (
                    "that pairing code is not valid: it is wrong, already used, or "
                    "expired. Run 'invariant oracle web pair' on the Mac for a new one"
                )
            else:
                pairings.remove(match)
                document["failed"] = 0
                devices = document.setdefault("devices", [])
                taken = {row.get("id") for row in devices}
                device_id = secrets.token_hex(4)
                while device_id in taken:
                    device_id = secrets.token_hex(4)
                token = self.token(TOKEN_BYTES)
                row = {"id": device_id, "name": match.get("name", "device"),
                       "hash": _hash(token), "paired": _iso(now)}
                devices.append(row)
                outcome["device"], outcome["token"] = _device(row), token
        self._close()
        if "error" in outcome:
            raise PairingError(outcome["error"])
        return outcome["device"], outcome["token"]

    def revoke(self, device_id: str) -> Device:
        """Forget one device. Its token stops working at its next request."""
        removed: list[dict[str, Any]] = []
        if not self.store.exists(DEVICES_FILE):
            raise UsageError(f"no paired device has the id {device_id!r}")
        with self._locked() as document:
            devices = _checked(document).get("devices", [])
            removed = [row for row in devices if row.get("id") == device_id]
            for row in removed:
                devices.remove(row)
        self._close()
        if not removed:
            raise UsageError(
                f"no paired device has the id {device_id!r}; "
                f"'invariant oracle web devices' lists them"
            )
        return _device(removed[0])


def _live(document: dict[str, Any], now: float) -> list[dict[str, Any]]:
    """The pending codes, with the expired ones dropped in place."""
    pairings = document.setdefault("pairings", [])
    pairings[:] = [p for p in pairings if p.get("expires", 0) > now]
    return pairings


def _device(row: dict[str, Any]) -> Device:
    return Device(id=str(row.get("id", "")), name=str(row.get("name", "")),
                  paired=str(row.get("paired", "")))


def _checked(document: Any) -> dict[str, Any]:
    """The stored document, or a refusal of any shape this build cannot read."""
    if not isinstance(document, dict):
        raise StateError(f"{DEVICES_FILE} is not a JSON object; inspect or remove it")
    version = document.get("version", VERSION)
    if version != VERSION:
        raise StateError(
            f"{DEVICES_FILE} is version {version}, and this build reads version "
            f"{VERSION}; upgrade invariant rather than rewrite it"
        )
    for key in ("devices", "pairings"):
        rows = document.get(key, [])
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise StateError(f"{DEVICES_FILE} has a malformed {key} list; inspect or remove it")
    return document
