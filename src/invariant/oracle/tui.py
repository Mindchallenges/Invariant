"""The oracle's live dashboard: ``invariant oracle tui``.

A thin Textual app over :mod:`~invariant.oracle.dashboard` (what to show) and
:mod:`~invariant.oracle.actions` (what a confirmed step does). Textual ships
as the ``invariant[oracle]`` extra, so this module is imported only by the
command that runs it, and the rest of the library never needs it (ADR-007
D19).

One table: a line per live session with its hosts nested under it, then the
orphan hosts, the leaked worktrees and what could not be read. A high alert
is red, any other alert amber, markers dim. The table refreshes on the
snapshot's own interval, in a worker thread, so a slow in-process scan never
freezes the screen.

**It always refreshes.** The timer starts when the app mounts, before the
first read, and a read that fails leaves it running, so a broken first load
is retried on the next tick rather than never. A line under the banner says
``updated N s ago`` every second and turns red once the rows are older than
three intervals, :func:`~invariant.oracle.dashboard.freshness`, the rule the
web page and ``oracle status`` use too.

**Nothing runs without a confirm.** Enter (or ``a``) on a line opens a modal:
an ``info`` step shows its text and has no action; a ``run`` or ``terminal``
step shows the exact argv, cwd and environment, focuses Cancel, and runs only
on ``y`` or the Run button. The output is shown when it finishes.

**A new session, the same way** (IDEA_030, ADR-009 D16). ``n`` opens the "New
session" modal: a picker of the registered projects, the highlighted row's
preselected, an optional first message, an editable label, and what would
start, the full name, the two facts (open windows, uncommitted changes in the
main checkout) and the exact command, asked of the CLI's own
:class:`~invariant.oracle.startsession.Starter` after every edit. Nothing runs
until Start; Start prepares again from a fresh read and starts nothing if the
name moved (:meth:`~invariant.oracle.startsession.Starter.again`), and the
outcome is shown as a step's output is.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import ClassVar

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Button, DataTable, Footer, Header, Input, Select, Static

from ..core.errors import InvariantError
from .actions import Actions, Outcome, Step, refusal, step_for
from .dashboard import (
    AMBER,
    DIM,
    PLAIN,
    RED,
    SOURCE_SNAPSHOT,
    Line,
    Loaded,
    freshness,
    lines,
)
from .recommend import INFO
from .startsession import Starter, choices, fact_lines
from .watcher import DEFAULT_INTERVAL

COLUMNS = ("project", "session", "stage / lane", "status", "signals", "next")

#: How each tone is drawn.
TONE_STYLES = {RED: "bold red", AMBER: "dark_orange", DIM: "dim", PLAIN: ""}

_MODAL_CSS = """
{name} {{ align: center middle; }}
{name} > Vertical {{
    width: 90%; height: auto; max-height: 90%;
    border: thick $accent; background: $surface; padding: 1 2;
}}
{name} Horizontal {{ height: auto; margin-top: 1; }}
{name} Button {{ margin-right: 2; }}
"""

#: Seconds the "New session" modal waits after an edit before it asks again,
#: as the page waits after a keystroke: reading the names in use costs a
#: process-table read.
START_DEBOUNCE = 0.3

#: What the modal says about the mode; there is no choice to offer (D16).
START_MODE = (
    "Claude's default permission mode: every permission prompt reaches you "
    "through Remote Control, and the oracle answers none"
)


class InfoScreen(ModalScreen[None]):
    """Text only. There is nothing to confirm."""

    DEFAULT_CSS = _MODAL_CSS.format(name="InfoScreen")
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "close"), Binding("q", "close", "close")]

    def __init__(self, title: str, text: str) -> None:
        super().__init__()
        self.title_text = title
        self.text = text

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self.title_text, style="bold"))
            yield Static(Text(self.text), id="text")
            with Horizontal():
                yield Button("Close", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """The exact command, and an explicit choice. Cancel has the focus."""

    DEFAULT_CSS = _MODAL_CSS.format(name="ConfirmScreen")
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y", "confirm", "run it"),
        Binding("n", "cancel", "cancel"),
        Binding("escape", "cancel", "cancel"),
    ]

    def __init__(self, step: Step, lines: list[str]) -> None:
        super().__init__()
        self.step = step
        self.lines = lines

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(f"{self.step.subject}: {self.step.label}", style="bold"))
            if self.step.detail:
                yield Static(Text(self.step.detail, style="dim"))
            yield Static(Text("\n".join(self.lines)), id="command")
            with Horizontal():
                yield Button("Cancel", id="cancel", variant="primary")
                yield Button("Run", id="confirm", variant="error")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class OutputScreen(InfoScreen):
    """What a confirmed step, or a start, did."""

    DEFAULT_CSS = _MODAL_CSS.format(name="OutputScreen")

    def __init__(self, title: str, outcome: Outcome) -> None:
        super().__init__(title, outcome.text or "(no output)")
        self.outcome = outcome

    def compose(self) -> ComposeResult:
        if self.outcome.status is not None:
            said = f"exit {self.outcome.status}"
        else:
            said = "done" if self.outcome.ok else "failed"
        with Vertical():
            yield Static(Text(self.title_text, style="bold"))
            yield Static(
                Text(said, style="green" if self.outcome.ok else "bold red"), id="status"
            )
            with VerticalScroll():
                yield Static(Text(self.text), id="output")
            with Horizontal():
                yield Button("Close", id="close")


class StartScreen(ModalScreen["tuple[str, dict] | None"]):
    """The "New session" confirm: what would start, asked again on every edit.

    Every answer is the Starter's, the CLI's own ``_start_prepare``, so the
    name, the facts, the command and every refusal are the command's. Start
    is enabled only while the shown answer is for what the fields hold now,
    and dismisses with ``(project key, prepared)``; nothing runs here.
    """

    DEFAULT_CSS = _MODAL_CSS.format(name="StartScreen") + """
    StartScreen Select, StartScreen Input { margin-top: 1; }
    StartScreen #name { margin-top: 1; text-style: bold; }
    StartScreen #refused { color: $error; text-style: bold; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, starter: Starter, document: dict, offered: list[tuple[str, str]],
                 selected: str) -> None:
        super().__init__()
        self.starter = starter
        self.document = document
        self.choices = offered
        self.selected = selected
        #: The answer shown, and the project it is for.
        self.prepared: dict | None = None
        self.prepared_key = ""
        self._pending: Timer | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text("New session", style="bold"))
            yield Static(Text(
                "A new Terminal window with Remote Control on, in the project's main "
                "checkout. Nothing starts until Start.", style="dim"))
            yield Select(self.choices, value=self.selected, allow_blank=False, id="project")
            yield Input(placeholder="first message (optional)", id="prompt")
            yield Input(placeholder="label (blank: the time, MMDD-HHMM)", id="label")
            yield Static(id="name")
            yield Static(id="refused")
            yield Static(id="message")
            yield Static(id="facts")
            yield Static(id="command")
            yield Static(Text(START_MODE, style="dim"), id="mode")
            with Horizontal():
                yield Button("Cancel", id="cancel", variant="primary")
                yield Button("Start", id="start", variant="error", disabled=True)

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self.ask(*self.inputs())

    def selected_key(self) -> str:
        value = self.query_one("#project", Select).value
        return value if isinstance(value, str) else ""

    def inputs(self) -> tuple[str, str, str]:
        """``(project key, label, first message)`` as the fields hold them now."""
        return (self.selected_key(), self.query_one("#label", Input).value,
                self.query_one("#prompt", Input).value)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._edited()

    def on_select_changed(self, event: Select.Changed) -> None:
        self._edited()

    def _edited(self) -> None:
        self.query_one("#start", Button).disabled = True
        if self._pending is not None:
            self._pending.stop()
        self._pending = self.set_timer(START_DEBOUNCE, lambda: self.ask(*self.inputs()))

    @work(thread=True, exclusive=True, group="start-prepare")
    def ask(self, key: str, label: str, prompt: str) -> None:
        """Ask the Starter what ``(key, label, prompt)`` would start; launches nothing."""
        try:
            prepared, why = self.starter.prepare_in(
                self.document, key, label.strip() or None, prompt or None), None
        except InvariantError as exc:
            prepared, why = None, str(exc)
        except Exception as exc:  # noqa: BLE001 - shown in the modal; the dashboard goes on
            prepared, why = None, f"{type(exc).__name__}: {exc}"
        self.app.call_from_thread(self.show_answer, (key, label, prompt), prepared, why)

    def show_answer(self, asked: tuple[str, str, str], prepared: dict | None,
                    why: str | None) -> None:
        """Show one answer, unless the fields moved on since it was asked."""
        if asked != self.inputs():
            return
        self.prepared, self.prepared_key = prepared, asked[0]
        start = self.query_one("#start", Button)
        if prepared is None:
            self.query_one("#refused", Static).update(Text(f"will not start: {why}"))
            for part in ("#name", "#message", "#facts", "#command"):
                self.query_one(part, Static).update("")
            start.disabled = True
            return
        message = prepared.get("prompt")
        self.query_one("#refused", Static).update("")
        self.query_one("#name", Static).update(Text(f"name: {prepared['name']}"))
        self.query_one("#message", Static).update(
            Text(f"first message: {message}" if message else "first message: none"))
        self.query_one("#facts", Static).update(Text("\n".join(fact_lines(prepared["facts"]))))
        self.query_one("#command", Static).update(
            Text(f"command  {prepared['by_hand']}\nin       {prepared['cwd']}"))
        start.disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self.action_start()
        else:
            self.dismiss(None)

    def action_start(self) -> None:
        if self.prepared is None or self.query_one("#start", Button).disabled:
            return
        self.dismiss((self.prepared_key, self.prepared))

    def action_cancel(self) -> None:
        self.dismiss(None)


class OracleApp(App):
    """Every session on the machine, and the one next step for each."""

    TITLE = "invariant oracle"
    CSS = """
    #banner { background: $warning 30%; padding: 0 1; }
    #source { color: $text-muted; padding: 0 1; }
    #fresh { color: $text-muted; padding: 0 1; }
    #fresh.stale { color: $error; text-style: bold; }
    #rows { height: 1fr; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("a", "act", "act on the row"),
        Binding("n", "new_session", "new session"),
        Binding("r", "reload", "refresh"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(
        self,
        loader: Callable[[], Loaded],
        actions: Actions,
        interval: float = DEFAULT_INTERVAL,
        clock: Callable[[], float] = time.time,
        starter: Starter | None = None,
    ) -> None:
        super().__init__()
        self.loader = loader
        self.actions = actions
        self.clock = clock
        #: How ``n`` reaches ``oracle start-session``'s own path; ``None``: not offered.
        self.starter = starter
        self.lines: dict[str, Line] = {}
        #: The last read shown, whose projects the "New session" picker offers.
        self.document: dict = {}
        #: Seconds between refreshes until a read says otherwise.
        self.interval = interval
        self.refresh_timer: Timer | None = None
        #: When the shown rows were read, and how old they were then.
        self.read_at: float | None = None
        self.read_age = 0.0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="banner")
        yield Static(id="source")
        yield Static(id="fresh")
        yield DataTable(id="rows", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#banner").display = False
        self.query_one("#rows", DataTable).add_columns(*COLUMNS)
        # Armed before the first read, so a read that fails is retried.
        self.refresh_timer = self.set_interval(self.interval, self.action_reload)
        self.set_interval(1.0, self.show_freshness)
        self.show_freshness()
        self.action_reload()

    # -- loading -------------------------------------------------------

    def action_reload(self) -> None:
        self._load()

    @work(thread=True, exclusive=True, group="load")
    def _load(self) -> None:
        try:
            loaded = self.loader()
        except Exception as exc:  # noqa: BLE001 - a failed read is shown; the next one runs
            self.call_from_thread(self._show_failure, f"{type(exc).__name__}: {exc}")
            return
        self.call_from_thread(self._show, loaded)

    def _show_failure(self, message: str) -> None:
        banner = self.query_one("#banner", Static)
        banner.update(Text(f"could not read the machine: {message}"))
        banner.display = True
        self.show_freshness()

    def show_freshness(self) -> None:
        """``updated N s ago``, red once it is stale. Ages through failures."""
        age = None if self.read_at is None else self.read_age + max(
            0.0, self.clock() - self.read_at
        )
        text, stale = freshness(age, self.interval)
        fresh = self.query_one("#fresh", Static)
        fresh.update(Text(text))
        fresh.set_class(stale, "stale")

    def _show(self, loaded: Loaded) -> None:
        banner = self.query_one("#banner", Static)
        banner.update(Text(loaded.banner))
        banner.display = bool(loaded.banner)
        if loaded.source == SOURCE_SNAPSHOT:
            source = f"from the watcher's snapshot, generated {loaded.document.get('generated')}"
        else:
            source = "scanned in-process"
        self.query_one("#source", Static).update(Text(source))

        self.document = loaded.document
        table = self.query_one("#rows", DataTable)
        keep = self.current_key()
        table.clear()
        self.lines = {}
        for line in lines(loaded.document):
            self.lines[line.key] = line
            table.add_row(*_cells(line), key=line.key)
        if keep in self.lines:
            table.move_cursor(row=table.get_row_index(keep))

        self.read_at = self.clock()
        self.read_age = loaded.age
        if loaded.interval != self.interval:
            if self.refresh_timer is not None:
                self.refresh_timer.stop()
            self.interval = loaded.interval
            self.refresh_timer = self.set_interval(loaded.interval, self.action_reload)
        self.show_freshness()

    def current_key(self) -> str | None:
        table = self.query_one("#rows", DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    # -- acting --------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_act()

    def action_act(self) -> None:
        key = self.current_key()
        if key is None:
            return
        row = self.lines[key].row
        step = step_for(row)
        if step is None:
            self.notify("nothing is recommended for this row")
            return
        reason = refusal(step, row)
        if reason:
            self.push_screen(InfoScreen(f"{step.subject}: not run", reason))
        elif step.kind == INFO:
            self.push_screen(InfoScreen(step.subject, "\n".join(filter(None, [step.label, step.detail]))))
        else:
            self.push_screen(
                ConfirmScreen(step, self.actions.describe(step)),
                lambda confirmed: self._perform(step) if confirmed else None,
            )

    @work(thread=True, group="act")
    def _perform(self, step: Step) -> None:
        outcome = self.actions.perform(step)
        self.call_from_thread(self.push_screen, OutputScreen(f"{step.subject}: {step.label}", outcome))
        self.call_from_thread(self.action_reload)

    # -- starting a session (ADR-009 D16) ------------------------------

    def action_new_session(self) -> None:
        if self.starter is None:
            self.notify("starting a session is not available here")
            return
        offered = choices(self.document)
        if not offered:
            self.notify("no registered project to start a session in")
            return
        key = self.current_key()
        here = self.lines[key].project_key if key is not None else ""
        selected = here if here in {k for _, k in offered} else offered[0][1]
        self.push_screen(StartScreen(self.starter, self.document, offered, selected),
                         self._confirmed_start)

    def _confirmed_start(self, confirmed: tuple[str, dict] | None) -> None:
        if confirmed is not None:
            self._start(*confirmed)

    @work(thread=True, group="act")
    def _start(self, key: str, prepared: dict) -> None:
        outcome = self._start_outcome(key, prepared)
        self.call_from_thread(self.push_screen, OutputScreen("New session", outcome))
        self.call_from_thread(self.action_reload)

    def _start_outcome(self, key: str, prepared: dict) -> Outcome:
        """Prepare again from a fresh read, as the page does, and launch only if it holds."""
        try:
            document = self.loader().document
        except Exception as exc:  # noqa: BLE001 - shown; the dashboard goes on
            return Outcome(False, f"did not start: could not read the machine: "
                                  f"{type(exc).__name__}: {exc}; nothing ran")
        starter: Starter = self.starter  # type: ignore[assignment]
        try:
            again, why = starter.again(document, key, prepared["label"], prepared["name"],
                                       prepared["prompt"])
            if again is None:
                return Outcome(False, f"did not start: {why}")
            return Outcome(True, f"started as {again['name']}\n{starter.launch(again)}")
        except InvariantError as exc:
            return Outcome(False, f"did not start: {exc}")
        except Exception as exc:  # noqa: BLE001 - shown; the dashboard goes on
            return Outcome(False, f"did not start: {type(exc).__name__}: {exc}")


def _cells(line: Line) -> tuple[Text, ...]:
    style = TONE_STYLES[line.tone]
    signals = Text("; ").join(Text(text, style=TONE_STYLES[tone]) for text, tone in line.signals)
    return (
        Text(line.project),
        Text(line.subject, style=style),
        Text(line.stage),
        Text(line.status, style=style),
        signals,
        Text(line.next, style="dim" if line.nested else ""),
    )
