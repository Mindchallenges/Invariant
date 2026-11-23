"""Web push from the oracle (ADR-027 increment 4, ruling 17, IDEA_013).

The push channel is one more notifier behind the seam ``oracle watch`` already
posts through. These tests run the real cryptography: a notification is
encrypted for a browser key made here and decrypted again with the browser's
half (RFC 8291), RFC 8291's own example is reproduced byte for byte, and the
VAPID token's signature is verified with the public key the page is given
(RFC 8292). Nothing reaches the network: the POST is an injected seam, and the
suite's socket guard refuses anything past loopback besides.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
import struct
import sys

import pytest

from invariant.core.errors import StateError
from invariant.oracle import push
from invariant.oracle.devices import DeviceStore
from invariant.oracle.notify import (
    FanOutNotifier,
    NobodyToNotify,
    Notification,
    NotifyError,
    Outcome,
    RecordingNotifier,
)

cryptography = pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    encode_dss_signature,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NOW = 1_792_640_000.0
APPLE = "https://web.push.apple.com/QGuQyavXutnMIthj6CZ6ENO2gWKKZ8"
GOOGLE = "https://fcm.googleapis.com/fcm/send/dXJsLXNhZmUtYmFzZTY0"
ORIGIN = "https://mac.tail1234.ts.net:7341"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Browser:
    """A browser's half of a push subscription: its key pair and auth secret."""

    def __init__(self, endpoint: str = APPLE) -> None:
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.public = self.private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.auth = os.urandom(16)
        self.endpoint = endpoint

    def subscription(self) -> dict:
        return {"endpoint": self.endpoint, "expirationTime": None,
                "keys": {"p256dh": _b64(self.public), "auth": _b64(self.auth)}}

    def decrypt(self, body: bytes) -> bytes:
        return _decrypt(body, self.private, self.public, self.auth)


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _decrypt(body: bytes, ua_private, ua_public: bytes, auth: bytes) -> bytes:
    """RFC 8291 section 3 and RFC 8188, the receiving side, written out here."""
    salt, rs, idlen = body[:16], struct.unpack(">I", body[16:20])[0], body[20]
    as_public = body[21:21 + idlen]
    ciphertext = body[21 + idlen:]
    assert rs >= len(ciphertext), "one record"
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    secret = ua_private.exchange(ec.ECDH(), peer)
    prk_key = _hmac(auth, secret)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    padded = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert padded.rstrip(b"\x00").endswith(b"\x02"), "the last record's delimiter"
    return padded.rstrip(b"\x00")[:-1]


class Post:
    """The injected POST: records each request and answers from a script."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[dict] = []

    def __call__(self, url: str, body: bytes, headers: dict, timeout: float) -> int:
        self.calls.append({"url": url, "body": body, "headers": dict(headers),
                           "timeout": timeout})
        answer = self.answers.pop(0) if self.answers else 201
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


def _pair(home, name="iPhone") -> str:
    store = DeviceStore(home)
    pairing = store.issue(name)
    device, _ = store.redeem(pairing.code)
    return device.id


def _notifier(home, post, clock=lambda: NOW):
    return push.PushNotifier(push.PushBook(home, clock=clock), DeviceStore(home), post,
                             clock=clock)


def _alert(sound="Glass") -> Notification:
    return Notification(title="invariant: Heap", subtitle="band height",
                        message="Claude asked for permission", sound=sound)


class TestEncoding:
    def test_base64url_round_trips_without_padding(self):
        assert push.b64url(b"\xff\xfe") == "__4"
        assert push.unb64url("__4") == b"\xff\xfe"

    def test_rfc_8291_appendix_a_is_reproduced_byte_for_byte(self):
        ua_private = ec.derive_private_key(
            int.from_bytes(_unb64("q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"), "big"),
            ec.SECP256R1())
        ua_public = _unb64("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4"
                           "bjyPjs7Vd8pZGH6SRpkNtoIAiw4")
        body = push.encrypt(
            b"When I grow up, I want to be a watermelon",
            ua_public,
            _unb64("BTBZMqHH6r4Tts7J_aSIgg"),
            salt=_unb64("DGv6ra1nlYgDCS1FRnbzlw"),
            private=_unb64("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"),
        )
        assert _b64(body) == (
            "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLoc"
            "InmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWL"
            "VWGNWQexSgSxsj_Qulcy4a-fN")
        assert _decrypt(body, ua_private, ua_public, _unb64("BTBZMqHH6r4Tts7J_aSIgg")) == (
            b"When I grow up, I want to be a watermelon")

    def test_a_fresh_encryption_decrypts_with_the_browsers_half(self):
        browser = Browser()
        body = push.encrypt(b'{"title": "x"}', browser.public, browser.auth)
        assert browser.decrypt(body) == b'{"title": "x"}'
        assert struct.unpack(">I", body[16:20])[0] == push.RECORD_SIZE
        # A new salt and key each time: the same text never encrypts the same.
        assert push.encrypt(b"same", browser.public, browser.auth) != push.encrypt(
            b"same", browser.public, browser.auth)

    def test_a_text_too_long_for_one_record_is_refused(self):
        browser = Browser()
        with pytest.raises(ValueError, match="one record"):
            push.encrypt(b"x" * push.RECORD_SIZE, browser.public, browser.auth)


class TestThePayload:
    def test_it_carries_the_banners_text_and_nothing_else(self):
        sent = json.loads(push.payload(_alert()))
        assert sent == {"title": "invariant: Heap", "subtitle": "band height",
                        "message": "Claude asked for permission", "silent": False}

    def test_a_silent_banner_is_a_silent_push(self):
        assert json.loads(push.payload(_alert(sound=None)))["silent"] is True

    def test_long_text_is_cut_to_fit_one_record(self):
        sent = json.loads(push.payload(Notification(
            title="t" * 500, subtitle="s" * 500, message="m" * 5000)))
        assert len(sent["title"]) == push.MAX_TITLE and sent["title"].endswith("…")
        assert len(sent["subtitle"]) == push.MAX_SUBTITLE
        assert len(sent["message"]) == push.MAX_MESSAGE
        assert len(push.payload(Notification(title="t" * 500, subtitle="s" * 500,
                                             message="é" * 5000))) < push.RECORD_SIZE - 120


class TestVapid:
    def test_the_token_is_signed_by_the_key_the_page_is_given(self):
        private, public = push.new_key()
        header = push.vapid_authorization(private, public, APPLE + "/x", ORIGIN, NOW)
        scheme, rest = header.split(" ", 1)
        fields = dict(part.strip().split("=", 1) for part in rest.split(","))
        assert scheme == "vapid" and fields["k"] == public
        head, claims, signature = fields["t"].split(".")
        assert json.loads(_unb64(head)) == {"typ": "JWT", "alg": "ES256"}
        assert json.loads(_unb64(claims)) == {
            "aud": "https://web.push.apple.com", "exp": int(NOW) + push.JWT_LIFETIME,
            "sub": ORIGIN}
        raw = _unb64(signature)
        assert len(raw) == 64
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _unb64(public))
        key.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"),
                                        int.from_bytes(raw[32:], "big")),
                   f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))

    def test_a_new_key_is_a_p256_pair(self):
        private, public = push.new_key()
        assert len(push.unb64url(private)) == 32
        assert len(push.unb64url(public)) == 65 and push.unb64url(public)[0] == 4


class TestCheckingASubscription:
    def test_a_browsers_subscription_is_kept_as_its_three_fields(self):
        browser = Browser(GOOGLE)
        assert push.check_subscription(browser.subscription()) == {
            "endpoint": GOOGLE, "p256dh": _b64(browser.public), "auth": _b64(browser.auth)}

    @pytest.mark.parametrize("endpoint", [
        "http://web.push.apple.com/x",
        "https://evil.example/x",
        "https://push.apple.com.evil.example/x",
        "https://user@web.push.apple.com/x",
        "https://web.push.apple.com:8443/x",
        "https://100.64.0.1/x",
        "https://web.push.apple.com/" + "x" * 2000,
        None,
    ])
    def test_an_endpoint_outside_the_known_push_services_is_refused(self, endpoint):
        sub = Browser().subscription()
        sub["endpoint"] = endpoint
        with pytest.raises(push.PushError):
            push.check_subscription(sub)

    def test_the_known_services_are_each_accepted(self):
        for endpoint in ("https://web.push.apple.com/a", "https://fcm.googleapis.com/fcm/send/a",
                         "https://updates.push.services.mozilla.com/wpush/v2/a",
                         "https://wns2-par02p.notify.windows.com/w/?token=a",
                         "https://web.push.apple.com:443/a"):
            sub = Browser().subscription()
            sub["endpoint"] = endpoint
            assert push.check_subscription(sub)["endpoint"] == endpoint

    @pytest.mark.parametrize("keys", [
        None, {}, {"p256dh": "AAAA", "auth": _b64(b"a" * 16)},
        {"p256dh": _b64(b"\x04" + b"\x01" * 64), "auth": _b64(b"a" * 16)},
        {"auth": _b64(b"a" * 16)},
        "not keys",
    ])
    def test_keys_that_are_not_a_p256_point_and_a_16_byte_secret_are_refused(self, keys):
        sub = Browser().subscription()
        sub["keys"] = keys
        with pytest.raises(push.PushError):
            push.check_subscription(sub)

    def test_a_short_auth_secret_is_refused(self):
        sub = Browser().subscription()
        sub["keys"]["auth"] = _b64(b"a" * 8)
        with pytest.raises(push.PushError, match="auth"):
            push.check_subscription(sub)

    def test_a_body_that_is_not_an_object_is_refused(self):
        with pytest.raises(push.PushError):
            push.check_subscription(["https://web.push.apple.com/a"])


class TestTheBook:
    def test_the_key_is_made_once_and_kept_owner_only(self, home):
        book = push.PushBook(home, clock=lambda: NOW)
        assert book.key() is None
        public = book.ensure_key()
        assert book.ensure_key() == public == book.key()["public"]
        path = home / push.PUSH_FILE
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        stored = json.loads(path.read_text())
        assert stored["version"] == push.VERSION
        assert stored["vapid"]["created"] == "2026-10-22T03:33:20+00:00"

    def test_a_subscription_is_bound_to_its_device_and_replaced_not_doubled(self, home):
        book = push.PushBook(home, clock=lambda: NOW)
        browser = Browser()
        book.subscribe("d1", push.check_subscription(browser.subscription()), ORIGIN)
        book.subscribe("d1", push.check_subscription(browser.subscription()), ORIGIN)
        rows = book.subscriptions()
        assert [(r["device"], r["endpoint"], r["origin"]) for r in rows] == [
            ("d1", APPLE, ORIGIN)]
        # The same endpoint subscribed by another device moves to it.
        book.subscribe("d2", push.check_subscription(browser.subscription()), ORIGIN)
        assert [r["device"] for r in book.subscriptions()] == ["d2"]
        assert book.for_device("d1") == {"on": False, "count": 0, "error": None}
        assert book.for_device("d2") == {"on": True, "count": 1, "error": None}

    def test_a_device_keeps_its_newest_few_subscriptions(self, home):
        book = push.PushBook(home, clock=lambda: NOW)
        for n in range(push.MAX_PER_DEVICE + 2):
            book.subscribe("d1", push.check_subscription(Browser(f"{APPLE}{n}").subscription()),
                           ORIGIN)
        endpoints = [r["endpoint"] for r in book.subscriptions()]
        assert endpoints == [f"{APPLE}{n}" for n in range(2, push.MAX_PER_DEVICE + 2)]

    def test_unsubscribe_takes_one_endpoint_or_every_one_of_the_device(self, home):
        book = push.PushBook(home, clock=lambda: NOW)
        for device, n in (("d1", 1), ("d1", 2), ("d2", 3)):
            book.subscribe(device, push.check_subscription(
                Browser(f"{APPLE}{n}").subscription()), ORIGIN)
        assert book.unsubscribe("d1", f"{APPLE}3") == 0  # another device's
        assert book.unsubscribe("d1", f"{APPLE}1") == 1
        assert book.unsubscribe("d1", None) == 1
        assert [r["device"] for r in book.subscriptions()] == ["d2"]
        assert book.drop_device("d2") == 1
        assert book.subscriptions() == []

    def test_dropping_a_device_with_no_book_writes_nothing(self, home):
        assert push.PushBook(home).drop_device("d1") == 0
        assert not (home / push.PUSH_FILE).exists()

    def test_a_newer_or_malformed_book_is_refused_not_rewritten(self, home):
        path = home / push.PUSH_FILE
        path.parent.mkdir(parents=True)
        for content in ('{"version": 2}', "[]", '{"version": 1, "subscriptions": {}}',
                        '{"version": 1, "vapid": "x"}'):
            path.write_text(content)
            book = push.PushBook(home)
            with pytest.raises(StateError):
                book.subscriptions()
            with pytest.raises(StateError):
                book.ensure_key()
            assert path.read_text() == content

    def test_unknown_keys_survive_a_write(self, home):
        path = home / push.PUSH_FILE
        path.parent.mkdir(parents=True)
        path.write_text('{"version": 1, "later": true}')
        push.PushBook(home).ensure_key()
        assert json.loads(path.read_text())["later"] is True

    def test_record_keeps_the_last_success_and_error_and_prunes_the_gone(self, home):
        book = push.PushBook(home, clock=lambda: NOW)
        for n in (1, 2, 3):
            book.subscribe("d1", push.check_subscription(
                Browser(f"{APPLE}{n}").subscription()), ORIGIN)
        book.record([push.Delivery(f"{APPLE}1", ok=True),
                     push.Delivery(f"{APPLE}2", ok=False, error="the push service answered 503"),
                     push.Delivery(f"{APPLE}3", ok=False, error="gone", gone=True)])
        rows = {r["endpoint"]: r for r in book.subscriptions()}
        assert set(rows) == {f"{APPLE}1", f"{APPLE}2"}
        assert rows[f"{APPLE}1"]["last_ok"] == "2026-10-22T03:33:20+00:00"
        assert rows[f"{APPLE}1"].get("last_error") is None
        assert rows[f"{APPLE}2"]["last_error"] == "the push service answered 503"
        assert book.for_device("d1") == {"on": True, "count": 2,
                                         "error": "the push service answered 503"}
        # A later success clears the error.
        book.record([push.Delivery(f"{APPLE}2", ok=True)])
        assert book.for_device("d1")["error"] is None

    def test_record_of_nothing_writes_nothing(self, home):
        push.PushBook(home).record([])
        assert not (home / push.PUSH_FILE).exists()


class TestTheNotifier:
    def _subscribe(self, home, device, browser):
        book = push.PushBook(home, clock=lambda: NOW)
        book.ensure_key()
        book.subscribe(device, push.check_subscription(browser.subscription()), ORIGIN)

    def test_it_sends_the_banner_encrypted_to_each_paired_device(self, home):
        phone, tablet = Browser(APPLE), Browser(GOOGLE)
        self._subscribe(home, _pair(home, "iPhone"), phone)
        self._subscribe(home, _pair(home, "iPad"), tablet)
        post = Post()
        notifier = _notifier(home, post)
        notifier.notify(_alert())
        assert [c["url"] for c in post.calls] == [APPLE, GOOGLE]
        call = post.calls[0]
        assert call["timeout"] == push.POST_TIMEOUT
        assert call["headers"]["Content-Encoding"] == "aes128gcm"
        assert call["headers"]["TTL"] == str(push.TTL_SECONDS)
        assert call["headers"]["Urgency"] == "high"
        assert call["headers"]["Authorization"].startswith("vapid t=")
        assert json.loads(phone.decrypt(call["body"])) == json.loads(push.payload(_alert()))
        assert json.loads(tablet.decrypt(post.calls[1]["body"]))["message"] == (
            "Claude asked for permission")
        assert notifier.last == [Outcome("push", "iPhone", True, "sent"),
                                 Outcome("push", "iPad", True, "sent")]
        assert all(r.get("last_ok") for r in push.PushBook(home).subscriptions())

    def test_a_silent_banner_is_sent_at_normal_urgency(self, home):
        self._subscribe(home, _pair(home), Browser())
        post = Post()
        _notifier(home, post).notify(_alert(sound=None))
        assert post.calls[0]["headers"]["Urgency"] == "normal"

    def test_nobody_subscribed_is_nobody_to_notify(self, home):
        _pair(home)
        with pytest.raises(NobodyToNotify):
            _notifier(home, Post()).notify(_alert())

    def test_a_revoked_devices_subscription_gets_nothing(self, home):
        device = _pair(home)
        self._subscribe(home, device, Browser())
        DeviceStore(home).revoke(device)
        post = Post()
        with pytest.raises(NobodyToNotify):
            _notifier(home, post).notify(_alert())
        assert post.calls == []

    def test_unreadable_devices_send_nothing(self, home):
        self._subscribe(home, _pair(home), Browser())
        (home / "web" / "devices.json").write_text("[]")
        post = Post()
        with pytest.raises(NotifyError, match="paired devices"):
            _notifier(home, post).notify(_alert())
        assert post.calls == []

    def test_an_unreadable_book_is_a_failure(self, home):
        _pair(home)
        (home / push.PUSH_FILE).write_text('{"version": 9}')
        with pytest.raises(NotifyError, match="push.json"):
            _notifier(home, Post()).notify(_alert())

    def test_one_device_failing_does_not_stop_the_other(self, home):
        self._subscribe(home, _pair(home, "iPhone"), Browser(APPLE))
        self._subscribe(home, _pair(home, "iPad"), Browser(GOOGLE))
        post = Post(503, 201)
        notifier = _notifier(home, post)
        notifier.notify(_alert())
        assert notifier.last == [
            Outcome("push", "iPhone", False, "the push service answered 503"),
            Outcome("push", "iPad", True, "sent")]
        assert push.PushBook(home).for_device(
            push.PushBook(home).subscriptions()[0]["device"])["error"] == (
            "the push service answered 503")

    def test_every_device_failing_raises_naming_each(self, home):
        self._subscribe(home, _pair(home, "iPhone"), Browser(APPLE))
        post = Post(OSError("timed out"))
        with pytest.raises(NotifyError, match="iPhone: could not reach the push service: "
                                              "timed out"):
            _notifier(home, post).notify(_alert())

    def test_a_gone_subscription_is_pruned(self, home):
        self._subscribe(home, _pair(home), Browser())
        notifier = _notifier(home, Post(410))
        with pytest.raises(NotifyError, match="no longer subscribed; removed"):
            notifier.notify(_alert())
        assert push.PushBook(home).subscriptions() == []
        with pytest.raises(NobodyToNotify):
            notifier.notify(_alert())

    def test_a_failing_endpoint_rests_so_a_slow_service_cannot_stall_every_tick(self, home):
        self._subscribe(home, _pair(home), Browser())
        clock = [NOW]
        post = Post(OSError("timed out"))
        notifier = _notifier(home, post, clock=lambda: clock[0])
        with pytest.raises(NotifyError):
            notifier.notify(_alert())
        clock[0] += push.REST_SECONDS - 1
        with pytest.raises(NotifyError, match="resting after a failure"):
            notifier.notify(_alert())
        assert len(post.calls) == 1
        clock[0] += 2
        notifier.notify(_alert())
        assert len(post.calls) == 2

    def test_a_refused_rate_rests_too(self, home):
        self._subscribe(home, _pair(home), Browser())
        clock = [NOW]
        post = Post(429)
        notifier = _notifier(home, post, clock=lambda: clock[0])
        with pytest.raises(NotifyError, match="429"):
            notifier.notify(_alert())
        with pytest.raises(NotifyError, match="resting"):
            notifier.notify(_alert())
        assert len(post.calls) == 1

    def test_no_key_is_nobody_to_notify(self, home):
        device = _pair(home)
        book = push.PushBook(home, clock=lambda: NOW)
        book.subscribe(device, push.check_subscription(Browser().subscription()), ORIGIN)
        with pytest.raises(NobodyToNotify):
            _notifier(home, Post()).notify(_alert())


class TestFanOut:
    def test_every_channel_gets_it_and_one_failing_does_not_stop_another(self):
        mac, phone = RecordingNotifier(), RecordingNotifier()
        mac.fail = True
        logged: list[str] = []
        fan = FanOutNotifier([("mac", mac), ("push", phone)], log=logged.append)
        fan.notify(_alert())
        assert phone.sent == [_alert()]
        assert fan.last == [
            Outcome("mac", "", False, "the recording notifier was told to fail"),
            Outcome("push", "", True, "sent")]
        assert logged == ["mac could not notify: the recording notifier was told to fail"]

    def test_every_channel_failing_raises(self):
        mac, phone = RecordingNotifier(), RecordingNotifier()
        mac.fail = phone.fail = True
        with pytest.raises(NotifyError, match="mac: the recording notifier"):
            FanOutNotifier([("mac", mac), ("push", phone)]).notify(_alert())

    def test_a_channel_with_nobody_is_neither_a_delivery_nor_a_failure(self):
        class Nobody:
            def notify(self, notification):
                raise NobodyToNotify("no device has turned push on")

        mac = RecordingNotifier()
        fan = FanOutNotifier([("mac", mac), ("push", Nobody())])
        fan.notify(_alert())
        assert fan.last == [Outcome("mac", "", True, "sent"),
                            Outcome("push", "", None, "no device has turned push on")]
        with pytest.raises(NobodyToNotify):
            FanOutNotifier([("push", Nobody())]).notify(_alert())

    def test_a_channels_own_outcomes_are_carried_and_its_failures_logged(self, home):
        TestTheNotifier()._subscribe(home, _pair(home, "iPhone"), Browser(APPLE))
        TestTheNotifier()._subscribe(home, _pair(home, "iPad"), Browser(GOOGLE))
        logged: list[str] = []
        mac = RecordingNotifier()
        fan = FanOutNotifier([("mac", mac), ("push", _notifier(home, Post(201, 503)))],
                             log=logged.append)
        fan.notify(_alert())
        assert fan.last == [Outcome("mac", "", True, "sent"),
                            Outcome("push", "iPhone", True, "sent"),
                            Outcome("push", "iPad", False, "the push service answered 503")]
        assert logged == ["push to iPad could not notify: the push service answered 503"]

    def test_the_mac_banner_goes_first(self):
        order: list[str] = []

        class Channel:
            def __init__(self, name):
                self.name = name

            def notify(self, notification):
                order.append(self.name)

        FanOutNotifier([("mac", Channel("mac")), ("push", Channel("push"))]).notify(_alert())
        assert order == ["mac", "push"]


class TestWithoutCryptography:
    def test_push_says_why_it_is_not_available(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "cryptography", None)
        assert "cryptography" in push.unavailable()
        assert "invariant[oracle]" in push.unavailable()

    def test_with_it_push_is_available(self):
        assert push.unavailable() is None


class TestTheRealPost:
    def test_it_posts_once_and_answers_the_status(self, monkeypatch):
        seen = {}

        class Answer:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(request, timeout):
            seen.update(url=request.full_url, method=request.get_method(),
                        body=request.data, timeout=timeout,
                        encoding=request.get_header("Content-encoding"))
            return Answer()

        monkeypatch.setattr(push.urllib.request, "urlopen", urlopen)
        status = push.urllib_post(APPLE, b"x", {"Content-Encoding": "aes128gcm"}, 5.0)
        assert status == 201
        assert seen == {"url": APPLE, "method": "POST", "body": b"x", "timeout": 5.0,
                        "encoding": "aes128gcm"}

    def test_an_http_error_is_its_status(self, monkeypatch):
        import urllib.error

        def urlopen(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 410, "Gone", {}, None)

        monkeypatch.setattr(push.urllib.request, "urlopen", urlopen)
        assert push.urllib_post(APPLE, b"x", {}, 5.0) == 410


class TestTheEdges:
    def test_text_that_is_not_base64url_is_a_value_error(self):
        with pytest.raises(ValueError):
            push.unb64url("a")
        with pytest.raises(TypeError):
            push.unb64url(None)

    def test_a_port_that_is_not_a_number_is_refused(self):
        sub = Browser().subscription()
        sub["endpoint"] = "https://web.push.apple.com:99999/x"
        with pytest.raises(push.PushError):
            push.check_subscription(sub)

    def test_an_auth_secret_that_is_not_base64url_is_refused(self):
        sub = Browser().subscription()
        sub["keys"]["auth"] = "a"
        with pytest.raises(push.PushError, match="not base64url"):
            push.check_subscription(sub)

    def test_a_stored_subscription_that_cannot_be_used_is_that_devices_failure(self, home):
        device = _pair(home)
        book = push.PushBook(home, clock=lambda: NOW)
        book.ensure_key()
        book.subscribe(device, {"endpoint": APPLE, "p256dh": _b64(b"\x04" + b"\x01" * 64),
                                "auth": _b64(b"a" * 16)}, ORIGIN)
        post = Post()
        with pytest.raises(NotifyError, match="its subscription cannot be used"):
            _notifier(home, post).notify(_alert())
        assert post.calls == []
        assert "cannot be used" in book.for_device(device)["error"]

    def test_a_refusal_that_is_not_the_services_load_does_not_rest(self, home):
        TestTheNotifier()._subscribe(home, _pair(home), Browser())
        post = Post(413, 201)
        notifier = _notifier(home, post)
        with pytest.raises(NotifyError, match="413"):
            notifier.notify(_alert())
        notifier.notify(_alert())
        assert len(post.calls) == 2

    def test_a_book_that_cannot_be_written_after_sending_still_counts_the_send(
            self, home, monkeypatch):
        TestTheNotifier()._subscribe(home, _pair(home), Browser())
        notifier = _notifier(home, Post())

        def broken(deliveries):
            raise StateError("the disk is full")

        monkeypatch.setattr(notifier.book, "record", broken)
        notifier.notify(_alert())
        assert notifier.last[0].ok is True
