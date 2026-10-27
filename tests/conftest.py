"""Shared fixtures.

Every fixture value here is synthetic. Nothing in this suite may carry a real
person's identifier, which is the same rule the library's own privacy gate
enforces on its consumers.
"""

import os
import shlex
from pathlib import Path

import pytest
import yaml

from invariant.core.manifest import MANIFEST_RELPATH, Manifest


@pytest.fixture(autouse=True)
def _no_ambient_host_project_dir(monkeypatch):
    """Hooks resolve their manifest from CLAUDE_PROJECT_DIR when set (AUD-04).

    This suite itself runs inside an agent host, so the variable is set — and
    points at the library's own repository. Left in place it would make every
    hook test resolve the real project's manifest instead of the synthetic one
    the test built, which is the exact cross-project misattribution the
    resolution order exists to prevent. Tests that need the variable set it
    explicitly.
    """
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


@pytest.fixture(autouse=True)
def _no_ambient_session_id(monkeypatch):
    """This suite creates sessions, and `INVARIANT_SESSION_ID` names one.

    `SessionDomain.start()` resolves the id to register as
    `session_id or os.environ["INVARIANT_SESSION_ID"]`, so with the variable
    set every `.start()` that does not pass an id explicitly resolves to the
    real, live session instead of the derived fallback — and collides with it.
    Measured: 25 false failures in `test_domains.py` against an identical bare
    run that was fully green (BUG_035).

    `domains/pipeline` now strips it from the environment it spawns the suite
    with, which fixes the route through `invariant pipeline test`. This is the
    other route: a bare `uv run pytest` typed in a shell that has it exported.
    Tests that need the variable set it explicitly.
    """
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)


@pytest.fixture(autouse=True)
def _no_real_oracle_home(monkeypatch, tmp_path_factory):
    """No test may write the developer's real ``~/.invariant``.

    ``init`` and the SessionStart hook register the project in the oracle's
    user-level index, so without this every test that initialises a project
    would add a temporary directory to the index on the machine running the
    suite. The variable is set, not deleted, because the fallback is the real
    home; it is exported, so a hook shim a test runs as a subprocess inherits
    it too. The directory itself is not created: a test that asserts nothing
    was written can check that it does not exist.
    """
    home = tmp_path_factory.mktemp("oracle") / "home"
    monkeypatch.setenv("INVARIANT_HOME", str(home))
    return home


class InertCloser:
    """A host closer that signals nothing and records what it was asked."""

    def __init__(self, name: str | None = "claude") -> None:
        self.name = name
        self.asked: list[int] = []
        self.closed: list[tuple[int, int]] = []

    def process_name(self, pid: int) -> str | None:
        self.asked.append(pid)
        return self.name

    def close(self, pid: int, delay: int) -> None:
        self.closed.append((pid, delay))


@pytest.fixture(autouse=True)
def _no_real_host_close(monkeypatch):
    """No test anywhere may end the Claude Code session it runs inside.

    A successful `session handoff` schedules a SIGTERM to `CLAUDE_PID`
    (IDEA_009), and this suite runs inside a Claude Code session that exports
    it — so a test reaching the command with the real closer in place would end
    the host running the suite. The closer is replaced suite-wide, not only in
    the handoff tests, and the ambient pid is removed: a test that wants one
    sets it.
    """
    monkeypatch.delenv("CLAUDE_PID", raising=False)
    inert = InertCloser()
    monkeypatch.setattr(
        "invariant.cli.main._handoff_closer", lambda: inert, raising=False
    )
    return inert


@pytest.fixture(autouse=True)
def _no_real_process_walk(monkeypatch):
    """No hook in the suite reads the real process table for its host pid.

    A hook records its host's pid in the attention sidecar, from ``CLAUDE_PID``
    or, without it, by walking the parent chain with ``ps``. The closer fixture
    above removes ``CLAUDE_PID``, so every hook a test runs would otherwise
    walk — slower, and answering with whatever ``claude`` happens to be running
    the suite. The walk finds nothing here; a test about the walk supplies a
    chain of its own.
    """
    monkeypatch.setattr(
        "invariant.cli.main._attention_process_lookup",
        lambda: (lambda pid: None),
        raising=False,
    )


class InertWatch:
    """A successor watch that reads no process table.

    It answers what a stub launcher makes true: nothing was started, so there
    is no successor, and a handoff that reaches it fails as a real one with no
    successor would. A test whose subject is the launch that worked sets
    ``pid`` to the successor it pretends was found.
    """

    def __init__(self, pid: int | None = None) -> None:
        self.pid = pid
        self.watched: list[tuple[str, set[int]]] = []

    def running(self, name: str) -> set[int]:
        return set()

    def wait_for(self, name: str, exclude: set[int]) -> int | None:
        self.watched.append((name, set(exclude)))
        return self.pid


@pytest.fixture(autouse=True)
def _no_real_successor_watch(monkeypatch):
    """No test waits on the real process table for a successor (BUG_075).

    The real watch polls ``ps`` for up to ``SUCCESSOR_TIMEOUT_SECONDS``, and no
    test starts a successor, so it would only ever time out. Replaced
    suite-wide beside the closer.
    """
    inert = InertWatch()
    monkeypatch.setattr(
        "invariant.cli.main._handoff_successor", lambda: inert, raising=False
    )
    return inert


@pytest.fixture(autouse=True)
def _no_real_names_in_use(monkeypatch):
    """No test reads the real ``~/.claude`` or process table for names in use.

    ``oracle start-session`` gives a new window a name no live Claude holds,
    and asks one seam, ``_oracle_names_in_use``, which reads Claude Code's
    per-process records and ``ps``. Here it answers that none is in use; a
    test about a clash sets its own.
    """
    monkeypatch.setattr(
        "invariant.cli.main._oracle_names_in_use", lambda: set(), raising=False
    )


@pytest.fixture(autouse=True)
def _no_real_notifier(monkeypatch):
    """No test posts a real macOS notification (IDEA_012 increment 4).

    ``oracle watch`` and ``oracle notify-test`` reach the notifier through one
    seam, ``_oracle_notifier``, replaced here suite-wide by a recorder. A test
    about what was sent reads the recorder this fixture returns.
    """
    from invariant.oracle.notify import RecordingNotifier

    recorder = RecordingNotifier()
    monkeypatch.setattr(
        "invariant.cli.main._oracle_notifier", lambda: recorder, raising=False
    )
    return recorder


@pytest.fixture(autouse=True)
def _no_real_launchd(monkeypatch, tmp_path_factory):
    """No test loads, unloads or writes a real LaunchAgent (IDEA_012 increment 6).

    ``oracle install``, ``uninstall`` and ``status``, and ``doctor``'s
    interpreter check, reach launchd through one seam, ``_oracle_installer``.
    It is replaced here suite-wide by an installer over a temporary
    ``LaunchAgents`` directory and a recording launchctl, which this fixture
    returns. The guard below refuses the real program as the backstop.
    """
    from invariant.oracle.launchd import recording_installer

    installer = recording_installer(tmp_path_factory.mktemp("launchd") / "LaunchAgents")
    monkeypatch.setattr(
        "invariant.cli.main._oracle_installer", lambda: installer, raising=False
    )
    return installer


@pytest.fixture(autouse=True)
def _no_real_web_launchd(monkeypatch, _no_real_launchd):
    """No test loads or writes the web page's LaunchAgent (IDEA_023 increment 3).

    ``oracle install --web``, ``uninstall``, ``status``, ``web pair`` and
    ``doctor`` reach the second agent, ``dev.invariant.oracle.web``, through
    ``_oracle_web_installer``. It is replaced here by an installer over the
    same temporary ``LaunchAgents`` directory and the same recording
    launchctl as the watcher's, so a test sees both agents in one launchd.
    """
    from invariant.oracle.launchd import WEB_LABEL, recording_installer

    installer = recording_installer(
        _no_real_launchd.agents_dir, label=WEB_LABEL, run=_no_real_launchd.run
    )
    monkeypatch.setattr(
        "invariant.cli.main._oracle_web_installer", lambda: installer, raising=False
    )
    return installer


class InertLauncher:
    """A terminal launcher that opens nothing and records each plan."""

    def __init__(self) -> None:
        self.plans: list = []

    def launch(self, plan) -> None:
        self.plans.append(plan)


@pytest.fixture(autouse=True)
def _no_real_terminal_launch(monkeypatch):
    """No test opens a Terminal window (IDEA_012 increment 7).

    ``session handoff`` and the oracle dashboard's resume action reach
    Terminal through one seam, ``_handoff_launcher``. ``test_handoff.py``
    installs its own recorder over this one; everywhere else this inert one
    stands, and the ``osascript`` guard below is the backstop. A test about
    what would have launched reads the plans it recorded.
    """
    inert = InertLauncher()
    monkeypatch.setattr(
        "invariant.cli.main._handoff_launcher", lambda: inert, raising=False
    )
    return inert


@pytest.fixture(autouse=True)
def _no_real_oracle_runner(monkeypatch):
    """No test runs a command the oracle dashboard recommends.

    A confirmed ``run`` step leaves through one seam, ``_oracle_runner``,
    replaced here suite-wide by a recorder that runs nothing and answers
    ``(0, "")``. A test about what ran reads the calls it recorded.
    """
    from invariant.oracle.actions import RecordingRunner

    recorder = RecordingRunner()
    monkeypatch.setattr(
        "invariant.cli.main._oracle_runner", lambda: recorder, raising=False
    )
    return recorder


@pytest.fixture(autouse=True)
def oracle_restarts(monkeypatch):
    """No test replaces the test runner with a restarted oracle (BUG_089).

    ``oracle watch`` and ``oracle web --wait`` run themselves again through
    one seam, ``_oracle_restart``, replaced here suite-wide by a recorder
    that counts the restarts asked for and answers 0.
    """
    restarts: list[str] = []

    def restart() -> int:
        restarts.append("restart")
        return 0

    monkeypatch.setattr(
        "invariant.cli.main._oracle_restart", lambda: restart, raising=False
    )
    return restarts


@pytest.fixture(autouse=True)
def oracle_web(monkeypatch):
    """No test binds a Tailscale address or asks the real ``tailscale`` (IDEA_023).

    ``oracle web`` finds the Mac's Tailscale addresses through one seam,
    ``_oracle_web_addresses``, and listens through another,
    ``_oracle_web_server``. Both are replaced here suite-wide: discovery finds
    no tailnet until a test sets ``addresses``, and the server binds nothing
    and records what it was asked to serve, and ``oracle web --wait``'s
    pauses (``_oracle_web_wait``) run the test's ``script`` and then stop the
    loop instead of sleeping. The guard below refuses
    ``tailscale`` as the backstop. A test that needs a real socket builds a
    ``WebServer`` on loopback itself.
    """
    from invariant.oracle.web import RecordingWeb

    recorder = RecordingWeb()
    monkeypatch.setattr(
        "invariant.cli.main._oracle_web_addresses",
        lambda: (lambda: list(recorder.addresses)),
        raising=False,
    )
    monkeypatch.setattr(
        "invariant.cli.main._oracle_web_server", lambda: recorder.factory, raising=False
    )
    monkeypatch.setattr(
        "invariant.cli.main._oracle_web_wait", lambda: recorder.wait, raising=False
    )
    return recorder


@pytest.fixture(autouse=True)
def oracle_certificates(monkeypatch):
    """No test asks Tailscale for a certificate (IDEA_026).

    ``oracle web`` gets its HTTPS certificate through one seam,
    ``_oracle_web_certificates``, beside the address discovery and the
    server. It is replaced here suite-wide by a ``RecordingIssuer`` that
    refuses until a test hands it one of the self-signed fixtures under
    ``tests/fixtures/tls``, so by default the page falls back to plain http.
    The program guard below refuses ``tailscale`` as the backstop.
    """
    from invariant.oracle.tls import RecordingIssuer

    issuer = RecordingIssuer()
    monkeypatch.setattr(
        "invariant.cli.main._oracle_web_certificates", lambda: issuer, raising=False
    )
    return issuer


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_osascript: the test runs osascript on purpose, for a script that "
        "opens, posts and changes nothing",
    )


class RefusedProgram(BaseException):
    """A test reached a program the suite never runs for real.

    A ``BaseException`` and not an ``AssertionError``: code under test that
    catches ``Exception`` broadly — the watcher's notify path does, on
    purpose — would otherwise swallow the refusal and carry on as if the
    program had failed normally.
    """


#: Never run, by any route. ``osascript`` alone can be admitted, per test.
#: ``tailscale`` and the macOS app's ``Tailscale`` would report, or change,
#: the real tailnet.
_REFUSED = ("osascript", "launchctl", "tailscale", "Tailscale")

#: Programs that run their ``-c`` argument as a command string.
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})

#: Words that run the word after them as the program.
_WRAPPERS = frozenset({"exec", "env", "command", "nohup", "time", "sudo"})

#: Shell operators that start a new command.
_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "(", ")"})


def _word(value) -> str:
    """One argument as text: a str, bytes, or any ``os.PathLike``."""
    value = os.fspath(value)
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)


def _command_programs(command: str, depth: int) -> set[str]:
    """Best effort: the program each command in a shell string starts."""
    # shlex reads a newline as whitespace; to a shell it ends a command.
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        tokens = command.split()
    programs: set[str] = set()
    segment: list[str] = []
    for token in [*tokens, ";"]:
        if token not in _SEPARATORS:
            segment.append(token)
            continue
        words = [w for w in segment if "=" not in w.split("/")[0]]
        while words and words[0] in _WRAPPERS:
            words = [w for w in words[1:] if not w.startswith("-")]
        if words:
            programs |= _argv_programs(words, depth + 1)
        segment = []
    return programs


def _argv_programs(argv: list, depth: int = 0) -> set[str]:
    """The program ``argv`` starts, and what a ``sh -c`` in it would start."""
    words = [_word(arg) for arg in argv]
    if not words:
        return set()
    program = Path(words[0]).name
    programs = {program}
    if program in _SHELLS and depth < 3:
        for position, word in enumerate(words[1:], start=1):
            if word.startswith("-") and not word.startswith("--") and "c" in word[1:]:
                if position + 1 < len(words):
                    programs |= _command_programs(words[position + 1], depth)
                break
    return programs


def _programs_of(args, shell: bool = False, executable=None) -> set[str]:
    """Every program a ``Popen(args, shell=…, executable=…)`` would start."""
    if isinstance(args, (str, bytes, os.PathLike)):
        args = [args]
    else:
        args = list(args)
    if shell:
        command = " ".join(_word(arg) for arg in args[:1])
        programs = _command_programs(command, 0)
    else:
        programs = _argv_programs(args)
    if executable is not None:
        programs.add(Path(_word(executable)).name)
    return programs


@pytest.fixture(autouse=True)
def _no_real_osascript(request, monkeypatch):
    """No test runs ``osascript``, ``launchctl`` or ``tailscale``, by any route.

    ``osascript`` opens Terminal windows (``session handoff``) and posts
    notifications (the oracle); ``launchctl`` loads and unloads the oracle's
    LaunchAgent. Every route to either is a seam the suite replaces, and this
    is the backstop: ``subprocess.Popen`` — which ``run`` and every other
    helper start through — refuses them, whether named in an argument list,
    as a bare string or path, through ``executable=``, or inside a shell
    command string (``shell=True``, ``sh -c``; best effort). A test that
    evaluates a pure AppleScript expression on purpose carries
    ``real_osascript``. Nothing admits ``launchctl``.
    """
    admitted = {"osascript"} if request.node.get_closest_marker("real_osascript") else set()
    refused = set(_REFUSED) - admitted
    import subprocess

    real_init = subprocess.Popen.__init__

    def guarded(self, args, *rest, **kwargs):
        hit = refused & _programs_of(
            args, shell=bool(kwargs.get("shell")), executable=kwargs.get("executable")
        )
        if hit:
            raise RefusedProgram(
                f"a test ran {', '.join(sorted(hit))}: {args!r}; replace the seam"
                + (", or mark the test real_osascript if it evaluates a script "
                   "with no effect" if "osascript" in hit else "")
            )
        real_init(self, args, *rest, **kwargs)
        _SPAWNED.add(self.pid)

    monkeypatch.setattr(subprocess.Popen, "__init__", guarded)


#: The pids of the processes the running test started itself. Only these may
#: be sent a signal; see ``_no_real_signal``.
_SPAWNED: set[int] = set()


@pytest.fixture(autouse=True)
def _no_real_signal(monkeypatch):
    """No test signals a process it did not start itself (IDEA_027 increment 6).

    ``oracle close-host`` sends SIGTERM to a Claude process, and this suite
    runs inside one. Every route to a real signal is a seam the suite replaces
    (``_oracle_host_seams``, ``_handoff_closer``); this is the backstop:
    ``os.kill`` and ``os.killpg`` refuse any signal but 0, which only asks
    whether a pid exists, unless the pid is a child this test spawned through
    ``subprocess.Popen``. It raises :class:`RefusedProgram`, so a broad
    ``except`` cannot hide it.
    """
    real_kill, real_killpg = os.kill, os.killpg

    def kill(pid, sig):
        if sig == 0 or pid in _SPAWNED:
            return real_kill(pid, sig)
        raise RefusedProgram(
            f"a test sent signal {sig} to process {pid} (kill), which it did not spawn; "
            "replace the seam"
        )

    def killpg(pgid, sig):
        if sig == 0:
            return real_killpg(pgid, sig)
        raise RefusedProgram(f"a test sent signal {sig} to process group {pgid} (killpg)")

    _SPAWNED.clear()
    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(os, "killpg", killpg)
    yield
    _SPAWNED.clear()


class InertHostSeams:
    """Host seams for ``oracle close-host`` that know no process and signal nothing."""

    def __init__(self) -> None:
        self.signalled: list[int] = []
        self.tabs: list[str] = []

    def build(self):
        from invariant.oracle.closehost import HostSeams

        return HostSeams(
            records=list,
            facts=lambda pid: None,
            alive=lambda pid: False,
            terminate=self.signalled.append,
            in_terminal=lambda pid: None,
            close_tab=lambda tty: self.tabs.append(tty) or ("no_tab", ""),
            sleep=lambda seconds: None,
        )


@pytest.fixture(autouse=True)
def _no_real_close_host(monkeypatch):
    """No test reaches the real seams behind ``oracle close-host``.

    The command re-reads Claude Code's per-process records, asks ``ps``,
    sends SIGTERM and runs ``osascript`` against Terminal, all through one
    seam, ``_oracle_host_seams``. It is replaced here by seams that know no
    process, so the command refuses before it signals anything; a test about
    the command passes fakes of its own.
    """
    inert = InertHostSeams()
    monkeypatch.setattr(
        "invariant.cli.main._oracle_host_seams", inert.build, raising=False
    )
    return inert


def _loopback(address) -> bool:
    """Whether a socket address stays on this machine: loopback or a path."""
    import ipaddress

    if not isinstance(address, tuple):
        return True  # AF_UNIX: a path, or an abstract name
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _no_network_beyond_loopback(monkeypatch):
    """No test binds, connects, sends or looks a name up beyond loopback (IDEA_023).

    macOS asks the person whether Python may "find devices on your local
    network" the first time a process reaches past loopback, so a test that
    did would put a privacy prompt on the screen of whoever runs the suite.
    ``oracle web``'s address discovery and its server are seams the suite
    replaces (``oracle_web`` above); this is the backstop. ``bind``,
    ``connect``, ``connect_ex`` and ``sendto`` refuse any address that is not
    loopback, the wildcard ``""``, ``0.0.0.0`` and ``::`` included, and a
    reverse lookup (``gethostbyaddr``, which ``socket.getfqdn`` and the
    standard library's ``HTTPServer.server_bind`` make) is refused outright.
    It raises :class:`RefusedProgram`, so a broad ``except`` cannot hide it.
    """
    import socket

    def guard(name, real):
        def guarded(self, address, *rest):
            target = rest[-1] if name == "sendto" and rest else address
            if not _loopback(target):
                raise RefusedProgram(
                    f"a test called socket.{name}({target!r}): only loopback is "
                    f"allowed, so the suite never reaches the local network"
                )
            if name == "sendto":
                return real(self, address, *rest)
            return real(self, address)

        return guarded

    for name in ("bind", "connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, guard(name, getattr(socket.socket, name)))

    def no_reverse_lookup(host, *rest):
        raise RefusedProgram(
            f"a test asked for the name of {host!r}: a reverse lookup can reach "
            f"the local network, and the suite never does"
        )

    monkeypatch.setattr(socket, "gethostbyaddr", no_reverse_lookup)


def write_manifest(root: Path, body: dict) -> Path:
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path):
    """A minimal project directory with a manifest."""
    write_manifest(tmp_path, {"version": 1, "project": {"name": "demo"}})
    (tmp_path / "docs" / "architecture").mkdir(parents=True)
    (tmp_path / ".claude" / "memory").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def manifest(project):
    return Manifest.load(project)


@pytest.fixture
def make_manifest(tmp_path):
    def build(body: dict, root: Path | None = None) -> Manifest:
        root = root or tmp_path
        write_manifest(root, body)
        return Manifest.load(root)

    return build
