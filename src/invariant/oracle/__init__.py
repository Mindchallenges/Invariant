"""The Session Oracle: a machine-wide, read-only view of every session.

A layer of its own, above ``domains/`` and beside ``cli/``. Nothing in
``core/``, ``domains/`` or ``adapters/`` imports it, so the engine works the
same whether or not the oracle is ever used (``docs/architecture/oracle.md``).
"""
