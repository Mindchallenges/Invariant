"""Web push: the oracle's alerts and chain notices on each phone that asked.

ADR-027 increment 4 (ruling 17, IDEA_013). The push channel is one more
:class:`~invariant.oracle.notify.Notifier`, fanned out beside the Mac banner,
so it gets every alert and every chain notice under the same ledgers and adds
no filter of its own. What it sends is the banner's own text: the title, the
row's label and the one-line message (:func:`payload`), never a brief, a
prompt, a transcript or a path.

* **End to end encrypted** (RFC 8291, ``aes128gcm``): :func:`encrypt` seals the
  payload for the browser's key and auth secret, so the push service relays
  ciphertext it cannot read.
* **Signed with the oracle's own key** (VAPID, RFC 8292):
  :func:`vapid_authorization` signs a short-lived ES256 token naming the push
  service and, as its ``sub`` contact, the page's own https origin.
* **Kept on the Mac** (:class:`PushBook`): ``<oracle home>/web/push.json``
  holds the VAPID key pair and each paired device's subscriptions, owner-only,
  beside the device tokens and never in a manifest. A subscription belongs to
  the device that made it: revoking the device drops it, and a send skips any
  device no longer paired.
* **Bounded** (:class:`PushNotifier`): each POST has :data:`POST_TIMEOUT`, an
  endpoint that failed rests :data:`REST_SECONDS` before it is tried again, so
  a slow push service cannot stretch every tick, and an endpoint the service
  answers 404 or 410 is removed.
* **Only to the push services browsers use** (:func:`check_subscription`): an
  endpoint elsewhere is refused, so a paired device cannot point the oracle's
  POSTs at another host.

``cryptography`` (the ``invariant[oracle]`` extra) does the P-256 and AES-GCM
work; it is imported only when needed, and :func:`unavailable` says why push
is off without it. The POST itself is injected (:func:`urllib_post` is the
real one), so no test reaches the network.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core.errors import InvariantError, StateError
from .devices import DeviceStore
from .home import PRIVATE_FILE_MODE, private_dir
from .index import HomeStore
from .notify import NobodyToNotify, Notification, NotifyError, Outcome

PUSH_FILE = "web/push.json"
VERSION = 1

#: The one record a payload is sealed into (RFC 8188's ``rs``).
RECORD_SIZE = 4096

#: How long the push service may hold a notification for a phone that is off.
TTL_SECONDS = 12 * 3600

#: How long a VAPID token is good for; RFC 8292 allows at most a day.
JWT_LIFETIME = 12 * 3600

#: Seconds one POST may take, and how long a failing endpoint then rests.
POST_TIMEOUT = 5.0
REST_SECONDS = 60.0

#: The payload's fields are cut to these lengths, so it always fits one record.
MAX_TITLE = 120
MAX_SUBTITLE = 160
MAX_MESSAGE = 600

#: Subscriptions one device may hold (a browser and its home-screen app).
MAX_PER_DEVICE = 4

#: The longest endpoint URL accepted.
MAX_ENDPOINT = 1024

#: The push services browsers subscribe through: Safari, Chrome and Edge's
#: Chromium, Firefox, and Windows. An endpoint is one of these or below one.
PUSH_SERVICES = (
    "push.apple.com",
    "fcm.googleapis.com",
    "push.services.mozilla.com",
    "notify.windows.com",
)

_STATUS_OK = range(200, 300)
_STATUS_GONE = (404, 410)


class PushError(InvariantError):
    """A subscription the oracle will not keep."""

    exit_code = 2


def unavailable() -> str | None:
    """Why push cannot work on this Mac, or ``None`` when it can."""
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return ("web push needs the cryptography package: install invariant[oracle] "
                "on the Mac")
    return None


def b64url(data: bytes) -> str:
    """Base64url without padding, as the Push API and JWTs spell bytes."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    """The bytes of base64url text, padded or not; ``ValueError`` when it is not."""
    if not isinstance(text, str):
        raise TypeError("not base64url text")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"not base64url text: {exc}") from exc


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


# -- the cryptography -----------------------------------------------------------------


def _curve():
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec


def _point(private) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _private_from(raw: bytes):
    ec = _curve()
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def new_key() -> tuple[str, str]:
    """A fresh P-256 pair: the private scalar and the uncompressed public point."""
    ec = _curve()
    private = ec.generate_private_key(ec.SECP256R1())
    scalar = private.private_numbers().private_value.to_bytes(32, "big")
    return b64url(scalar), b64url(_point(private))


def encrypt(plaintext: bytes, p256dh: bytes, auth: bytes, *, salt: bytes | None = None,
            private: bytes | None = None) -> bytes:
    """``plaintext`` sealed for one browser, as RFC 8291's ``aes128gcm`` body.

    ``p256dh`` is the browser's public point and ``auth`` its 16-byte secret.
    ``salt`` and ``private`` (the sender's one-time key) are fresh unless a
    test names them, to reproduce the RFC's example.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(plaintext) + 1 + 16 > RECORD_SIZE:
        raise ValueError(f"{len(plaintext)} bytes do not fit one record of {RECORD_SIZE}")
    ec = _curve()
    sender = _private_from(private) if private is not None else ec.generate_private_key(
        ec.SECP256R1())
    sender_public = _point(sender)
    browser = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)
    secret = sender.exchange(ec.ECDH(), browser)
    salt = salt if salt is not None else os.urandom(16)
    prk_key = _hmac(auth, secret)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + p256dh + sender_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    sealed = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + struct.pack(">I", RECORD_SIZE) + bytes([len(sender_public)]) + sender_public
    return header + sealed


def vapid_authorization(private: str, public: str, endpoint: str, contact: str,
                        now: float) -> str:
    """The ``Authorization`` header for one POST: ``vapid t=<jwt>, k=<public key>``."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    target = urlsplit(endpoint)
    head = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64url(json.dumps(
        {"aud": f"{target.scheme}://{target.hostname}", "exp": int(now) + JWT_LIFETIME,
         "sub": contact}, separators=(",", ":")).encode())
    signing = f"{head}.{claims}".encode("ascii")
    der = _private_from(unb64url(private)).sign(signing, _curve().ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    signature = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{claims}.{signature}, k={public}"


def _cut(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def payload(notification: Notification) -> bytes:
    """What a phone is sent: the banner's own text, cut to fit, and whether it sounds."""
    return json.dumps({
        "title": _cut(notification.title, MAX_TITLE),
        "subtitle": _cut(notification.subtitle, MAX_SUBTITLE),
        "message": _cut(notification.message, MAX_MESSAGE),
        "silent": notification.sound is None,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")


# -- a subscription -------------------------------------------------------------------


def _known_service(host: str) -> bool:
    return any(host == s or host.endswith("." + s) for s in PUSH_SERVICES)


def check_subscription(body: Any) -> dict[str, str]:
    """A browser's ``PushSubscription`` JSON as the three fields kept, or a refusal."""
    if not isinstance(body, Mapping):
        raise PushError("a subscription is a JSON object")
    endpoint = body.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > MAX_ENDPOINT:
        raise PushError("a subscription needs its endpoint")
    target = urlsplit(endpoint)
    try:
        port = target.port
    except ValueError:
        port = -1
    host = (target.hostname or "").lower()
    if (target.scheme != "https" or target.username is not None or port not in (None, 443)
            or not _known_service(host)):
        raise PushError("this browser's push service is not one the oracle sends to "
                        f"({', '.join(PUSH_SERVICES)})")
    keys = body.get("keys")
    if not isinstance(keys, Mapping):
        raise PushError("a subscription needs its keys")
    try:
        p256dh = unb64url(keys.get("p256dh"))
        ec = _curve()
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)
    except (TypeError, ValueError) as exc:
        raise PushError("the subscription's p256dh is not a P-256 public key") from exc
    try:
        auth = unb64url(keys.get("auth"))
    except (TypeError, ValueError) as exc:
        raise PushError("the subscription's auth secret is not base64url") from exc
    if len(auth) != 16:
        raise PushError("the subscription's auth secret is not 16 bytes")
    return {"endpoint": endpoint, "p256dh": b64url(p256dh), "auth": b64url(auth)}


# -- the book -------------------------------------------------------------------------


@dataclass(frozen=True)
class Delivery:
    """One POST's result, for :meth:`PushBook.record`."""

    endpoint: str
    ok: bool
    error: str | None = None
    gone: bool = False


class PushBook:
    """``<home>/web/push.json``: the VAPID key pair and each device's subscriptions.

    A record, not derived state: deleting it turns push off for every device
    and makes a new key, which each phone must subscribe to again. Like
    ``devices.json`` it is owner-only from creation, written under the home's
    lock, and a newer version or a malformed shape is refused, not rewritten.
    """

    def __init__(self, home: Path, clock: Callable[[], float] = time.time) -> None:
        self.home = Path(home)
        self.store = HomeStore(self.home, mode=PRIVATE_FILE_MODE)
        self.clock = clock

    def _document(self) -> dict[str, Any]:
        return _checked(self.store.read(PUSH_FILE, {}))

    def _locked(self):
        private_dir((self.home / PUSH_FILE).parent)
        return self.store.locked(PUSH_FILE, {"version": VERSION})

    # -- reads -----------------------------------------------------------

    def key(self) -> dict[str, str] | None:
        """``{private, public}``, or ``None`` before :meth:`ensure_key`."""
        vapid = self._document().get("vapid")
        return dict(vapid) if vapid else None

    def subscriptions(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._document().get("subscriptions", [])]

    def for_device(self, device_id: str) -> dict[str, Any]:
        """Whether ``device_id`` has push on, and the last delivery error to it."""
        rows = [r for r in self.subscriptions() if r.get("device") == device_id]
        errors = [r["last_error"] for r in rows if r.get("last_error")]
        return {"on": bool(rows), "count": len(rows), "error": errors[-1] if errors else None}

    # -- writes ----------------------------------------------------------

    def ensure_key(self) -> str:
        """The public key, made and kept on first need."""
        with self._locked() as document:
            vapid = _checked(document).get("vapid")
            if not vapid:
                private, public = new_key()
                vapid = document["vapid"] = {"private": private, "public": public,
                                             "created": _iso(self.clock())}
        return vapid["public"]

    def subscribe(self, device_id: str, subscription: Mapping[str, str], origin: str) -> None:
        """Keep ``subscription`` for ``device_id``; one endpoint belongs to one device."""
        with self._locked() as document:
            rows = _checked(document).setdefault("subscriptions", [])
            rows[:] = [r for r in rows if r.get("endpoint") != subscription["endpoint"]]
            rows.append({"device": device_id, **subscription, "origin": origin,
                         "added": _iso(self.clock())})
            mine = [r for r in rows if r.get("device") == device_id]
            for row in mine[:-MAX_PER_DEVICE]:
                rows.remove(row)

    def unsubscribe(self, device_id: str, endpoint: str | None) -> int:
        """Drop the device's ``endpoint``, or every one of its subscriptions."""
        return self._drop(lambda r: r.get("device") == device_id and (
            endpoint is None or r.get("endpoint") == endpoint))

    def drop_device(self, device_id: str) -> int:
        """Drop every subscription of a device that is being revoked."""
        return self._drop(lambda r: r.get("device") == device_id)

    def _drop(self, matches: Callable[[dict[str, Any]], bool]) -> int:
        if not self.store.exists(PUSH_FILE):
            return 0
        with self._locked() as document:
            rows = _checked(document).setdefault("subscriptions", [])
            gone = [r for r in rows if matches(r)]
            rows[:] = [r for r in rows if not matches(r)]
        return len(gone)

    def record(self, deliveries: list[Delivery]) -> None:
        """Note each POST's result on its subscription; a gone one is removed."""
        if not deliveries:
            return
        at = _iso(self.clock())
        by_endpoint = {d.endpoint: d for d in deliveries}
        with self._locked() as document:
            rows = _checked(document).setdefault("subscriptions", [])
            kept = []
            for row in rows:
                delivery = by_endpoint.get(row.get("endpoint"))
                if delivery is not None and delivery.gone:
                    continue
                if delivery is not None and delivery.ok:
                    row["last_ok"] = at
                    row.pop("last_error", None)
                    row.pop("last_error_at", None)
                elif delivery is not None:
                    row["last_error"] = delivery.error
                    row["last_error_at"] = at
                kept.append(row)
            rows[:] = kept


def _checked(document: Any) -> dict[str, Any]:
    """The stored document, or a refusal of any shape this build cannot read."""
    if not isinstance(document, dict):
        raise StateError(f"{PUSH_FILE} is not a JSON object; inspect or remove it")
    version = document.get("version", VERSION)
    if version != VERSION:
        raise StateError(
            f"{PUSH_FILE} is version {version}, and this build reads version "
            f"{VERSION}; upgrade invariant rather than rewrite it"
        )
    rows = document.get("subscriptions", [])
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise StateError(f"{PUSH_FILE} has a malformed subscriptions list; inspect or remove it")
    vapid = document.get("vapid")
    if vapid is not None and not (isinstance(vapid, dict) and isinstance(
            vapid.get("private"), str) and isinstance(vapid.get("public"), str)):
        raise StateError(f"{PUSH_FILE} has a malformed vapid key; inspect or remove it")
    return document


# -- the notifier ---------------------------------------------------------------------


Post = Callable[[str, bytes, Mapping[str, str], float], int]


def urllib_post(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> int:
    """POST ``body`` once and answer the status; a network failure raises ``OSError``."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


class PushNotifier:
    """Sends a notification to every subscription of a paired device.

    It raises :class:`NobodyToNotify` when no paired device has push on, and
    :class:`NotifyError` when it reached none of those that have; a device
    that failed beside one that succeeded is in :attr:`last`, which the
    fan-out reports. The book and the device list are read on every
    notification, so a subscription or a revoke takes effect at once.
    """

    channel = "push"

    def __init__(self, book: PushBook, devices: DeviceStore, post: Post,
                 clock: Callable[[], float] = time.time,
                 timeout: float = POST_TIMEOUT) -> None:
        self.book = book
        self.devices = devices
        self.post = post
        self.clock = clock
        self.timeout = timeout
        #: Per device, the outcome of the last :meth:`notify`.
        self.last: list[Outcome] = []
        #: Endpoint to the time it may be tried again, after a failure.
        self._resting: dict[str, float] = {}

    def notify(self, notification: Notification) -> None:
        self.last = []
        try:
            key = self.book.key()
            rows = self.book.subscriptions()
        except StateError as exc:
            raise NotifyError(f"cannot read {PUSH_FILE}: {exc}") from exc
        if not rows or key is None:
            raise NobodyToNotify("no device has turned push on")
        try:
            paired = {device.id: device.name for device in self.devices.devices()}
        except StateError as exc:
            raise NotifyError(f"cannot read the paired devices, so push sent nothing: "
                              f"{exc}") from exc
        rows = [row for row in rows if row.get("device") in paired]
        if not rows:
            raise NobodyToNotify("no paired device has turned push on")
        plain = payload(notification)
        deliveries: list[Delivery] = []
        for row in rows:
            name = paired[row["device"]]
            outcome, delivery = self._send(row, key, plain, notification.sound is not None)
            self.last.append(Outcome(self.channel, name, outcome is None,
                                     "sent" if outcome is None else outcome))
            if delivery is not None:
                deliveries.append(delivery)
        try:
            self.book.record(deliveries)
        except StateError:
            pass  # the notification went; only its note on the book is lost
        if not any(o.ok for o in self.last):
            raise NotifyError("push reached no device: " + "; ".join(
                f"{o.target}: {o.detail}" for o in self.last))

    def _send(self, row: Mapping[str, Any], key: Mapping[str, str], plain: bytes,
              audible: bool) -> tuple[str | None, Delivery | None]:
        """``(None, delivery)`` when sent, else ``(why, delivery or None)``."""
        endpoint = str(row.get("endpoint", ""))
        now = self.clock()
        resting = self._resting.get(endpoint)
        if resting is not None and resting > now:
            return (f"resting after a failure, tried again in "
                    f"{int(resting - now) + 1} s"), None
        try:
            body = encrypt(plain, unb64url(str(row.get("p256dh", ""))),
                           unb64url(str(row.get("auth", ""))))
            headers = {
                "TTL": str(TTL_SECONDS),
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                "Urgency": "high" if audible else "normal",
                "Authorization": vapid_authorization(
                    key["private"], key["public"], endpoint, str(row.get("origin", "")), now),
            }
            status = self.post(endpoint, body, headers, self.timeout)
        except ValueError as exc:
            return f"its subscription cannot be used: {exc}", Delivery(
                endpoint, ok=False, error=f"its subscription cannot be used: {exc}")
        except Exception as exc:  # noqa: BLE001 - a network failure is this device's alone
            self._resting[endpoint] = now + REST_SECONDS
            why = f"could not reach the push service: {exc}"
            return why, Delivery(endpoint, ok=False, error=why)
        if status in _STATUS_OK:
            self._resting.pop(endpoint, None)
            return None, Delivery(endpoint, ok=True)
        if status in _STATUS_GONE:
            return "no longer subscribed; removed", Delivery(endpoint, ok=False, gone=True)
        if status == 429 or status >= 500:
            self._resting[endpoint] = now + REST_SECONDS
        why = f"the push service answered {status}"
        return why, Delivery(endpoint, ok=False, error=why)
