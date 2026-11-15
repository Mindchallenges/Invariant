"""A questionnaire run: asking the configuration through a Prompter (ADR-017 D2, D3).

`core/questionnaire` owns the catalogue and everything about it that is not
asking: each question's default and provenance, the check of an answer, the
level an answer goes to and the write plan. This module asks. The level once,
then each section in catalogue order, each question shown with its default
and where that comes from; every answer is checked as the configuration it
produces and asked again, with the manifest's reason, when refused. A step
the engine ships is asked by code: the `commands` step here, the `docs` step
by its caller, which hands it in as a callable (it is `cli/onboarding`'s
offers, and its writes belong to `init`).

Nothing is written while asking. :func:`write_user` applies the user level's
share of a plan through `core/home.write_user_defaults`; the caller applies
the rest.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from ..core import questionnaire
from ..core.home import write_user_defaults
from ..core.manifest import Manifest
from ..domains.docs import setting_lines
from .prompts import Prompter

#: What each level is, as the level question offers it (D4).
LEVEL_DESCRIPTIONS = {
    "user": "defaults: in ~/.invariant/config.yaml, for every project on this machine",
    "store": "the store's own level, shared by every repository it serves",
    "store-repo": "this repository's block in its store",
    "repo": "this project's own manifest",
}

#: The level question.
LEVEL_QUESTION = "Which level do these answers go to?"

#: The commands step's one question (D1: "change any?", default no).
COMMANDS_QUESTION = "Commands: change any of them?"


@dataclass
class Asked:
    """A run's answers so far, and the draft of the levels they make."""

    level: str
    draft: questionnaire.Draft
    answers: list[questionnaire.Answer] = field(default_factory=list)
    #: Every redirect said while asking (D4).
    notes: list[str] = field(default_factory=list)

    def value(self, key: str, fallback: Any = None) -> Any:
        """The last answer given for ``key``, else ``fallback``."""
        for answer in reversed(self.answers):
            if answer.key == key:
                return answer.value
        return fallback

    def keep(self, answer: questionnaire.Answer) -> None:
        self.answers.append(answer)
        self.draft = self.draft.placed(answer.key, answer.value, answer.level)


def typed(value: Any) -> str:
    """``value`` as a person would type it back: a list comma-separated, a
    bool yes or no, nothing for none."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return questionnaire.shown(value)


def heading(question: questionnaire.Question) -> str:
    """The question as asked: its prompt, then its default and provenance."""
    return (
        f"{question.prompt} (now {questionnaire.shown(question.default)}, "
        f"from {question.provenance})"
    )


def ask_level(prompter: Prompter, manifest: Manifest) -> str:
    """D3.1: the level the answers go to, offered as the project has them."""
    options = [(t, LEVEL_DESCRIPTIONS[t]) for t in questionnaire.targets(manifest)]
    return prompter.choose(LEVEL_QUESTION, options, questionnaire.default_target(manifest))


def ask_sections(
    prompter: Prompter,
    catalogue: questionnaire.Catalogue,
    asked: Asked,
    sections: Iterable[str],
    *,
    given: Mapping[str, tuple[Any, str]] | None = None,
    prefill: Mapping[str, tuple[Any, str]] | None = None,
    steps: Mapping[str, Callable[[Asked], None]] | None = None,
) -> Asked:
    """D3.2: ask ``sections`` in order, into ``asked``.

    ``given`` maps a key to an answer a flag already gave and the flag's
    name: it is not asked, and is checked as a typed answer is; a refusal is
    raised as :class:`questionnaire.Refused`. ``prefill`` maps a key to a
    default the configuration does not resolve by itself and its provenance
    (D10). ``steps`` are the handlers of the steps the caller asks itself.
    """
    given = given or {}
    prefill = prefill or {}
    steps = steps or {}
    for sid in sections:
        section = catalogue.sections[sid]
        prompter.say(f"-- {section.title}")
        for entry in section.questions:
            if entry.kind == "step" and entry.id in steps:
                steps[entry.id](asked)
                continue
            questions = questionnaire.resolve_entry(
                asked.draft.manifest(), asked.level, sid, entry
            )
            if entry.kind == "step" and entry.id == "commands":
                _commands(prompter, questions, asked)
                continue
            for question in questions:
                if question.key is None:
                    continue
                if question.key in prefill:
                    value, label = prefill[question.key]
                    question = replace(question, default=value, provenance=label)
                if question.key in given:
                    _given(prompter, question, asked, *given[question.key])
                else:
                    _ask(prompter, question, asked)
    return asked


def _destination(prompter: Prompter, question: questionnaire.Question, asked: Asked) -> str:
    level, note = questionnaire.route(
        str(question.key), asked.level, store_project=asked.draft.base.store is not None
    )
    if note is not None:
        prompter.say(note)
        asked.notes.append(note)
    return level


def _given(
    prompter: Prompter,
    question: questionnaire.Question,
    asked: Asked,
    value: Any,
    flag: str,
) -> None:
    level = _destination(prompter, question, asked)
    reason = questionnaire.check(question, value, asked.draft, level)
    if reason is not None:
        raise questionnaire.Refused(f"{flag} {typed(value)}: {reason}")
    prompter.say(f"{question.key}: {questionnaire.shown(value)} (given by {flag})")
    asked.keep(questionnaire.Answer(question.key, value, level, question.default))


def _ask(prompter: Prompter, question: questionnaire.Question, asked: Asked) -> None:
    """One question, asked until its answer is accepted (D6).

    The default shown is accepted as it is: it is what the configuration
    resolves to now, and accepting it writes nothing (ruling 4). Judging it
    again would ask forever after a default that fails a check load does not
    make, a hand-written command of the wrong shape (ruling 13), at a
    terminal whose end of input answers every question with its default.
    """
    level = _destination(prompter, question, asked)
    while True:
        try:
            value = _read(prompter, question)
        except questionnaire.Refused as exc:
            prompter.say(f"refused: {exc}")
            continue
        if value == question.default:
            break
        reason = questionnaire.check(question, value, asked.draft, level)
        if reason is None:
            break
        prompter.say(f"refused: {reason}")
    asked.keep(questionnaire.Answer(str(question.key), value, level, question.default))


def _read(prompter: Prompter, question: questionnaire.Question) -> Any:
    """The answer to ``question``: a choice chosen, any other kind typed."""
    if question.kind == "choice" and question.choices:
        keys = [str(c) for c in question.choices]
        default = str(question.default) if str(question.default) in keys else keys[0]
        return prompter.choose(heading(question), [(k, "") for k in keys], default)
    if question.kind == "bool":
        options = [("yes", ""), ("no", "")]
        return prompter.choose(heading(question), options, typed(bool(question.default))) == "yes"
    text = prompter.ask(heading(question), typed(question.default))
    return questionnaire.parse_answer(question, text)


def _commands(
    prompter: Prompter, questions: list[questionnaire.Question], asked: Asked
) -> None:
    """The commands step (D1): every command of every declared ecosystem in
    one table with its default and provenance, then "change any?", default
    no; yes walks them one by one. Empty keeps a command's default. A
    project declares at least one ecosystem, so the table is never empty."""
    width = max(len(str(q.key)) for q in questions)
    values = [questionnaire.shown(q.default) for q in questions]
    value_width = max(len(v) for v in values)
    prompter.say(
        "\n".join(
            [
                "commands (default, and where it comes from):",
                *(
                    f"  {q.key:<{width}}  {v:<{value_width}}  {q.provenance}"
                    for q, v in zip(questions, values, strict=True)
                ),
            ]
        )
    )
    change = prompter.choose(
        COMMANDS_QUESTION,
        [("no", "keep them"), ("yes", "walk them one by one")],
        "no",
    )
    if change != "yes":
        return
    for question in questions:
        _ask(prompter, replace(question, prompt=str(question.key)), asked)


def say_notes(prompter: Prompter, notes: Iterable[str]) -> list[str]:
    """Say each plan note (ruling 12) and return them for the report."""
    said = list(notes)
    for note in said:
        prompter.say(note)
    return said


@dataclass(frozen=True)
class Applied:
    """What a level writer did: the action line, and the held file, if any,
    as ``{"path", "reason", "yaml"}`` to add by hand (D5)."""

    action: str | None
    by_hand: dict[str, str] | None = None


def write_user(write: questionnaire.LevelWrite) -> Applied:
    """The user level's share of a plan, through `write_user_defaults`."""
    result = write_user_defaults(write.sets, write.removes)
    if not result.written:
        return Applied(
            action=None,
            by_hand={
                "path": str(result.path),
                "reason": result.reason,
                "yaml": result.by_hand or "",
            },
        )
    changed = [*setting_lines(write.sets), *(f"remove {key}" for key in write.removes)]
    return Applied(action=f"write {', '.join(changed)} to the user level's defaults ({result.path})")


def by_hand_lines(by_hand: Mapping[str, str]) -> list[str]:
    """What a run prints for a user level file it would not rewrite."""
    return [
        f"The user level's defaults were not written: {by_hand['reason']}.",
        f"Add these by hand to {by_hand['path']}, merging them into its defaults block:",
        "",
        by_hand["yaml"].rstrip("\n"),
    ]
