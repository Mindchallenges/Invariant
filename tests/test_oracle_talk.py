"""The host-neutral half of talking to a session: the last message and the conversation.

``oracle/talk`` names what it reads (``SessionTalk``, its conversation's
shape) and decides what of it is a session's last message. It imports nothing
from ``hosts/``: the values here are plain stand-ins with the same shape, as
Claude Code's reader returns (ADR-029 D4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from invariant.core.errors import StateError
from invariant.oracle import talk
from invariant.oracle.devices import Device
from invariant.oracle.talk import (
    LastMessage,
    RecordedLook,
    RecordingTalk,
    Talker,
    TalkHost,
    Turns,
    fingerprint,
    last_message,
    read_turns,
)


@dataclass(frozen=True)
class Entry:
    role: str
    text: str
    at: float = 0.0
    cut: bool = False


@dataclass(frozen=True)
class Unreadable:
    reason: str = "an entry that is not JSON"
    version: str | None = None


@dataclass(frozen=True)
class Conversation:
    entries: tuple
    unreadable: Unreadable | None = None


def said(*pairs, unreadable=None) -> Conversation:
    return Conversation(tuple(Entry(role, text) for role, text in pairs), unreadable)


class TestTheLastMessage:
    def test_claudes_newest_text_with_the_prompt_it_answers(self):
        read = said(("user", "first"), ("assistant", "one"), ("user", "fix it"),
                    ("assistant", "working on it"), ("assistant", "fixed"))
        assert last_message(read) == LastMessage(claude="fixed", you="fix it")

    def test_a_prompt_newer_than_claudes_text_is_not_its_context(self):
        read = said(("user", "earlier"), ("assistant", "done"), ("user", "now this"))
        assert last_message(read) == LastMessage(claude="done", you="earlier")

    def test_no_prompt_in_the_read_shows_claude_alone(self):
        assert last_message(said(("assistant", "hello"))) == LastMessage("hello", None)

    def test_nothing_from_claude_is_no_message(self):
        assert last_message(said(("user", "only me"))) is None
        assert last_message(said()) is None
        assert last_message(said(("assistant", ""))) is None

    def test_an_unreadable_read_with_nothing_newer_is_no_message(self):
        assert last_message(said(unreadable=Unreadable())) is None

    def test_what_was_read_before_an_unreadable_entry_stands(self):
        read = said(("assistant", "newest"), unreadable=Unreadable())
        assert last_message(read) == LastMessage("newest", None)

    def test_the_cap_holds_whatever_the_host_returned(self):
        read = said(("user", "y" * (talk.ENTRY_TEXT_CAP + 5)),
                    ("assistant", "x" * (talk.ENTRY_TEXT_CAP + 5)))
        last = last_message(read)
        assert len(last.claude) == talk.ENTRY_TEXT_CAP
        assert len(last.you) == talk.ENTRY_TEXT_CAP

    def test_an_entry_of_another_role_or_no_text_is_passed_over(self):
        read = said(("user", "ask"), ("assistant", "answer"), ("tool", "ran ls"),
                    ("assistant", ""))
        assert last_message(read) == LastMessage("answer", "ask")

    def test_it_is_a_plain_dict_for_the_view(self):
        assert LastMessage("c", "y").to_dict() == {"claude": "c", "you": "y"}


def entries(*triples, unreadable=None) -> Conversation:
    return Conversation(tuple(Entry(role, text, at, cut) for role, text, at, cut in triples),
                        unreadable)


class TestTheConversation:
    """What the Conversation screen is served (ADR-029 increment 2, D19, D21)."""

    def test_the_turns_oldest_first_as_plain_values(self):
        read = talk.turns(entries(("user", "fix it", 1.0, False), ("tool", "Ran ls", 2.0, False),
                                  ("assistant", "<b>done</b>", 3.0, True)))
        assert read == Turns((
            {"role": "user", "text": "fix it", "at": 1.0, "cut": False},
            {"role": "tool", "text": "Ran ls", "at": 2.0, "cut": False},
            {"role": "assistant", "text": "<b>done</b>", "at": 3.0, "cut": True}), False)
        assert read.to_dict() == {"turns": list(read.turns), "unreadable": False}

    def test_only_the_newest_forty(self):
        many = [("assistant", f"m{n}", float(n), False) for n in range(45)]
        read = talk.turns(entries(*many))
        assert [t["text"] for t in read.turns] == [f"m{n}" for n in range(5, 45)]
        assert talk.CONVERSATION_ENTRIES == 40

    def test_a_stop_among_the_shown_turns_says_so(self):
        read = talk.turns(entries(("assistant", "newer", 1.0, False), unreadable=Unreadable()))
        assert read.unreadable is True and [t["text"] for t in read.turns] == ["newer"]
        assert talk.turns(entries(unreadable=Unreadable())) == Turns((), True)

    def test_a_stop_older_than_the_newest_forty_is_not_shown(self):
        many = [("assistant", f"m{n}", float(n), False) for n in range(41)]
        assert talk.turns(entries(*many, unreadable=Unreadable())).unreadable is False

    def test_the_caps_hold_whatever_the_host_returned(self):
        read = talk.turns(entries(("assistant", "x" * (talk.ENTRY_TEXT_CAP + 1), 1.0, False),
                                  ("tool", "y" * 300, 2.0, False)))
        claude, tool = read.turns
        assert len(claude["text"]) == talk.ENTRY_TEXT_CAP and claude["cut"] is True
        assert len(tool["text"]) == talk.TOOL_LINE_CAP == 160 and tool["cut"] is False

    def test_a_role_it_does_not_know_stops_there_and_keeps_what_is_newer(self):
        read = talk.turns(entries(("assistant", "older", 1.0, False),
                                  ("narrator", "?", 2.0, False),
                                  ("user", "newer", 3.0, False)))
        assert read == Turns(({"role": "user", "text": "newer", "at": 3.0, "cut": False},),
                             True)

    def test_a_read_that_raises_is_unreadable(self):
        class Broken:
            def conversation(self, host):
                raise OSError("gone")

        assert read_turns(Broken(), TalkHost("h")) == Turns((), True)

    def test_a_read_through_the_seam(self):
        class Reader:
            def conversation(self, host):
                return entries(("assistant", host.host_id, 1.0, False))

        assert [t["text"] for t in read_turns(Reader(), TalkHost("h1")).turns] == ["h1"]

    def test_the_cursor_names_what_was_served(self):
        one = {"live": True, "turns": [{"text": "a"}], "unreadable": False}
        assert talk.cursor(one) == talk.cursor(dict(one))
        assert talk.cursor(one) != talk.cursor({**one, "turns": [{"text": "b"}]})
        assert talk.cursor(one) != talk.cursor({**one, "unreadable": True})
        assert re.fullmatch(r"[0-9a-f]{24}", talk.cursor(one))

    @pytest.mark.parametrize("live, unreadable, why", [
        (True, False, "reading_only"), (True, True, "unreadable"), (False, False, "gone")])
    def test_nothing_is_offered_yet_and_it_says_why(self, live, unreadable, why):
        assert talk.offered(live, unreadable) == {
            "send": False, "answer": False, "interrupt": False, "why": why}


def test_a_talk_host_names_the_window_by_its_host_id():
    assert TalkHost("abc").host_id == "abc"


def test_the_talk_module_imports_no_host():
    source = Path(talk.__file__).read_text()
    imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", source, re.MULTILINE)
    assert not [i for i in imports if "hosts" in i]


# -- sending a message (ADR-029 increment 3) -----------------------------------------

HOST = "2abd173c-1111-2222-3333-444455556666"
PHONE = Device("d1", "phone", "2026-11-20")
T0 = 1_792_640_000.0


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _host(turn="ended", waiting=None, tty="ttys004", pid=4242, start="Thu Nov 20 10:00:00 2026",
          status=None, source="both", alive=True, host=HOST):
    attention = {"turn": turn, "turn_since": "2026-11-20T10:00:00+00:00"}
    if waiting:
        attention["waiting"] = {"kind": waiting, "since": "2026-11-20T10:01:00+00:00"}
    return {"host_session_id": host, "current": True, "alive": alive, "pid": pid,
            "process_start": start, "tty": tty, "attention": attention, "status": status,
            "pid_source": source}


def _row(**host):
    return {"kind": "session", "project": "K", "session_id": "s1", "name": "ship-s1",
            "hosts": [_host(**host)], "signals": []}


def _document(row):
    return {"now": T0, "projects": [{"project": {"key": "K", "name": "Heap"},
                                     "rows": [row]}]}


class World:
    """A clock that sleeping moves, a host that records, and the sidecar's record."""

    def __init__(self, **host):
        self.now = 1000.0
        self.wall = T0
        self.row = _row(**host)
        self.record: dict = {"turn": "ended", "turn_since": "2026-11-20T10:00:00+00:00"}
        self.talk = RecordingTalk()
        self.lines: list[dict] = []
        self.alerts: list[tuple[str, str]] = []
        self.locks: list[str] = []
        self.revoked: set[str] = set()
        self.loops: list = []
        self.audit_fails = False
        self.on_type = None
        self.lock_fails = False

    def sleep(self, seconds):
        self.now += seconds
        self.wall += seconds

    def audit(self, line):
        if self.audit_fails:
            raise StateError("disk full")
        self.lines.append(line)

    def attention(self, project, host):
        assert project == "K"
        return dict(self.record)

    def typist(self, tty):
        world = self

        class Lock:
            def __enter__(self):
                if world.lock_fails:
                    raise StateError("held")
                world.locks.append(tty)

            def __exit__(self, *exc):
                return False

        return Lock()

    def talker(self, **kw):
        talker = Talker(self.talk, lambda: _document(self.row), self.audit,
                        attention=self.attention, typist=self.typist,
                        alert=lambda project, title: self.alerts.append((project, title)),
                        paired=lambda device: device not in self.revoked,
                        clock=lambda: self.wall, timer=lambda: self.now, sleep=self.sleep,
                        spawn=lambda work: work(), background=self.loops.append, **kw)
        return talker

    def receipt(self, text, at=None):
        from invariant.oracle.talk import text_hash

        self.record["received"] = {"kind": "message", "hash": text_hash(text.strip()),
                                   "at": _iso(at if at is not None else self.wall)}


def _nonce(talker, world, device=PHONE):
    return talker.issue(device.id, "K|s1", fingerprint(world.row), "K", "Heap",
                        "Ship it")


def _post(talker, world, text="Ship it now", **kw):
    return talker.post(PHONE, "K|s1", _nonce(talker, world), "message", text, **kw)


def _events(world):
    return [line["event"] for line in world.lines]


class TestTheFingerprint:
    def test_it_names_the_window_its_process_terminal_and_turn(self):
        fp = fingerprint(_row())
        assert (fp.host, fp.pid, fp.tty, fp.waiting, fp.since, fp.ready) == (
            HOST, 4242, "ttys004", "turn:ended", "2026-11-20T10:00:00+00:00", True)

    def test_a_waiting_prompt_is_its_waiting_identity_and_not_ready(self):
        fp = fingerprint(_row(waiting="permission"))
        assert (fp.waiting, fp.since, fp.ready) == (
            "permission", "2026-11-20T10:01:00+00:00", False)

    def test_an_idle_prompt_is_ready_even_with_a_running_turn(self):
        assert fingerprint(_row(turn="running", waiting="idle")).ready is True

    def test_a_running_turn_is_not_ready(self):
        assert fingerprint(_row(turn="running")).ready is False

    @pytest.mark.parametrize(("status", "ready"), [("idle", True), ("shell", True),
                                                   ("busy", False), ("waiting", False)])
    def test_claude_codes_own_status_outranks_the_record(self, status, ready):
        assert fingerprint(_row(turn="running", status=status)).ready is ready
        assert fingerprint(_row(turn="ended", status=status)).ready is ready

    def test_a_status_the_record_did_not_vouch_for_is_not_used(self):
        assert fingerprint(_row(turn="running", status="idle",
                                source="attention")).ready is False

    def test_a_record_that_says_nothing_is_not_ready(self):
        row = _row()
        row["hosts"][0]["attention"] = None
        assert fingerprint(row).ready is False

    def test_no_live_window_is_no_fingerprint(self):
        assert fingerprint(_row(alive=None)) is None
        assert fingerprint({"hosts": []}) is None
        assert fingerprint(None) is None

    def test_identity_names_what_changed(self):
        old = fingerprint(_row())
        assert talk.identity_change(old, fingerprint(_row())) is None
        assert talk.identity_change(old, fingerprint(_row(host="other"))) == "window"
        assert talk.identity_change(old, fingerprint(_row(pid=5))) == "process"
        assert talk.identity_change(old, fingerprint(_row(start="later"))) == "process"
        assert talk.identity_change(old, fingerprint(_row(tty="ttys009"))) == "terminal"


class TestATap:
    def test_it_is_held_for_five_seconds_and_audited_without_the_text(self):
        world = World()
        talker = world.talker()
        reply = _post(talker, world, "The secret is hunter2")
        assert reply.status == 202
        assert reply.body["send"]["state"] == "held" and reply.body["boot"] == talker.boot
        assert "hunter2" not in repr(reply.body)
        assert _events(world) == ["talk-held"]
        line = world.lines[0]
        assert set(line) == {"event", "send", "device", "device_name", "row", "host", "kind",
                             "length", "hash"}
        assert line["length"] == len("The secret is hunter2")
        assert line["hash"] == talk.text_hash("The secret is hunter2")
        assert "hunter2" not in repr(world.lines)
        assert len(world.loops) == 1
        world.sleep(4.9)
        talker.tick()
        assert world.talk.typed == []

    def test_the_loop_starts_once(self):
        world = World()
        talker = world.talker()
        _post(talker, world)
        _post(talker, world)
        assert len(world.loops) == 1

    @pytest.mark.parametrize(("kind", "text", "status", "reason"), [
        ("answer", "x", 400, "kind"),
        ("interrupt", "x", 400, "text"),
        ("message", "   ", 400, "empty"),
        ("message", None, 400, "empty"),
        ("message", "x" * 8001, 400, "long"),
        ("message", "stop\x1b", 400, "control"),
        ("message", "bye\x03", 400, "control"),
        ("message", "tab\there", 400, "control"),
    ])
    def test_what_is_not_a_message_is_refused_and_nothing_held(self, kind, text, status,
                                                                 reason):
        world = World()
        talker = world.talker()
        reply = talker.post(PHONE, "K|s1", _nonce(talker, world), kind, text)
        assert (reply.status, reply.body["reason"]) == (status, reason)
        assert world.lines == [] and talker.sends(PHONE.id, "K|s1") == []

    def test_a_message_of_8000_characters_with_line_breaks_is_taken(self):
        world = World()
        talker = world.talker()
        assert _post(talker, world, "a\n" * 4000).status == 202

    def test_the_nonce_is_spent_by_the_first_send(self):
        world = World()
        talker = world.talker()
        nonce = _nonce(talker, world)
        assert talker.post(PHONE, "K|s1", nonce, "message", "one").status == 202
        again = talker.post(PHONE, "K|s1", nonce, "message", "two")
        assert (again.status, again.body["reason"]) == (403, "nonce")

    def test_a_newer_read_replaces_the_nonce(self):
        world = World()
        talker = world.talker()
        first = _nonce(talker, world)
        _nonce(talker, world)
        assert talker.post(PHONE, "K|s1", first, "message", "x").status == 403

    def test_a_nonce_is_bound_to_its_device_and_row(self):
        world = World()
        talker = world.talker()
        nonce = _nonce(talker, world)
        other = Device("d2", "laptop", "2026-11-20")
        assert talker.post(other, "K|s1", nonce, "message", "x").status == 403
        assert talker.post(PHONE, "K|s2", nonce, "message", "x").status == 403

    def test_a_nonce_lasts_two_minutes(self):
        world = World()
        talker = world.talker()
        nonce = _nonce(talker, world)
        world.sleep(121)
        reply = talker.post(PHONE, "K|s1", nonce, "message", "x")
        assert reply.status == 403 and "expired" in reply.body["error"]

    def test_an_expired_nonce_is_forgotten_when_the_next_is_issued(self):
        world = World()
        talker = world.talker()
        talker.issue("d9", "K|s1", fingerprint(world.row), "K", "Heap", "x")
        world.sleep(121)
        _nonce(talker, world)
        assert ("d9", "K|s1") not in talker._nonces

    @pytest.mark.parametrize(("text", "reason"), [("/model", "menu_command"),
                                                  ("  !rm -rf /", "input_mode")])
    def test_a_message_the_host_refuses_is_refused_with_its_reason(self, text, reason):
        world = World()
        world.talk.refusal = reason
        talker = world.talker()
        reply = _post(talker, world, text)
        assert (reply.status, reply.body["reason"]) == (409, reason)
        assert _events(world) == ["talk-refused"] and world.lines[0]["reason"] == reason
        assert talker.sends(PHONE.id, "K|s1") == []

    def test_a_host_that_cannot_say_refuses(self):
        world = World()
        world.talk.refuses = lambda kind, text: 1 / 0
        talker = world.talker()
        assert _post(talker, world).body["reason"] == "input_mode"

    def test_a_window_with_no_terminal_is_refused(self):
        world = World(tty=None)
        talker = world.talker()
        reply = _post(talker, world)
        assert (reply.status, reply.body["reason"]) == (409, "no_tab")

    def test_a_held_line_that_cannot_be_written_refuses_the_send(self):
        world = World()
        world.audit_fails = True
        talker = world.talker()
        reply = _post(talker, world)
        assert reply.status == 503 and "audit" in reply.body["error"]
        assert talker.sends(PHONE.id, "K|s1") == [] and world.loops == []


class TestUndo:
    def test_undo_in_the_window_types_nothing(self):
        world = World()
        talker = world.talker()
        send = _post(talker, world).body["send"]
        reply = talker.cancel(PHONE, send["id"])
        assert reply.status == 200 and reply.body["send"]["state"] == "cancelled"
        world.sleep(6)
        talker.tick()
        assert world.talk.typed == []
        assert _events(world) == ["talk-held", "talk-cancelled"]
        assert talker.sends(PHONE.id, "K|s1") == []

    def test_only_the_device_that_sent_it_can_undo_it(self):
        world = World()
        talker = world.talker()
        send = _post(talker, world).body["send"]
        other = Device("d2", "laptop", "2026-11-20")
        assert talker.cancel(other, send["id"]).status == 404
        assert talker.cancel(PHONE, "nope").status == 404
        assert talker.cancel(PHONE, None).status == 404

    def test_a_typed_send_cannot_be_undone(self):
        world = World()
        talker = world.talker()
        send = _post(talker, world).body["send"]
        world.on_type = None
        world.sleep(5)
        talker.tick()
        reply = talker.cancel(PHONE, send["id"])
        assert reply.status == 409


def _deliver(world, talker, text="Ship it now", receipt=True):
    """Post, let the window end, and tick once: quiet, typing and receipt run inline."""
    send = _post(talker, world, text).body["send"]
    world.sleep(5)
    if receipt:
        original = world.talk.deliver

        def deliver(host, typed):
            answer = original(host, typed)
            world.receipt(typed)
            return answer

        world.talk.deliver = deliver
    talker.tick()
    return next(s for s in talker.sends(PHONE.id, "K|s1") if s["id"] == send["id"])


class TestDelivery:
    def test_a_quiet_window_is_typed_into_once_and_delivered(self):
        world = World()
        talker = world.talker()
        send = _deliver(world, talker, "Ship it now")
        assert send["state"] == "delivered"
        assert world.talk.typed == [(HOST, "ttys004", "Ship it now")]
        assert world.locks == ["ttys004"]
        assert _events(world) == ["talk-held", "talk-typed", "talk-delivered"]
        assert world.alerts == []
        talker.tick()
        assert len(world.talk.typed) == 1

    def test_it_waits_three_seconds_of_quiet_first(self):
        world = World()
        talker = world.talker()
        world.talk.looks = [RecordedLook("a"), RecordedLook("b"), RecordedLook("b"),
                            RecordedLook("b", empty=False), *[RecordedLook("c")] * 7]
        began = world.now + 5
        _deliver(world, talker)
        assert world.talk.typed
        assert world.now - began >= 3.0 + 2.0

    def test_busy_for_a_minute_is_refused_and_nothing_typed(self):
        world = World()
        talker = world.talker()
        world.talk.looks = [RecordedLook(str(n)) for n in range(200)]
        send = _deliver(world, talker)
        assert (send["state"], send["reason"]) == ("refused", "typing")
        assert world.talk.typed == []
        assert _events(world)[-1] == "talk-refused"

    def test_text_left_on_the_input_line_is_busy(self):
        world = World()
        talker = world.talker()
        world.talk.looks = [RecordedLook("same", empty=False)] * 200
        assert _deliver(world, talker)["reason"] == "typing"

    def test_an_unreadable_tab_refuses_and_turns_send_off(self):
        world = World()
        talker = world.talker()
        world.talk.looks = [Unreadable("no input box")]
        send = _deliver(world, talker)
        assert (send["state"], send["reason"]) == ("refused", "unreadable")
        assert talker.offered(fingerprint(world.row), True, False)["why"] == "unreadable"

    def test_a_look_that_fails_is_unreadable(self):
        world = World()
        world.talk.quiet = lambda host: 1 / 0
        talker = world.talker()
        assert _deliver(world, talker)["reason"] == "unreadable"

    @pytest.mark.parametrize("refused", ["no_tab", "no_claude"])
    def test_a_tab_without_claude_refuses_and_says_so_after(self, refused):
        world = World()
        talker = world.talker()
        world.talk.looks = [RecordedLook(refused=refused)]
        assert _deliver(world, talker)["reason"] == refused
        offered = talker.offered(fingerprint(world.row), True, False)
        assert (offered["send"], offered["why"]) == (False, refused)

    def test_a_look_the_mac_did_not_allow_refuses(self):
        world = World()
        talker = world.talker()
        world.talk.looks = [RecordedLook(refused="not_allowed")]
        assert _deliver(world, talker)["reason"] == "not_allowed"

    def test_a_look_refused_another_way_fails(self):
        world = World()
        talker = world.talker()
        world.talk.looks = [RecordedLook(refused="odd")]
        assert _deliver(world, talker)["reason"] == "failed"

    def test_the_typists_lock_held_elsewhere_refuses(self):
        world = World()
        world.lock_fails = True
        talker = world.talker()
        assert _deliver(world, talker)["reason"] == "typing"
        assert world.talk.typed == []

    def test_without_a_typist_nothing_is_typed(self):
        world = World()
        talker = world.talker()
        talker.typist = None
        assert _deliver(world, talker)["reason"] == "no_tab"

    def test_a_new_turn_since_the_look_refuses(self):
        world = World()
        talker = world.talker()
        send = _post(talker, world).body["send"]
        world.row = _row(turn="ended")
        world.row["hosts"][0]["attention"]["turn_since"] = "2026-11-20T10:05:00+00:00"
        world.sleep(5)
        talker.tick()
        got = talker.sends(PHONE.id, "K|s1")[0]
        assert (got["id"], got["state"], got["reason"], got["what"]) == (
            send["id"], "refused", "changed", "turn")
        assert world.talk.typed == []

    @pytest.mark.parametrize(("change", "what"), [({"pid": 99}, "process"),
                                                  ({"tty": "ttys009"}, "terminal"),
                                                  ({"host": "other"}, "window")])
    def test_another_window_since_the_look_refuses(self, change, what):
        world = World()
        talker = world.talker()
        _post(talker, world)
        world.row = _row(**change)
        world.sleep(5)
        talker.tick()
        got = talker.sends(PHONE.id, "K|s1")[0]
        assert (got["reason"], got["what"]) == ("changed", what)

    def test_a_window_gone_since_the_look_refuses(self):
        world = World()
        talker = world.talker()
        _post(talker, world)
        world.row = _row(alive=False)
        world.sleep(5)
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["reason"] == "gone"

    def test_a_machine_that_cannot_be_read_refuses(self):
        world = World()
        talker = world.talker()
        _post(talker, world)
        talker.fresh = lambda: 1 / 0
        world.sleep(5)
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["reason"] == "failed"

    @pytest.mark.parametrize(("code", "reason"), [("no_claude", "no_claude"),
                                                  ("no_tab", "no_tab"),
                                                  ("not_running", "no_tab"),
                                                  ("not_allowed", "not_allowed"),
                                                  ("odd", "failed")])
    def test_a_typer_that_refuses_types_nothing_and_says_why(self, code, reason):
        world = World()
        world.talk.code = code
        talker = world.talker()
        send = _deliver(world, talker, receipt=False)
        assert (send["state"], send["reason"]) == ("refused", reason)

    def test_a_typer_that_fails_may_have_typed_so_the_receipt_decides(self):
        world = World()
        world.talk.code = "failed"
        talker = world.talker()
        assert _deliver(world, talker)["state"] == "delivered"
        assert world.lines[1]["reason"] == "failed"

    def test_a_typer_that_raises_is_left_to_the_receipt(self):
        world = World()
        talker = world.talker()

        def boom(host, text):
            raise OSError("osascript went away")

        world.talk.deliver = boom
        send = _deliver(world, talker, receipt=False)
        assert send["state"] == "not_confirmed"

    def test_a_delivery_that_breaks_is_refused_never_retried(self):
        world = World()
        talker = world.talker()
        world.talk.digest = lambda kind, text: 1 / 0
        send = _deliver(world, talker, receipt=False)
        assert (send["state"], send["reason"]) == ("refused", "failed")


class TestTheReceipt:
    def test_no_receipt_in_five_seconds_is_not_confirmed_and_one_alert(self):
        world = World()
        talker = world.talker()
        send = _deliver(world, talker, "The secret is hunter2", receipt=False)
        assert (send["state"], send["reason"]) == ("not_confirmed", "no_receipt")
        assert world.alerts == [("Heap", "Ship it")]
        assert _events(world) == ["talk-held", "talk-typed", "talk-not-confirmed"]
        assert "hunter2" not in repr(world.alerts) + repr(world.lines)
        talker.tick()
        assert len(world.talk.typed) == 1

    def test_a_receipt_carrying_another_hash_is_not_confirmed(self):
        world = World()
        talker = world.talker()
        original = world.talk.deliver

        def deliver(host, typed):
            world.receipt("something else typed at the Mac")
            return original(host, typed)

        world.talk.deliver = deliver
        send = _deliver(world, talker, receipt=False)
        assert (send["state"], send["reason"]) == ("not_confirmed", "other_hash")

    def test_the_receipt_before_the_typing_does_not_count(self):
        world = World()
        world.receipt("Ship it now", at=T0 - 600)
        talker = world.talker()
        assert _deliver(world, talker, receipt=False)["state"] == "not_confirmed"

    def test_an_alert_that_fails_still_says_not_confirmed(self):
        world = World()
        talker = world.talker()
        talker.alert = lambda project, title: 1 / 0
        assert _deliver(world, talker, receipt=False)["state"] == "not_confirmed"


class TestTheRing:
    def test_a_send_whose_turn_runs_is_queued_and_goes_when_it_ends(self):
        world = World(turn="running")
        world.record = {"turn": "running", "turn_since": "2026-11-20T10:00:00+00:00"}
        talker = world.talker()
        send = _post(talker, world).body["send"]
        world.sleep(5)
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["state"] == "queued"
        assert _events(world) == ["talk-held", "talk-queued"]
        talker.tick()
        assert world.talk.typed == []
        world.record = {"turn": "ended", "turn_since": "2026-11-20T10:09:00+00:00"}
        world.row = _row(turn="ended")
        original = world.talk.deliver

        def deliver(host, typed):
            world.receipt(typed)
            return original(host, typed)

        world.talk.deliver = deliver
        talker.tick()
        got = talker.sends(PHONE.id, "K|s1")[0]
        assert (got["id"], got["state"]) == (send["id"], "delivered")

    def test_a_queued_send_is_cancellable_until_it_is_due(self):
        world = World(turn="running")
        world.record = {"turn": "running"}
        talker = world.talker()
        send = _post(talker, world).body["send"]
        world.sleep(5)
        talker.tick()
        assert talker.cancel(PHONE, send["id"]).status == 200
        world.record = {"turn": "ended"}
        talker.tick()
        assert world.talk.typed == []

    def test_one_queued_send_per_turn_end(self):
        world = World(turn="running")
        world.record = {"turn": "running", "turn_since": "t0"}
        talker = world.talker()
        _post(talker, world, "first")
        _post(talker, world, "second")
        world.sleep(5)
        talker.tick()
        world.record = {"turn": "ended", "turn_since": "t1"}
        world.row = _row(turn="ended")
        talker.tick()
        assert [t for _, _, t in world.talk.typed] == ["first"]
        talker.tick()
        assert [t for _, _, t in world.talk.typed] == ["first"]
        world.record = {"turn": "ended", "turn_since": "t2"}
        talker.tick()
        assert [t for _, _, t in world.talk.typed] == ["first", "second"]

    def test_a_queued_send_the_fresh_scan_finds_busy_waits_again(self):
        world = World(turn="running")
        world.record = {"turn": "running"}
        talker = world.talker()
        _post(talker, world)
        world.sleep(5)
        talker.tick()
        world.record = {"turn": "ended"}
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["state"] == "queued"
        assert _events(world).count("talk-queued") == 1
        talker.tick()
        assert world.talk.typed == []

    def test_a_held_send_on_a_busy_window_waits_behind_the_queue(self):
        world = World()
        talker = world.talker()
        talker._inflight.add(HOST)
        _post(talker, world)
        world.sleep(5)
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["state"] == "queued"

    def test_a_ninth_queued_send_drops_the_oldest(self):
        world = World(turn="running")
        world.record = {"turn": "running"}
        talker = world.talker()
        ids = []
        for n in range(8):
            ids.append(_post(talker, world, f"m{n}").body["send"]["id"])
            world.sleep(5)
            talker.tick()
        _post(talker, world, "m8")
        world.sleep(5)
        talker.tick()
        states = {s["id"]: (s["state"], s["reason"]) for s in talker.sends(PHONE.id, "K|s1")}
        assert states[ids[0]] == ("dropped", "ring")
        assert sum(1 for state, _ in states.values() if state == "queued") == 8
        assert "talk-dropped" in _events(world)

    def test_the_server_keeps_at_most_64_waiting(self, monkeypatch):
        monkeypatch.setattr(talk, "RING_TOTAL", 3)
        world = World(turn="running")
        world.record = {"turn": "running"}
        talker = world.talker()
        rows = []
        for n in range(4):
            world.row = _row(host=f"host{n}", turn="running")
            talker.issue(PHONE.id, "K|s1", fingerprint(world.row), "K", "Heap", "t")
            nonce = talker._nonces[(PHONE.id, "K|s1")].nonce
            rows.append(talker.post(PHONE, "K|s1", nonce, "message", f"m{n}").body["send"]["id"])
            world.sleep(5)
            talker.tick()
        states = {s["id"]: s["state"] for s in talker.sends(PHONE.id, "K|s1")}
        assert states[rows[0]] == "dropped"

    def test_a_tap_past_a_full_ring_drops_the_oldest_queued(self, monkeypatch):
        monkeypatch.setattr(talk, "RING_PER_HOST", 2)
        world = World(turn="running")
        world.record = {"turn": "running"}
        talker = world.talker()
        first = _post(talker, world, "a").body["send"]["id"]
        world.sleep(5)
        talker.tick()
        _post(talker, world, "b")
        assert _post(talker, world, "c").status == 202
        states = {s["id"]: s["state"] for s in talker.sends(PHONE.id, "K|s1")}
        assert states[first] == "dropped"

    def test_a_tap_past_a_ring_of_held_sends_is_refused(self, monkeypatch):
        monkeypatch.setattr(talk, "RING_PER_HOST", 2)
        world = World()
        talker = world.talker()
        _post(talker, world, "a")
        _post(talker, world, "b")
        reply = _post(talker, world, "c")
        assert (reply.status, reply.body["reason"]) == (409, "ring")

    def test_a_revoked_devices_waiting_sends_are_dropped(self):
        world = World(turn="running")
        world.record = {"turn": "running"}
        talker = world.talker()
        _post(talker, world)
        world.revoked.add(PHONE.id)
        talker.tick()
        got = talker.sends(PHONE.id, "K|s1")[0]
        assert (got["state"], got["reason"]) == ("dropped", "revoked")

    def test_a_paired_check_that_fails_keeps_the_send(self):
        world = World()
        talker = world.talker()
        talker.paired = lambda device: 1 / 0
        _post(talker, world)
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["state"] == "held"

    def test_a_finished_send_is_listed_for_ten_minutes(self):
        world = World()
        talker = world.talker()
        _deliver(world, talker)
        world.sleep(599)
        assert talker.sends(PHONE.id, "K|s1")
        world.sleep(2)
        talker._trim()
        assert talker.sends(PHONE.id, "K|s1") == []

    def test_at_most_64_finished_sends_are_kept(self, monkeypatch):
        monkeypatch.setattr(talk, "FINISHED_KEPT", 2)
        world = World()
        talker = world.talker()
        for _ in range(3):
            send = _post(talker, world).body["send"]
            talker.cancel(PHONE, send["id"])
        talker._trim()
        assert len(talker._sends) == 2


class TestTheLoop:
    def test_the_loop_ticks_until_closed(self, monkeypatch):
        world = World()
        talker = world.talker()
        ticks = []
        monkeypatch.setattr(talk, "LOOP_SECONDS", 0.001)

        def tick():
            ticks.append(1)
            if len(ticks) == 2:
                raise RuntimeError("one bad beat")
            if len(ticks) >= 3:
                talker.close()

        talker.tick = tick
        talker._run()
        assert len(ticks) == 3


class TestWhatIsOffered:
    def test_a_ready_window_offers_send(self):
        world = World()
        talker = world.talker()
        assert talker.offered(fingerprint(world.row), True, False) == {
            "send": True, "answer": False, "interrupt": False, "why": None, "running": False}

    def test_a_running_turn_still_offers_send_for_when_it_ends(self):
        world = World(turn="running")
        offered = world.talker().offered(fingerprint(world.row), True, False)
        assert (offered["send"], offered["running"]) == (True, True)

    @pytest.mark.parametrize(("fp", "live", "unreadable", "why"), [
        (None, False, False, "gone"), ("fp", True, True, "unreadable")])
    def test_gone_or_unreadable_offers_nothing(self, fp, live, unreadable, why):
        world = World()
        shown = world.talker().offered(fingerprint(world.row) if fp else None, live,
                                       unreadable)
        assert (shown["send"], shown["why"]) == (False, why)

    def test_a_new_process_forgets_what_the_old_one_answered(self):
        world = World()
        talker = world.talker()
        talker._mark(fingerprint(world.row), "no_claude")
        assert talker.offered(fingerprint(_row(pid=7)), True, False)["send"] is True

    def test_marks_are_bounded(self, monkeypatch):
        monkeypatch.setattr(talk, "FINISHED_KEPT", 2)
        talker = World().talker()
        for n in range(4):
            talker._mark(fingerprint(_row(pid=n)), "no_claude")
        assert len(talker._marks) == 2


class TestTheTypistsLock:
    def test_it_is_a_file_lock_per_terminal_in_the_oracle_home(self, tmp_path):
        with talk.typist(tmp_path, "ttys004"):
            assert (tmp_path / "locks" / "type-ttys004.lock").exists()
            with pytest.raises(StateError), talk.typist(tmp_path, "ttys004", wait=0.05):
                pass
            with talk.typist(tmp_path, "ttys005", wait=0.05):
                pass

    @pytest.mark.parametrize("tty", ["../x", "", None, "ttys004/../../x"])
    def test_only_a_terminal_names_a_lock(self, tmp_path, tty):
        with pytest.raises(StateError), talk.typist(tmp_path, tty):
            pass


class TestReadingTheSidecar:
    def test_it_reads_one_record_and_writes_nothing(self, tmp_path):
        from invariant.core.store import JsonStore
        from invariant.oracle.attention import AttentionStore

        AttentionStore(JsonStore(tmp_path)).prompt(HOST, received="abc")
        before = sorted(p.name for p in tmp_path.rglob("*"))
        record = talk.read_attention(str(tmp_path), HOST)
        assert record["received"]["hash"] == "abc" and record["turn"] == "running"
        assert sorted(p.name for p in tmp_path.rglob("*")) == before

    def test_none_or_unreadable_is_nothing(self, tmp_path):
        assert talk.read_attention(str(tmp_path), HOST) is None
        assert talk.read_attention(str(tmp_path), "../escape") is None


class TestTheRecorder:
    def test_it_types_nothing_and_reads_what_it_is_given(self):
        recorder = RecordingTalk()
        assert recorder.conversation(TalkHost("h")).entries == ()
        assert recorder.quiet(TalkHost("h", "ttys1")) == RecordedLook()
        assert recorder.deliver(TalkHost("h", "ttys1"), "x") == ("typed", "")
        assert recorder.typed == [("h", "ttys1", "x")]
        assert recorder.digest("message", " x ") == talk.text_hash("x")
        assert recorder.refuses("message", "x") is None


class TestEdges:
    def test_a_turn_that_ends_in_the_undo_window_is_typed_then(self):
        world = World(turn="running")
        world.record = {"turn": "running", "turn_since": "t0"}
        talker = world.talker()
        _post(talker, world)
        world.record = {"turn": "ended", "turn_since": "t1"}
        world.row = _row(turn="ended")
        world.sleep(5)
        talker.tick()
        assert [t for _, _, t in world.talk.typed] == ["Ship it now"]

    def test_a_terminal_gone_by_the_fresh_scan_is_no_tab(self):
        world = World(turn="running")
        world.record = {"turn": "ended"}
        talker = world.talker()
        _post(talker, world)
        world.row = _row(tty=None)
        world.row["hosts"][0]["tty"] = None
        talker._sends[next(iter(talker._sends))].fp = fingerprint(world.row)
        world.sleep(5)
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["reason"] == "no_tab"

    def test_a_line_that_cannot_be_written_after_the_hold_costs_the_record_only(self):
        world = World()
        talker = world.talker()
        send = _post(talker, world).body["send"]
        world.audit_fails = True
        assert talker.cancel(PHONE, send["id"]).status == 200

    def test_a_receipt_with_no_readable_time_does_not_count(self):
        world = World()
        talker = world.talker()
        original = world.talk.deliver

        def deliver(host, typed):
            world.record["received"] = {"kind": "message", "hash": "x", "at": "not a time"}
            return original(host, typed)

        world.talk.deliver = deliver
        assert _deliver(world, talker, receipt=False)["reason"] == "no_receipt"
        assert talk._epoch(None) is None

    def test_the_real_loop_and_deliveries_are_daemon_threads(self, monkeypatch):
        import threading

        started = []

        class Thread:
            def __init__(self, target, daemon, name):
                started.append((daemon, name))

            def start(self):
                pass

        monkeypatch.setattr(threading, "Thread", Thread)
        talk._start_thread(lambda: None)
        assert started == [(True, "oracle-talk")]


# -- the interrupt (ADR-029 increment 4) ------------------------------------------------


def _stop(talker, world, device=PHONE, text=None):
    return talker.post(device, "K|s1", _nonce(talker, world, device), "interrupt", text)


def _interrupt(world, talker, evidence="interrupt"):
    """Tap Stop, let the Undo window pass, and tick once; ``evidence`` is what
    the window's transcript shows after the key: the interrupt, another
    prompt, or nothing."""
    send = _stop(talker, world).body["send"]
    world.sleep(5)
    if evidence == "interrupt":
        world.talk.receipts.append(talk.RecordedReceipt(world.talk.digest("interrupt", ""),
                                                        world.wall + 5))
    elif evidence is not None:
        world.talk.receipts.append(talk.RecordedReceipt(talk.text_hash(evidence),
                                                        world.wall + 5))
    talker.tick()
    return next(s for s in talker.sends(PHONE.id, "K|s1") if s["id"] == send["id"])


class TestWhatStopIsOfferedFor:
    def test_a_running_turn_offers_stop(self):
        world = World(turn="running")
        assert world.talker().offered(fingerprint(world.row), True, False)["interrupt"] is True

    def test_a_window_waiting_for_input_has_nothing_to_stop(self):
        world = World()
        assert world.talker().offered(fingerprint(world.row), True, False)["interrupt"] is False

    @pytest.mark.parametrize("waiting", ["permission", "question"])
    def test_a_prompt_open_is_not_stopped_from_here(self, waiting):
        world = World(turn="running", waiting=waiting)
        assert world.talker().offered(fingerprint(world.row), True, False)["interrupt"] is False

    def test_claude_codes_own_waiting_status_is_a_prompt_open(self):
        world = World(turn="running", status="waiting")
        assert fingerprint(world.row).asking is True
        assert world.talker().offered(fingerprint(world.row), True, False)["interrupt"] is False

    def test_stop_stays_when_the_session_cannot_be_read(self):
        world = World(turn="running")
        shown = world.talker().offered(fingerprint(world.row), True, True)
        assert (shown["send"], shown["why"], shown["interrupt"]) == (False, "unreadable", True)

    def test_no_terminal_or_no_claude_in_it_offers_no_stop(self):
        world = World(turn="running", tty=None)
        assert world.talker().offered(fingerprint(world.row), True, False)["interrupt"] is False
        world = World(turn="running")
        talker = world.talker()
        talker._mark(fingerprint(world.row), "no_claude")
        assert talker.offered(fingerprint(world.row), True, False)["interrupt"] is False

    def test_a_tab_whose_box_was_unreadable_keeps_stop(self):
        world = World(turn="running")
        talker = world.talker()
        talker._mark(fingerprint(world.row), "unreadable")
        shown = talker.offered(fingerprint(world.row), True, False)
        assert (shown["send"], shown["interrupt"]) == (False, True)

    def test_a_gone_window_offers_no_stop(self):
        assert World(turn="running").talker().offered(None, False, False)["interrupt"] is False


class TestStopTapped:
    def test_it_is_held_for_its_undo_window_and_audited_with_no_text(self):
        world = World(turn="running")
        talker = world.talker()
        reply = _stop(talker, world)
        assert reply.status == 202
        assert (reply.body["send"]["kind"], reply.body["send"]["state"],
                reply.body["send"]["length"]) == ("interrupt", "held", 0)
        assert _events(world) == ["talk-held"] and world.lines[0]["kind"] == "interrupt"
        world.sleep(4.9)
        talker.tick()
        assert world.talk.keys == []

    def test_undo_cancels_it_and_nothing_is_typed(self):
        world = World(turn="running")
        talker = world.talker()
        send = _stop(talker, world).body["send"]
        assert talker.cancel(PHONE, send["id"]).status == 200
        world.sleep(6)
        talker.tick()
        assert world.talk.keys == [] and _events(world) == ["talk-held", "talk-cancelled"]

    def test_an_interrupt_carries_no_text(self):
        world = World(turn="running")
        reply = _stop(world.talker(), world, text="Stop now")
        assert (reply.status, reply.body["reason"]) == (400, "text")

    def test_a_window_waiting_for_input_is_refused_at_the_tap(self):
        world = World()
        talker = world.talker()
        reply = _stop(talker, world)
        assert (reply.status, reply.body["reason"]) == (409, "ended")
        assert _events(world) == ["talk-refused"]

    def test_a_prompt_open_is_refused_at_the_tap(self):
        world = World(turn="running", waiting="permission")
        reply = _stop(world.talker(), world)
        assert (reply.status, reply.body["reason"]) == (409, "asking")

    def test_an_unknown_kind_is_still_refused(self):
        world = World(turn="running")
        talker = world.talker()
        reply = talker.post(PHONE, "K|s1", _nonce(talker, world), "answer", "1")
        assert reply.status == 400


class TestStopDelivered:
    def test_it_is_typed_at_once_without_waiting_for_quiet_and_delivered(self):
        world = World(turn="running")
        world.record = {"turn": "running", "turn_since": "2026-11-20T10:00:00+00:00"}
        talker = world.talker()
        world.talk.looks = [RecordedLook("a"), RecordedLook("b")]
        began = world.now + 5
        send = _interrupt(world, talker)
        assert send["state"] == "delivered"
        assert world.talk.keys == [(HOST, "ttys004")] and world.talk.typed == []
        assert world.locks == ["ttys004"]
        assert len(world.talk.looks) == 1
        assert world.now - began < 3.0
        assert _events(world) == ["talk-held", "talk-typed", "talk-delivered"]

    def test_it_is_never_queued_behind_a_running_turn(self):
        world = World(turn="running")
        world.record = {"turn": "running", "turn_since": "t0"}
        talker = world.talker()
        assert _interrupt(world, talker)["state"] == "delivered"
        assert "talk-queued" not in _events(world)

    def test_a_turn_that_ended_in_the_undo_window_is_refused(self):
        world = World(turn="running")
        talker = world.talker()
        send = _stop(talker, world).body["send"]
        world.row = _row(turn="ended")
        world.sleep(5)
        talker.tick()
        shown = talker.sends(PHONE.id, "K|s1")[0]
        assert (shown["id"], shown["state"], shown["reason"]) == (send["id"], "refused", "ended")
        assert world.talk.keys == []

    def test_a_prompt_that_opened_in_the_undo_window_is_refused(self):
        world = World(turn="running")
        talker = world.talker()
        _stop(talker, world)
        world.row = _row(turn="running", waiting="permission")
        world.sleep(5)
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["reason"] == "asking"
        assert world.talk.keys == []

    def test_another_turn_by_then_is_refused_as_changed(self):
        world = World(turn="running")
        talker = world.talker()
        _stop(talker, world)
        world.row = _row(turn="running")
        world.row["hosts"][0]["attention"]["turn_since"] = "2026-11-20T10:05:00+00:00"
        world.sleep(5)
        talker.tick()
        shown = talker.sends(PHONE.id, "K|s1")[0]
        assert (shown["reason"], shown["what"]) == ("changed", "turn")

    def test_another_process_is_refused_as_changed(self):
        world = World(turn="running")
        talker = world.talker()
        _stop(talker, world)
        world.row = _row(turn="running", pid=9)
        world.sleep(5)
        talker.tick()
        shown = talker.sends(PHONE.id, "K|s1")[0]
        assert (shown["reason"], shown["what"]) == ("changed", "process")
        assert world.talk.keys == []

    def test_a_gone_window_or_a_failed_scan_is_refused(self):
        world = World(turn="running")
        talker = world.talker()
        _stop(talker, world)
        world.row = _row(turn="running", alive=False)
        world.sleep(5)
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["reason"] == "gone"
        world = World(turn="running")
        talker = world.talker()
        _stop(talker, world)
        talker.fresh = lambda: 1 / 0
        world.sleep(5)
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["reason"] == "failed"

    def test_text_left_on_the_mac_line_is_refused_not_cleared(self):
        world = World(turn="running")
        talker = world.talker()
        world.talk.looks = [RecordedLook("draft", empty=False)]
        send = _interrupt(world, talker, evidence=None)
        assert (send["state"], send["reason"]) == ("refused", "typing")
        assert world.talk.keys == []

    def test_a_tab_with_no_box_it_knows_is_refused_and_stop_stays(self):
        world = World(turn="running")
        talker = world.talker()
        world.talk.looks = [Unreadable("a menu is open")]
        send = _interrupt(world, talker, evidence=None)
        assert (send["state"], send["reason"]) == ("refused", "unreadable")
        assert world.talk.keys == []
        assert talker.offered(fingerprint(world.row), True, False)["interrupt"] is True

    @pytest.mark.parametrize("refused", ["no_tab", "no_claude"])
    def test_a_tab_without_claude_is_refused_and_remembered(self, refused):
        world = World(turn="running")
        talker = world.talker()
        world.talk.looks = [RecordedLook(refused=refused)]
        assert _interrupt(world, talker, evidence=None)["reason"] == refused
        assert talker.offered(fingerprint(world.row), True, False)["interrupt"] is False

    def test_a_look_refused_by_the_mac_is_not_allowed(self):
        world = World(turn="running")
        talker = world.talker()
        world.talk.looks = [RecordedLook(refused="not_allowed")]
        assert _interrupt(world, talker, evidence=None)["reason"] == "not_allowed"

    def test_another_typist_in_the_tab_refuses_it(self):
        world = World(turn="running")
        world.lock_fails = True
        talker = world.talker()
        assert _interrupt(world, talker, evidence=None)["reason"] == "typing"

    @pytest.mark.parametrize(("code", "why"), [("no_claude", "no_claude"),
                                               ("no_tab", "no_tab"),
                                               ("not_allowed", "not_allowed"),
                                               ("unknown_key", "failed")])
    def test_a_key_the_tab_did_not_take_is_refused(self, code, why):
        world = World(turn="running")
        world.talk.code = code
        talker = world.talker()
        assert _interrupt(world, talker, evidence=None)["reason"] == why

    def test_a_typer_that_failed_waits_for_the_receipt_like_a_typed_one(self):
        world = World(turn="running")
        world.talk.interrupt = lambda host: 1 / 0
        talker = world.talker()
        send = _interrupt(world, talker)
        assert send["state"] == "delivered"
        assert world.lines[1]["event"] == "talk-typed" and world.lines[1]["reason"] == "failed"

    def test_a_window_with_a_delivery_under_way_waits_a_beat(self):
        world = World(turn="running")
        talker = world.talker()
        _stop(talker, world)
        talker._inflight.add(HOST)
        world.sleep(5)
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["state"] == "held"
        talker._inflight.clear()
        world.talk.receipts.append(talk.RecordedReceipt(world.talk.digest("interrupt", ""),
                                                        world.wall))
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["state"] == "delivered"


class TestStopsReceipt:
    def test_no_evidence_in_five_seconds_is_not_confirmed_and_one_alert(self):
        world = World(turn="running")
        talker = world.talker()
        send = _interrupt(world, talker, evidence=None)
        assert (send["state"], send["reason"]) == ("not_confirmed", "no_receipt")
        assert world.alerts == [("Heap", "Ship it")]
        assert world.talk.keys == [(HOST, "ttys004")]

    def test_a_prompt_in_its_place_is_not_confirmed(self):
        world = World(turn="running")
        talker = world.talker()
        send = _interrupt(world, talker, evidence="typed at the Mac")
        assert (send["state"], send["reason"]) == ("not_confirmed", "other_hash")

    def test_evidence_from_before_the_key_does_not_count(self):
        world = World(turn="running")
        talker = world.talker()
        send = _stop(talker, world).body["send"]
        world.talk.receipts.append(talk.RecordedReceipt(world.talk.digest("interrupt", ""),
                                                        world.wall - 600))
        world.sleep(5)
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["id"] == send["id"]
        assert talker.sends(PHONE.id, "K|s1")[0]["state"] == "not_confirmed"

    def test_a_receipt_read_that_raises_is_no_evidence(self):
        world = World(turn="running")
        world.talk.receipt = lambda host, kind, since: 1 / 0
        talker = world.talker()
        assert _interrupt(world, talker)["reason"] == "no_receipt"

    def test_the_receipt_is_asked_from_just_before_the_key(self):
        world = World(turn="running")
        asked = []
        original = world.talk.receipt

        def receipt(host, kind, since):
            asked.append((host.host_id, kind, since))
            return original(host, kind, since)

        world.talk.receipt = receipt
        talker = world.talker()
        _interrupt(world, talker)
        assert asked[0][:2] == (HOST, "interrupt")
        assert asked[0][2] <= world.wall


class TestTheRecordersInterrupt:
    def test_it_records_the_key_and_answers_the_receipts_it_is_given(self):
        recorder = RecordingTalk()
        assert recorder.interrupt(TalkHost("h", "ttys1")) == ("typed", "")
        assert recorder.keys == [("h", "ttys1")]
        assert recorder.receipt(TalkHost("h"), "interrupt", 0.0) is None
        given = talk.RecordedReceipt("abc", 5.0)
        recorder.receipts.append(given)
        assert recorder.receipt(TalkHost("h"), "interrupt", 0.0) == given
        assert recorder.digest("interrupt", "") == talk.text_hash("interrupt")


class TestStopEdges:
    def test_no_typist_is_no_tab(self):
        world = World(turn="running")
        talker = world.talker()
        talker.typist = None
        assert _interrupt(world, talker, evidence=None)["reason"] == "no_tab"

    def test_a_look_that_fails_is_unreadable_and_nothing_typed(self):
        world = World(turn="running")
        world.talk.quiet = lambda host: 1 / 0
        talker = world.talker()
        assert _interrupt(world, talker, evidence=None)["reason"] == "unreadable"
        assert world.talk.keys == []


# -- slash commands (ADR-029 increment 4) ------------------------------------------------


class TestASlashCommandsReceipt:
    def test_with_no_hook_the_hosts_evidence_in_the_window_decides(self):
        world = World()
        talker = world.talker()
        world.talk.receipts.append(talk.RecordedReceipt(talk.text_hash("/plan"), world.wall + 30))
        send = _deliver(world, talker, "/plan", receipt=False)
        assert send["state"] == "delivered"

    def test_other_evidence_in_the_window_is_not_confirmed(self):
        world = World()
        talker = world.talker()
        world.talk.receipts.append(talk.RecordedReceipt(talk.text_hash("/other"),
                                                        world.wall + 30))
        send = _deliver(world, talker, "/plan", receipt=False)
        assert (send["state"], send["reason"]) == ("not_confirmed", "other_hash")

    def test_the_hooks_receipt_comes_first(self):
        world = World()
        talker = world.talker()
        world.talk.receipts.append(talk.RecordedReceipt(talk.text_hash("/other"),
                                                        world.wall + 30))
        assert _deliver(world, talker, "/hello there")["state"] == "delivered"

    def test_the_window_is_named_with_its_process_for_the_evidence(self):
        world = World()
        asked = []
        world.talk.receipt = lambda host, kind, since: asked.append(host) or None
        talker = world.talker()
        _deliver(world, talker, "/exit", receipt=False)
        assert asked and (asked[0].host_id, asked[0].tty, asked[0].pid) == (
            HOST, "ttys004", 4242)

    def test_evidence_older_than_the_typing_or_a_failing_read_is_none(self):
        world = World()
        talker = world.talker()
        world.talk.receipts.append(talk.RecordedReceipt(talk.text_hash("/plan"),
                                                        world.wall - 600))
        assert _deliver(world, talker, "/plan", receipt=False)["reason"] == "no_receipt"
        world = World()
        world.talk.receipt = lambda host, kind, since: 1 / 0
        talker = world.talker()
        assert _deliver(world, talker, "/plan", receipt=False)["reason"] == "no_receipt"


def test_a_talk_host_may_name_its_process():
    assert TalkHost("h").pid is None and TalkHost("h", "ttys1", 7).pid == 7


# -- answering a question (ADR-029 increment 5) -----------------------------------------


@dataclass(frozen=True)
class Choice:
    label: str
    description: str = ""


@dataclass(frozen=True)
class Question:
    text: str
    header: str
    multi: bool
    choices: tuple


@dataclass(frozen=True)
class Asked:
    id: str
    questions: tuple
    kind: str = "question"
    other: bool = True
    version: str | None = "2.1.330"


HUE = Question("Pick one hue", "Hue", False, (Choice("Crimson", "red"), Choice("Teal"),
                                              Choice("Amber")))
TOPPINGS = Question("Pick toppings", "Toppings", True, (Choice("Cheese"), Choice("Olives"),
                                                        Choice("Basil")))


def _asked(*questions, call="toolu_q1"):
    return Asked(call, tuple(questions or (HUE,)))


def _asking(asked=None, **host):
    """A world whose window waits on a question the host reads as ``asked``."""
    world = World(turn="running", waiting="question", **host)
    world.record = {"turn": "running", "turn_since": "2026-11-20T10:00:00+00:00",
                    "waiting": {"kind": "question", "since": "2026-11-20T10:01:00+00:00"}}
    world.talk.questions[HOST] = asked if asked is not None else _asked()
    return world


def _read(world):
    return talk.read_question(world.talk, TalkHost(HOST, "ttys004"))


def _answer_nonce(talker, world, device=PHONE):
    return talker.issue(device.id, "K|s1", fingerprint(world.row), "K", "Heap", "Ship it",
                        pending=_read(world).question)


def _answer(talker, world, answers=None, device=PHONE, text=None):
    answers = [{"choices": [1], "other": None}] if answers is None else answers
    return talker.post(device, "K|s1", _answer_nonce(talker, world, device), "question", text,
                       answers=answers)


def _answered(world, talker, answers=None, receipt=True):
    """Tap an answer, let the Undo window pass and tick once; with ``receipt``
    the hook records the answer's digest as Claude Code would."""
    send = _answer(talker, world, answers).body["send"]
    world.sleep(5)
    if receipt:
        original = world.talk.answer

        def answer(host, pending, picks):
            code = original(host, pending, picks)
            world.record["received"] = {"kind": "question",
                                        "hash": world.talk.answer_digest(pending, picks),
                                        "at": _iso(world.wall)}
            return code

        world.talk.answer = answer
    talker.tick()
    return next(s for s in talker.sends(PHONE.id, "K|s1") if s["id"] == send["id"])


class TestReadingTheQuestion:
    def test_the_hosts_question_is_read(self):
        world = _asking()
        read = _read(world)
        assert read.question == _asked() and read.unreadable is False

    def test_none_pending_is_nothing_and_unreadable_or_raising_is_unreadable(self):
        world = _asking()
        world.talk.questions[HOST] = None
        assert _read(world) == talk.QuestionRead(None, False)
        world.talk.questions[HOST] = Unreadable("an unknown call")
        assert _read(world) == talk.QuestionRead(None, True)
        world.talk.pending = lambda host: 1 / 0
        assert _read(world) == talk.QuestionRead(None, True)

    def test_a_pending_prompt_that_is_no_question_is_nothing_here(self):
        world = _asking(Asked("p1", (HUE,), kind="permission"))
        assert _read(world) == talk.QuestionRead(None, False)

    def test_the_page_is_served_its_words_and_never_the_hosts_objects(self):
        view = talk.pending_view(talk.QuestionRead(_asked(HUE, TOPPINGS), False))
        assert view == {"kind": "question", "id": "toolu_q1", "unreadable": False, "questions": [
            {"text": "Pick one hue", "header": "Hue", "multi": False, "other": True,
             "choices": [{"label": "Crimson", "description": "red"},
                         {"label": "Teal", "description": ""},
                         {"label": "Amber", "description": ""}]},
            {"text": "Pick toppings", "header": "Toppings", "multi": True, "other": True,
             "choices": [{"label": "Cheese", "description": ""},
                         {"label": "Olives", "description": ""},
                         {"label": "Basil", "description": ""}]}]}
        assert talk.pending_view(talk.QuestionRead(None, True)) == {
            "kind": "question", "unreadable": True}
        assert talk.pending_view(talk.QuestionRead(None, False)) is None
        assert talk.pending_view(None) is None

    def test_the_first_line_names_it_on_a_card(self):
        long = Question("Which way?\nMore here", "H", False, (Choice("a"), Choice("b")))
        assert talk.first_line(talk.QuestionRead(_asked(long), False)) == "Which way?"
        assert talk.first_line(talk.QuestionRead(None, True)) is None
        many = Question("x" * 500, "H", False, (Choice("a"), Choice("b")))
        assert len(talk.first_line(talk.QuestionRead(_asked(many), False))) == 200


class TestWhatAnAnswerMayBe:
    @pytest.mark.parametrize(("answers", "picks"), [
        ([{"choices": [2]}], [talk.Pick((2,), None)]),
        ([{"choices": [], "other": " my own "}], [talk.Pick((), "my own")]),
        ([{"choices": [1]}, {"choices": [2, 0], "other": "oil"}],
         [talk.Pick((1,), None), talk.Pick((0, 2), "oil")]),
    ])
    def test_choices_by_index_and_other_as_a_line(self, answers, picks):
        asked = _asked(HUE, TOPPINGS) if len(answers) == 2 else _asked()
        assert talk.picks(asked, answers) == tuple(picks)

    @pytest.mark.parametrize("answers", [
        None, "1", [], [{"choices": [1]}, {"choices": [1]}], [["1"]],
        [{"choices": [3]}], [{"choices": [-1]}], [{"choices": [True]}], [{"choices": ["1"]}],
        [{"choices": [0, 1]}], [{"choices": [1], "other": "and this"}], [{"choices": []}],
        [{"choices": [], "other": "  "}], [{"choices": [], "other": "two\nlines"}],
        [{"choices": [], "other": "esc\x1b"}], [{"choices": [], "other": 5}],
        [{"choices": [], "other": "x" * 8001}], [{"choices": [1, 1]}],
        [{"choices": "1"}],
    ])
    def test_anything_else_is_refused(self, answers):
        assert isinstance(talk.picks(_asked(), answers), str)

    def test_several_choices_only_where_the_question_takes_several(self):
        assert isinstance(talk.picks(_asked(TOPPINGS), [{"choices": [0, 0]}]), str)
        assert talk.picks(_asked(TOPPINGS), [{"choices": [0, 1, 2]}]) == (
            talk.Pick((0, 1, 2), None),)


class TestWhatAnAnswerIsOfferedFor:
    def test_a_window_waiting_on_a_question_it_can_read_offers_an_answer(self):
        world = _asking()
        shown = world.talker().offered(fingerprint(world.row), True, False, _read(world))
        assert shown["answer"] is True and shown["interrupt"] is False

    @pytest.mark.parametrize("case", ["unreadable_session", "unreadable_question", "none",
                                      "permission", "no_tab", "marked", "gone"])
    def test_otherwise_no_answer(self, case):
        world = _asking() if case != "permission" else World(turn="running",
                                                              waiting="permission")
        read = _read(world) if case != "permission" else talk.QuestionRead(_asked(), False)
        talker = world.talker()
        unreadable = case == "unreadable_session"
        fp, live = fingerprint(world.row), True
        if case == "unreadable_question":
            read = talk.QuestionRead(None, True)
        elif case == "none":
            read = None
        elif case == "no_tab":
            fp = fingerprint(_row(turn="running", waiting="question", tty=None))
        elif case == "marked":
            talker._mark(fp, "unreadable")
        elif case == "gone":
            fp, live = None, False
        assert talker.offered(fp, live, unreadable, read)["answer"] is False


class TestAnAnswerTapped:
    def test_it_is_held_for_its_undo_window_and_audited_by_index(self):
        world = _asking()
        talker = world.talker()
        reply = _answer(talker, world, [{"choices": [], "other": "The secret is hunter2"}])
        assert reply.status == 202 and reply.body["send"]["kind"] == "question"
        line = world.lines[0]
        assert (line["event"], line["kind"], line["choice"]) == ("talk-held", "question", [[3]])
        assert line["length"] == len("The secret is hunter2")
        assert "hunter2" not in repr(world.lines) + repr(reply.body)
        assert "Crimson" not in repr(world.lines) and "Teal" not in repr(world.lines)
        world.sleep(4.9)
        talker.tick()
        assert world.talk.answered == []

    def test_a_read_that_saw_no_question_cannot_answer_one(self):
        world = _asking()
        talker = world.talker()
        nonce = talker.issue(PHONE.id, "K|s1", fingerprint(world.row), "K", "Heap", "x")
        reply = talker.post(PHONE, "K|s1", nonce, "question", None,
                            answers=[{"choices": [1]}])
        assert (reply.status, reply.body["reason"]) == (409, "answered")
        assert _events(world) == ["talk-refused"]

    def test_an_answer_that_does_not_fit_the_read_is_refused(self):
        world = _asking()
        talker = world.talker()
        reply = _answer(talker, world, [{"choices": [9]}])
        assert (reply.status, reply.body["reason"]) == (400, "answer")
        assert world.lines == []

    def test_an_answer_carries_no_text_of_its_own(self):
        world = _asking()
        reply = _answer(world.talker(), world, text="hi")
        assert (reply.status, reply.body["reason"]) == (400, "text")

    def test_a_window_that_offers_no_answer_is_refused(self):
        world = _asking(tty=None)
        reply = _answer(world.talker(), world)
        assert (reply.status, reply.body["reason"]) == (409, "no_tab")

    def test_undo_types_nothing(self):
        world = _asking()
        talker = world.talker()
        send = _answer(talker, world).body["send"]
        assert talker.cancel(PHONE, send["id"]).status == 200
        world.sleep(5)
        talker.tick()
        assert world.talk.answered == []


class TestAnAnswerDelivered:
    def test_it_is_typed_after_quiet_and_confirmed_by_the_answers_receipt(self):
        world = _asking()
        world.talk.looks = [RecordedLook("q", shows="question")] * 20
        talker = world.talker()
        send = _answered(world, talker, [{"choices": [1]}, ])
        assert send["state"] == "delivered"
        assert world.talk.answered == [(HOST, "ttys004", (talk.Pick((1,), None),))]
        assert world.locks == ["ttys004"]
        assert _events(world) == ["talk-held", "talk-typed", "talk-delivered"]

    def test_it_is_never_queued_and_waits_a_beat_for_a_delivery_under_way(self):
        world = _asking()
        talker = world.talker()
        _answer(talker, world)
        talker._inflight.add(HOST)
        world.sleep(5)
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["state"] == "held"
        talker._inflight.discard(HOST)
        talker.tick()
        assert world.talk.answered

    def test_no_receipt_is_not_confirmed_and_one_alert(self):
        world = _asking()
        talker = world.talker()
        send = _answered(world, talker, receipt=False)
        assert (send["state"], send["reason"]) == ("not_confirmed", "no_receipt")
        assert world.alerts == [("Heap", "Ship it")]

    def test_the_windows_evidence_confirms_it_when_no_hook_came(self):
        world = _asking()
        talker = world.talker()
        picks = (talk.Pick((1,), None),)
        world.talk.receipts.append(talk.RecordedReceipt(
            world.talk.answer_digest(_asked(), picks), world.wall + 60))
        assert _answered(world, talker, receipt=False)["state"] == "delivered"

    def test_the_input_box_back_means_it_was_answered_at_the_mac(self):
        world = _asking()
        world.talk.looks = [RecordedLook("box", shows="input")]
        send = _answered(world, world.talker())
        assert (send["state"], send["reason"]) == ("refused", "answered")
        assert world.talk.answered == []

    def test_a_question_someone_began_to_answer_is_typing_on_the_mac(self):
        world = _asking()
        world.talk.looks = [RecordedLook(str(n), shows="question", empty=False)
                            for n in range(200)]
        send = _answered(world, world.talker())
        assert (send["state"], send["reason"]) == ("refused", "typing")

    @pytest.mark.parametrize(("change", "reason", "what"), [
        ("answered", "answered", None), ("replaced", "answered", None),
        ("another_call", "answered", None), ("other_words", "answered", None),
        ("unreadable", "unreadable", None), ("process", "changed", "process"),
        ("gone", "gone", None)])
    def test_the_fresh_check_refuses_a_question_that_moved_on(self, change, reason, what):
        world = _asking()
        talker = world.talker()
        _answer(talker, world)
        if change == "answered":
            world.row = _row(turn="running")
        elif change == "replaced":
            world.row["hosts"][0]["attention"]["waiting"]["since"] = "2026-11-20T10:09:00+00:00"
        elif change == "another_call":
            world.talk.questions[HOST] = _asked(call="toolu_q2")
        elif change == "other_words":
            world.talk.questions[HOST] = _asked(TOPPINGS)
        elif change == "unreadable":
            world.talk.questions[HOST] = Unreadable("new shape")
        elif change == "process":
            world.row = _row(turn="running", waiting="question", pid=99)
        elif change == "gone":
            world.row = _row(turn="running", waiting="question", alive=False)
        world.sleep(5)
        talker.tick()
        send = talker.sends(PHONE.id, "K|s1")[0]
        assert (send["state"], send["reason"], send["what"]) == ("refused", reason, what)
        assert world.talk.answered == []

    @pytest.mark.parametrize(("code", "reason"), [
        ("busy", "typing"), ("answered", "answered"), ("unknown_shape", "unreadable"),
        ("no_claude", "no_claude"), ("no_tab", "no_tab"), ("not_allowed", "not_allowed"),
        ("weird", "failed")])
    def test_what_the_host_found_before_the_first_key_refuses(self, code, reason):
        world = _asking()
        world.talk.answer_code = code
        send = _answered(world, world.talker(), receipt=False)
        assert (send["state"], send["reason"]) == ("refused", reason)

    def test_a_host_that_stopped_part_way_waits_for_the_receipt(self):
        world = _asking()
        world.talk.answer_code = "failed"
        send = _answered(world, world.talker(), receipt=False)
        assert (send["state"], send["reason"]) == ("not_confirmed", "no_receipt")
        assert next(l for l in world.lines if l["event"] == "talk-typed")["reason"] == "failed"

    def test_a_host_that_raises_may_have_typed(self):
        world = _asking()
        world.talk.answer = lambda host, pending, picks: 1 / 0
        send = _answered(world, world.talker(), receipt=False)
        assert send["state"] == "not_confirmed"

    def test_a_typist_held_elsewhere_is_typing_on_the_mac(self):
        world = _asking()
        world.lock_fails = True
        send = _answered(world, world.talker())
        assert (send["state"], send["reason"]) == ("refused", "typing")

    def test_a_scan_that_fails_types_nothing(self):
        world = _asking()
        talker = world.talker()
        _answer(talker, world)
        talker.fresh = lambda: 1 / 0
        world.sleep(5)
        talker.tick()
        assert talker.sends(PHONE.id, "K|s1")[0]["reason"] == "failed"

    def test_a_window_without_a_lock_or_terminal_is_no_tab(self):
        world = _asking()
        talker = world.talker()
        talker.typist = None
        assert _answered(world, talker)["reason"] == "no_tab"

    def test_an_unreadable_look_turns_answer_off(self):
        world = _asking()
        world.talk.looks = [Unreadable("no view")]
        talker = world.talker()
        assert _answered(world, talker)["reason"] == "unreadable"
        assert talker.offered(fingerprint(world.row), True, False, _read(world))[
            "answer"] is False


class TestAMessageNeverTypedIntoAQuestion:
    def test_a_look_that_shows_a_question_is_not_quiet_for_a_message(self):
        world = World()
        world.talk.looks = [RecordedLook("q", shows="question")]
        talker = world.talker()
        assert _deliver(world, talker)["reason"] == "unreadable"
        assert world.talk.typed == []


class TestTheRecordersAnswer:
    def test_it_reads_the_question_it_is_given_and_types_nothing(self):
        recorder = RecordingTalk()
        assert recorder.pending(TalkHost("h")) is None
        picks = (talk.Pick((1,), None),)
        assert recorder.answer(TalkHost("h", "ttys1"), _asked(), picks) == ("typed", "")
        assert recorder.answered == [("h", "ttys1", picks)]
        assert recorder.answer_digest(_asked(), picks) != recorder.answer_digest(
            _asked(call="other"), picks)
