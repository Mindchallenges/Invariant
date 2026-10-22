# Uninstall and upgrade

## Upgrade

```bash
uv tool upgrade invariant              # or: pipx upgrade invariant
cd <project> && invariant doctor
```

The shims never change — all logic lives in the library, which is why they
are three lines — so an upgrade needs no file in your project rewritten.
`doctor` tells you when the installed agents are from an older release
(`library agents current: FAIL`); `invariant agents install` rewrites those
and skips, by name, any you have edited.

## Remove it from a project

```bash
invariant uninstall --dry-run
invariant uninstall
```

The inverse of `init`. It removes the seven shims and unwires exactly the
settings entries the library added — it recognises them by the shim path,
the same test `init` uses to avoid wiring twice — and leaves everything else
in `.claude/settings.json` as it found it. Malformed JSON is refused rather
than overwritten. It then **says what it left behind**:

- sessions and their state root (machine-local, gitignored; delete the
  directory if you want it gone)
- work items, memories and architecture docs — your data, never touched
- the manifest, unless `--remove-manifest`
- the library agents, unless `--remove-assets`; even then an agent you have
  edited survives

Afterwards `invariant doctor` on that project reports *not installed* and
names `invariant init` as the way back, rather than listing every shim as
missing.

## Remove it from the machine

```bash
uv tool uninstall invariant            # or: pipx uninstall invariant
```

A project you forgot to `uninstall` is harmless: its shims find no CLI and
exit silently with an empty verdict. Nothing in the project's own tooling
depends on the binary being present.
