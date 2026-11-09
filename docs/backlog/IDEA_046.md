---
id: IDEA_046
type: idea
title: The dart adapter names failing tests and reads a dart test run's scope
status: proposed
residue: []
history: []
priority: low
effort: S
area: adapters/dart
---

**Context:** ADR-019 D7 left three adapter questions at the base answer for `dart` and `flutter`: `failing_test_ids` (the sweep files one `suite:red` for a red run instead of one key per test), `test_scope_of` (only the bare template is a full run; any extra token is "cannot tell" for `sweep.no_full_run`), and `expected_failures_are_strict` (cannot tell).

**Proposal:** read the reporter's "Failing tests:" list (`<file>: <test name>`) into selectors `commands.test_one` takes back, for example `<file> --plain-name "<name>"`, and only when the template is known to accept that shape. Read `dart test` / `flutter test` narrowing arguments (a positional path, `--name`/`-n`, `--plain-name`/`-N`, `--tags`/`-t`, `--exclude-tags`/`-x`) as scoped, and known non-narrowing ones (`--reporter`, `--coverage-path`, `--coverage`, `-j`) as leaving the run full.

**Acceptance:**

* A sweep over a red dart run files one key per failing test, each re-runnable alone through `commands.test_one`.
* `sweep.no_full_run` treats `dart test --coverage-path=x` as full and `dart test test/a_test.dart` as scoped; anything unread stays "cannot tell".

**Risks / open questions:**

* A test name holding quotes or regex characters must survive the shell and `--plain-name` exactly.
* A project whose `test_one` runs a script taking package directories (not test files) cannot take a per-test selector; the answer must then be `None`.

**Why now / why this tier:** Low: only matters once a Dart project runs a sweep cadence. Filed so ADR-019 D7 is on record.

**Related:** ADR-019, ADR-010 D5, IDEA_036.
