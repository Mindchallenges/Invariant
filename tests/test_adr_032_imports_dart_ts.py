"""ADR-032 increment 5: the ``imports`` engine for Dart and TypeScript (D3).

``adapters/dart`` and ``adapters/node`` answer ``references`` through the
seam the Python adapter answers (``Adapter.references``), and list ``imports``
in ``guideline_engines``. Dart reads its ``import``/``export``/``part``
directives and resolves ``package:`` names through the project's own
``pubspec.yaml`` names into their ``lib/``, and relative URIs from the file.
TypeScript and JavaScript read ``import``/``export ... from``/``require``/
dynamic ``import()`` with a string literal, resolved through relative
specifiers and ``tsconfig.json``'s ``baseUrl`` and ``paths``, with
TypeScript's extension and ``index`` rules. An import that resolves outside
the project is outside the kind (DN-A2); one that cannot be resolved makes the
file unanswerable, never clean (DEC_ADR_032_I5).

The deliverable tests are :class:`TestDeliverableDart` and
:class:`TestDeliverableTypeScript`: a Use Cases file importing an outer layer
is a ``guidelines.mechanical_clean`` finding through ``package:``, ``tsconfig``
paths and relative specifiers; a third-party import is not; an unresolvable
one is unanswerable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, write

from invariant.adapters import get_adapter
from invariant.adapters.base import Reference
from invariant.core.rules import RuleContext, _offered_engines, evaluate

DART = get_adapter("dart")
FLUTTER = get_adapter("flutter")
NODE = get_adapter("node")


def put(root: Path, relative: str, text: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def dart_targets(root: Path, relative: str) -> list[str] | None:
    answer = DART.references(root / relative, Path(relative), root)
    return None if answer is None else [ref.target for ref in answer]


def ts_targets(root: Path, relative: str) -> list[str] | None:
    answer = NODE.references(root / relative, Path(relative), root)
    return None if answer is None else [ref.target for ref in answer]


# -- the seam ---------------------------------------------------------------------------


class TestTheSeam:
    @pytest.mark.parametrize("adapter", [DART, FLUTTER, NODE])
    def test_dart_flutter_and_node_offer_imports(self, adapter):
        assert "imports" in adapter.guideline_engines

    def test_node_and_dart_offer_imports_without_python(self):
        assert _offered_engines([NODE]) == frozenset({"imports"})
        assert _offered_engines([DART]) == frozenset({"imports"})

    def test_each_answers_only_for_its_own_language(self, tmp_path):
        put(tmp_path, "a.py", "import b\n")
        assert DART.references(tmp_path / "a.py", Path("a.py"), tmp_path) is None
        assert NODE.references(tmp_path / "a.py", Path("a.py"), tmp_path) is None
        put(tmp_path, "a.ts", "import './b';\n")
        assert DART.references(tmp_path / "a.ts", Path("a.ts"), tmp_path) is None
        put(tmp_path, "a.dart", "import 'b.dart';\n")
        assert NODE.references(tmp_path / "a.dart", Path("a.dart"), tmp_path) is None

    @pytest.mark.parametrize(("adapter", "name"), [(DART, "a.dart"), (NODE, "a.ts")])
    def test_an_unreadable_file_answers_none(self, tmp_path, adapter, name):
        assert adapter.references(tmp_path / name, Path(name), tmp_path) is None

    @pytest.mark.parametrize(("adapter", "name"), [(DART, "a.dart"), (NODE, "a.ts")])
    def test_a_file_that_is_not_utf8_answers_none(self, tmp_path, adapter, name):
        (tmp_path / name).write_bytes(b"var x = '\xff';\n")
        assert adapter.references(tmp_path / name, Path(name), tmp_path) is None


# -- Dart -------------------------------------------------------------------------------------


@pytest.fixture
def dart_app(tmp_path: Path) -> Path:
    put(tmp_path, "pubspec.yaml", "name: app\nenvironment:\n  sdk: ^3.0.0\n")
    put(tmp_path, "lib/domain/user.dart", "class User {}\n")
    put(tmp_path, "lib/data/db.dart", "class Db {}\n")
    put(tmp_path, "lib/usecases/login.dart", "")
    return tmp_path


class TestDartDirectives:
    def test_a_reference_carries_target_line_and_text(self, dart_app):
        put(dart_app, "lib/usecases/login.dart",
            "// a comment naming import 'x.dart';\n\nimport 'package:app/domain/user.dart';\n")
        answer = DART.references(
            dart_app / "lib/usecases/login.dart", Path("lib/usecases/login.dart"), dart_app
        )
        assert answer == [Reference(
            "lib/domain/user.dart", 3, "import 'package:app/domain/user.dart';"
        )]

    def test_import_export_and_part_count_and_part_of_does_not(self, dart_app):
        put(dart_app, "lib/usecases/login.g.dart", "part of 'login.dart';\n")
        put(dart_app, "lib/usecases/login.dart", (
            "library login;\n"
            "import '../domain/user.dart' show User;\n"
            'export "package:app/data/db.dart" hide Db;\n'
            "part 'login.g.dart';\n"
            "class Login {}\n"
        ))
        assert dart_targets(dart_app, "lib/usecases/login.dart") == [
            "lib/domain/user.dart", "lib/data/db.dart", "lib/usecases/login.g.dart",
        ]
        assert dart_targets(dart_app, "lib/usecases/login.g.dart") == []

    def test_every_uri_of_a_conditional_import_counts(self, dart_app):
        put(dart_app, "lib/usecases/login.dart", (
            "import 'package:app/domain/user.dart'\n"
            "    if (dart.library.io) 'package:app/data/db.dart';\n"
        ))
        assert dart_targets(dart_app, "lib/usecases/login.dart") == [
            "lib/domain/user.dart", "lib/data/db.dart",
        ]

    def test_annotations_raw_and_triple_quoted_uris_and_adjacent_strings(self, dart_app):
        put(dart_app, "lib/usecases/login.dart", (
            "/* a /* nested */ block */\n"
            "@deprecated\n"
            "@Annotation(1, 'x')\n"
            "library;\n"
            "import r'../domain/user.dart' as u;\n"
            "import '''package:app/data/db.dart''';\n"
            "import 'package:app/' 'domain/user.dart' deferred as v;\n"
        ))
        assert dart_targets(dart_app, "lib/usecases/login.dart") == [
            "lib/domain/user.dart", "lib/data/db.dart", "lib/domain/user.dart",
        ]

    def test_directives_end_at_the_first_declaration(self, dart_app):
        put(dart_app, "lib/usecases/login.dart", (
            "import 'package:app/domain/user.dart';\n"
            "const s = \"import 'package:app/data/db.dart';\";\n"
            "void main() { var import = 1; }\n"
        ))
        assert dart_targets(dart_app, "lib/usecases/login.dart") == ["lib/domain/user.dart"]

    def test_no_directive_is_an_empty_answer(self, dart_app):
        put(dart_app, "lib/usecases/login.dart", "class Login {}\n")
        assert dart_targets(dart_app, "lib/usecases/login.dart") == []

    @pytest.mark.parametrize("text", [
        "import 'package:app/${x}.dart';\n",
        "/* never closed\nimport 'a.dart';\n",
        "import 'never closed;\n",
        "import 'package:app/domain/user.dart'\n",
        "import foo;\n",
    ])
    def test_a_directive_the_reader_cannot_finish_is_unanswerable(self, dart_app, text):
        put(dart_app, "lib/usecases/login.dart", text)
        assert dart_targets(dart_app, "lib/usecases/login.dart") is None


class TestDartEdges:
    def test_a_shebang_a_bom_and_a_dotted_part_of(self, dart_app):
        put(dart_app, "lib/usecases/login.dart", (
            "\ufeff#!/usr/bin/env dart\n"
            "@pkg.Meta(\"(\")\n"
            "library a.b;\n"
            "import 'package:app/domain/user.dart' show A hide B;\n"
        ))
        assert dart_targets(dart_app, "lib/usecases/login.dart") == ["lib/domain/user.dart"]
        put(dart_app, "lib/usecases/login.dart", "part of app.usecases;\n")
        assert dart_targets(dart_app, "lib/usecases/login.dart") == []

    @pytest.mark.parametrize("text", [
        "@a(\nimport 'a.dart';\n",
        "@\n",
        "@a.\n",
        "library x\n",
        "import 'package:app/domain/user.dart' if (dart.library.io\n",
        "import 'package:app/';\n",
    ])
    def test_more_that_the_reader_cannot_finish(self, dart_app, text):
        put(dart_app, "lib/usecases/login.dart", text)
        assert dart_targets(dart_app, "lib/usecases/login.dart") is None

    def test_an_unreadable_pubspec_names_no_package(self, dart_app):
        (dart_app / "other").mkdir()
        (dart_app / "other" / "pubspec.yaml").write_bytes(b"name: \xff\n")
        put(dart_app, "lib/usecases/login.dart", "import 'package:app/domain/user.dart';\n")
        assert dart_targets(dart_app, "lib/usecases/login.dart") == ["lib/domain/user.dart"]


class TestDartResolution:
    def test_dart_and_third_party_packages_are_outside_the_project(self, dart_app):
        put(dart_app, "lib/usecases/login.dart", (
            "import 'dart:async';\n"
            "import 'package:http/http.dart' as http;\n"
            "import 'package:app/domain/user.dart';\n"
        ))
        assert dart_targets(dart_app, "lib/usecases/login.dart") == ["lib/domain/user.dart"]

    def test_a_workspace_member_resolves_into_its_own_lib(self, tmp_path):
        put(tmp_path, "pubspec.yaml", "name: root\nworkspace:\n  - packages/core\n")
        put(tmp_path, "packages/core/pubspec.yaml", "name: 'core'\n")
        put(tmp_path, "packages/core/lib/src/model.dart", "class M {}\n")
        put(tmp_path, "packages/app/pubspec.yaml", 'name: "app" # the app\n')
        put(tmp_path, "packages/app/lib/main.dart", "import 'package:core/src/model.dart';\n")
        put(tmp_path, ".dart_tool/pubspec.yaml", "name: core\n")
        put(tmp_path, "build/pubspec.yaml", "name: core\n")
        assert dart_targets(tmp_path, "packages/app/lib/main.dart") == [
            "packages/core/lib/src/model.dart",
        ]

    def test_a_name_two_packages_claim_is_unanswerable(self, tmp_path):
        put(tmp_path, "a/pubspec.yaml", "name: twin\n")
        put(tmp_path, "b/pubspec.yaml", "name: twin\n")
        put(tmp_path, "a/lib/x.dart", "")
        put(tmp_path, "b/lib/main.dart", "import 'package:twin/x.dart';\n")
        assert dart_targets(tmp_path, "b/lib/main.dart") is None

    def test_a_project_target_that_is_no_file_is_unanswerable(self, dart_app):
        put(dart_app, "lib/usecases/login.dart", "import 'package:app/gone.dart';\n")
        assert dart_targets(dart_app, "lib/usecases/login.dart") is None
        put(dart_app, "lib/usecases/login.dart", "part 'login.g.dart';\n")
        assert dart_targets(dart_app, "lib/usecases/login.dart") is None

    def test_a_relative_uri_leaving_the_project_is_outside_it(self, dart_app):
        put(dart_app, "lib/usecases/login.dart", "import '../../../elsewhere/x.dart';\n")
        assert dart_targets(dart_app, "lib/usecases/login.dart") == []

    def test_another_scheme_is_unanswerable(self, dart_app):
        put(dart_app, "lib/usecases/login.dart", "import 'http://example.invalid/x.dart';\n")
        assert dart_targets(dart_app, "lib/usecases/login.dart") is None

    def test_a_pubspec_with_no_name_names_no_package(self, tmp_path):
        put(tmp_path, "pubspec.yaml", "description: nameless\n")
        put(tmp_path, "lib/a.dart", "import 'package:nameless/b.dart';\n")
        assert dart_targets(tmp_path, "lib/a.dart") == []


# -- TypeScript and JavaScript ---------------------------------------------------------------------


@pytest.fixture
def ts_app(tmp_path: Path) -> Path:
    put(tmp_path, "package.json", "{}\n")
    put(tmp_path, "src/domain/user.ts", "export class User {}\n")
    put(tmp_path, "src/adapters/db.ts", "export class Db {}\n")
    put(tmp_path, "src/adapters/index.ts", "export * from './db';\n")
    put(tmp_path, "src/usecases/login.ts", "")
    return tmp_path


class TestTypeScriptForms:
    def test_a_reference_carries_target_line_and_text(self, ts_app):
        put(ts_app, "src/usecases/login.ts",
            "// import '../adapters/db';\n\nimport { User } from '../domain/user';\n")
        answer = NODE.references(
            ts_app / "src/usecases/login.ts", Path("src/usecases/login.ts"), ts_app
        )
        assert answer == [Reference(
            "src/domain/user.ts", 3, "import { User } from '../domain/user';"
        )]

    def test_every_form_counts_at_any_depth(self, ts_app):
        put(ts_app, "src/usecases/login.ts", (
            "import '../domain/user';\n"
            "import Def, { a as b, type C } from \"../adapters/db\";\n"
            "import * as ns from '../adapters';\n"
            "import type { T } from '../domain/user';\n"
            "export { x } from '../adapters/db';\n"
            "export * as all from '../domain/user';\n"
            "export type { Y } from '../domain/user';\n"
            "function f() {\n"
            "  const m = require('../adapters/db');\n"
            "  return import(`../domain/user`);\n"
            "}\n"
            "import fs = require('../adapters/db');\n"
        ))
        assert ts_targets(ts_app, "src/usecases/login.ts") == [
            "src/domain/user.ts", "src/adapters/db.ts", "src/adapters/index.ts",
            "src/domain/user.ts", "src/adapters/db.ts", "src/domain/user.ts",
            "src/domain/user.ts", "src/adapters/db.ts", "src/domain/user.ts",
            "src/adapters/db.ts",
        ]

    def test_what_is_not_a_reference(self, ts_app):
        put(ts_app, "src/usecases/login.ts", (
            "const s = 'import x from \"../adapters/db\"';\n"
            "/* require('../adapters/db') */\n"
            "const t = `${require}`;\n"
            "const u = import.meta.url;\n"
            "loader.require('../adapters/db');\n"
            "const v = { import: 1, require: 2 };\n"
            "const name = '../adapters/db';\n"
            "require(name);\n"
            "import(`../adapters/${name}`);\n"
            "export const from = 1;\n"
            "export default function () {}\n"
            "const r = /import '..\\/adapters\\/db'/g;\n"
            "const d = a / b / c;\n"
        ))
        assert ts_targets(ts_app, "src/usecases/login.ts") == []

    def test_template_substitutions_nest(self, ts_app):
        put(ts_app, "src/usecases/login.ts", (
            "const a = `x ${ `y ${ { k: '}' }.k } z` } w`;\n"
            "import { User } from '../domain/user';\n"
        ))
        assert ts_targets(ts_app, "src/usecases/login.ts") == ["src/domain/user.ts"]

    def test_a_regex_after_a_keyword_and_division_after_a_value(self, ts_app):
        put(ts_app, "src/usecases/login.ts", (
            "function f(x) { return /'/.test(x); }\n"
            "const half = (1 + 2) / 2; const q = arr[0] / 2; let i = 0; i++ / 2;\n"
            "const re = /[/]'/;\n"
            "import { User } from '../domain/user';\n"
        ))
        assert ts_targets(ts_app, "src/usecases/login.ts") == ["src/domain/user.ts"]

    def test_jsx_text_attributes_and_expressions(self, ts_app):
        put(ts_app, "src/ui/view.tsx", (
            "import { User } from '../domain/user';\n"
            "const Generic = <T,>(x: T) => x;\n"
            "export const V = () => (\n"
            "  <div title=\"it's\" data-x='a\"b' {...props}>\n"
            "    Don't {/* a comment */} stop <b>now</b> {cond ? <i/> : null}\n"
            "    <>{require('../adapters/db')}</>\n"
            "  </div>\n"
            ");\n"
        ))
        assert ts_targets(ts_app, "src/ui/view.tsx") == [
            "src/domain/user.ts", "src/adapters/db.ts",
        ]

    def test_a_shebang_and_a_hash_private_name(self, ts_app):
        put(ts_app, "src/usecases/login.js", (
            "#!/usr/bin/env node\n"
            "class A { #x = 1; m() { return this.#x / 2; } }\n"
            "const { User } = require('../domain/user');\n"
        ))
        assert ts_targets(ts_app, "src/usecases/login.js") == ["src/domain/user.ts"]

    @pytest.mark.parametrize("text", [
        "const s = 'never closed;\nimport x from '../domain/user';\n",
        "/* never closed\n",
        "const t = `never closed;\n",
        "const r = /never closed\n",
        "const t = `${ never closed`;\n",
    ])
    def test_a_file_the_lexer_cannot_finish_is_unanswerable(self, ts_app, text):
        put(ts_app, "src/usecases/login.ts", text)
        assert ts_targets(ts_app, "src/usecases/login.ts") is None

    @pytest.mark.parametrize("text", [
        "const V = () => <div>never closed\n",
        "const V = () => <div title=\"x\" =>;</div>\n",
        "const V = () => <div>{'x'</div>\n",
    ])
    def test_jsx_the_lexer_cannot_finish_is_unanswerable(self, ts_app, text):
        put(ts_app, "src/ui/view.tsx", text)
        assert ts_targets(ts_app, "src/ui/view.tsx") is None

    def test_no_import_is_an_empty_answer(self, ts_app):
        put(ts_app, "src/usecases/login.ts", "export const x = 1;\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") == []


class TestTypeScriptEdges:
    def test_jsx_attribute_forms_and_comments_in_a_tag(self, ts_app):
        put(ts_app, "src/ui/view.jsx", (
            "const V = () => <input disabled /* why */ // more\n"
            "  label=<b>x</b> value={require('../domain/user')} />;\n"
        ))
        assert ts_targets(ts_app, "src/ui/view.jsx") == ["src/domain/user.ts"]

    @pytest.mark.parametrize("text", [
        "const V = () => <a b=c />;\n",
        "const V = () => <a b=\"x />;\n",
        "const V = () => <a></a\n",
    ])
    def test_more_jsx_the_lexer_cannot_finish(self, ts_app, text):
        put(ts_app, "src/ui/view.tsx", text)
        assert ts_targets(ts_app, "src/ui/view.tsx") is None

    def test_a_comparison_is_not_jsx_and_a_generic_with_extends_is_not_either(self, ts_app):
        put(ts_app, "src/ui/view.tsx", (
            "const a = b < c;\n"
            "const f = <T extends object>(x: T) => x;\n"
            "import '../domain/user';\n"
        ))
        assert ts_targets(ts_app, "src/ui/view.tsx") == ["src/domain/user.ts"]

    def test_a_trailing_keyword_and_an_unfinished_call_are_not_references(self, ts_app):
        put(ts_app, "src/usecases/login.ts", "const n = 1.5e3 + .5;\nrequire(\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") == []
        put(ts_app, "src/usecases/login.ts", "export\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") == []
        put(ts_app, "src/usecases/login.ts", "import { a } from;\nimport b from x;\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") == []

    def test_escapes_in_strings_and_templates(self, ts_app):
        put(ts_app, "src/usecases/login.ts", (
            "const s = 'a\\\n b';\n"
            "const t = `line\\\n ${1}`;\n"
            "import '..\\/domain/user';\n"
        ))
        assert ts_targets(ts_app, "src/usecases/login.ts") == ["src/domain/user.ts"]

    @pytest.mark.parametrize("config", [
        {"extends": "./missing.json"},
        {"extends": "./tsconfig.json"},
        {"extends": "./bad.json"},
    ])
    def test_an_extends_chain_that_does_not_read_is_unanswerable(self, ts_app, config):
        put(ts_app, "bad.json", "[1, 2]")
        put(ts_app, "tsconfig.json", json.dumps(config))
        put(ts_app, "src/usecases/login.ts", "import r from 'react';\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") is None

    def test_an_extends_without_a_suffix_and_options_that_are_not_a_mapping(self, ts_app):
        put(ts_app, "base.json", json.dumps({"compilerOptions": {"baseUrl": "src"}}))
        put(ts_app, "tsconfig.json", json.dumps({"extends": "./base"}))
        put(ts_app, "src/usecases/login.ts", "import x from 'domain/user';\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") == ["src/domain/user.ts"]
        put(ts_app, "tsconfig.json", json.dumps({"compilerOptions": "none"}))
        assert ts_targets(ts_app, "src/usecases/login.ts") == []

    def test_a_substitution_that_is_not_text_is_skipped(self, ts_app):
        put(ts_app, "tsconfig.json", json.dumps(
            {"compilerOptions": {"paths": {"@/*": [7, "./src/*"]}}}
        ))
        put(ts_app, "src/usecases/login.ts", "import x from '@/domain/user';\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") == ["src/domain/user.ts"]

    def test_a_config_with_comments_strings_and_an_unclosed_comment(self, ts_app):
        put(ts_app, "tsconfig.json", (
            '{"compilerOptions": {"baseUrl": "./src", "x": "a // b \\" /* c */"}}'
            "/* never closed"
        ))
        put(ts_app, "src/usecases/login.ts", "import x from 'domain/user';\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") == ["src/domain/user.ts"]

    def test_an_unreadable_config_is_unanswerable(self, ts_app):
        (ts_app / "tsconfig.json").write_bytes(b"\xff")
        put(ts_app, "src/usecases/login.ts", "import r from 'react';\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") is None


class TestTypeScriptResolution:
    def test_extensions_and_index(self, ts_app):
        put(ts_app, "src/domain/types.d.ts", "export type T = 1;\n")
        put(ts_app, "src/domain/view.tsx", "export const V = 1;\n")
        put(ts_app, "src/domain/legacy.js", "module.exports = 1;\n")
        put(ts_app, "src/domain/esm.mts", "export const E = 1;\n")
        put(ts_app, "src/domain/logo.svg", "<svg/>\n")
        put(ts_app, "src/usecases/login.ts", (
            "import './../domain/user.js';\n"
            "import '../domain/types';\n"
            "import '../domain/view.jsx';\n"
            "import '../domain/legacy';\n"
            "import '../domain/esm.mjs';\n"
            "import '../domain/logo.svg';\n"
            "import '../adapters';\n"
            "import '..';\n"
        ))
        put(ts_app, "src/index.ts", "")
        assert ts_targets(ts_app, "src/usecases/login.ts") == [
            "src/domain/user.ts", "src/domain/types.d.ts", "src/domain/view.tsx",
            "src/domain/legacy.js", "src/domain/esm.mts", "src/domain/logo.svg",
            "src/adapters/index.ts", "src/index.ts",
        ]

    def test_bare_specifiers_and_node_builtins_are_outside_the_project(self, ts_app):
        put(ts_app, "src/usecases/login.ts", (
            "import React from 'react';\n"
            "import { z } from '@scope/lib/sub';\n"
            "import fs from 'node:fs';\n"
            "const p = require('path');\n"
        ))
        assert ts_targets(ts_app, "src/usecases/login.ts") == []

    def test_a_relative_specifier_resolving_nowhere_is_unanswerable(self, ts_app):
        put(ts_app, "src/usecases/login.ts", "import x from '../adapters/gone';\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") is None

    def test_a_relative_specifier_leaving_the_project_is_outside_it(self, ts_app):
        put(ts_app, "src/usecases/login.ts", "import x from '../../../elsewhere/x';\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") == []

    def test_tsconfig_paths_by_the_longest_prefix_from_base_url(self, ts_app):
        put(ts_app, "tsconfig.json", (
            "{\n"
            "  // comments and trailing commas are allowed\n"
            '  "compilerOptions": {\n'
            '    "baseUrl": "./src",\n'
            '    "paths": {\n'
            '      "@/*": ["missing/*", "*"],\n'
            '      "@/adapters/*": ["adapters/*",],\n'
            '      "~db": ["adapters/db.ts"],\n'
            "    },\n"
            "  },\n"
            "}\n"
        ))
        put(ts_app, "src/usecases/login.ts", (
            "import { User } from '@/domain/user';\n"
            "import { Db } from '@/adapters/db';\n"
            "import db from '~db';\n"
            "import x from 'domain/user';\n"
            "import r from 'react';\n"
        ))
        assert ts_targets(ts_app, "src/usecases/login.ts") == [
            "src/domain/user.ts", "src/adapters/db.ts", "src/adapters/db.ts",
            "src/domain/user.ts",
        ]

    def test_paths_without_base_url_resolve_from_the_config_folder(self, ts_app):
        put(ts_app, "tsconfig.json", json.dumps(
            {"compilerOptions": {"paths": {"@app/*": ["./src/*"]}}}
        ))
        put(ts_app, "src/usecases/login.ts", "import { Db } from '@app/adapters/db';\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") == ["src/adapters/db.ts"]

    def test_an_alias_resolving_nowhere_is_unanswerable_but_a_catch_all_is_not(self, ts_app):
        put(ts_app, "tsconfig.json", json.dumps({"compilerOptions": {
            "baseUrl": ".", "paths": {"@/*": ["src/*"], "*": ["types/*"]},
        }}))
        put(ts_app, "src/usecases/login.ts", "import r from 'react';\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") == []
        put(ts_app, "src/usecases/login.ts", "import x from '@/gone';\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") is None

    def test_the_extends_chain_is_followed_and_a_package_one_ignored(self, ts_app):
        put(ts_app, "config/base.json", json.dumps({
            "extends": "@tsconfig/node20/tsconfig.json",
            "compilerOptions": {"baseUrl": "../src", "paths": {"#/*": ["*"]}},
        }))
        put(ts_app, "tsconfig.json", json.dumps({"extends": ["./config/base.json"]}))
        put(ts_app, "src/usecases/login.ts", "import { Db } from '#/adapters/db';\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") == ["src/adapters/db.ts"]

    def test_the_nearest_config_wins_and_jsconfig_serves_javascript(self, ts_app):
        put(ts_app, "tsconfig.json", json.dumps(
            {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["nowhere/*"]}}}
        ))
        put(ts_app, "src/jsconfig.json", json.dumps(
            {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["*"]}}}
        ))
        put(ts_app, "src/usecases/login.js", "import { Db } from '@/adapters/db';\n")
        assert ts_targets(ts_app, "src/usecases/login.js") == ["src/adapters/db.ts"]

    def test_a_config_that_does_not_parse_is_unanswerable(self, ts_app):
        put(ts_app, "tsconfig.json", "{ not json")
        put(ts_app, "src/usecases/login.ts", "import r from 'react';\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") is None

    def test_a_relative_import_needs_no_config(self, ts_app):
        put(ts_app, "tsconfig.json", "{ not json")
        put(ts_app, "src/usecases/login.ts", "import { User } from '../domain/user';\n")
        assert ts_targets(ts_app, "src/usecases/login.ts") == ["src/domain/user.ts"]


# -- the deliverable: mechanical_clean over toy trees ------------------------------------------

DART_LAYERS = """\
version: 2
sections: []
rules: []
layers:
  map:
    - id: entities
      title: Entities
      paths: ["lib/domain/**"]
    - id: use-cases
      title: Use Cases
      paths: ["lib/usecases/**"]
    - id: interface-adapters
      title: Interface Adapters
      paths: ["lib/data/**"]
"""

TS_LAYERS = DART_LAYERS.replace("lib/domain/**", "src/domain/**").replace(
    "lib/usecases/**", "src/usecases/**"
).replace("lib/data/**", "src/adapters/**")


def mechanical_clean(root: Path, ecosystem: str, files: list[str]):
    m = git_project(root, {"version": 1, "project": {"name": "demo", "ecosystems": [ecosystem]},
                           "guidelines": {"ruleset": RULESET_REL}})
    ctx = RuleContext(manifest=m, cwd=root, session={"files": files, "base": ""})
    (result,) = evaluate(["guidelines.mechanical_clean"], ctx)
    return result.verdict


class TestDeliverableDart:
    def tree(self, tmp_path: Path, login: str) -> Path:
        root = tmp_path / "proj"
        write(root, RULESET_REL, DART_LAYERS)
        put(root, "pubspec.yaml", "name: app\n")
        put(root, "lib/domain/user.dart", "class User {}\n")
        put(root, "lib/data/db.dart", "class Db {}\n")
        put(root, "lib/usecases/login.dart", login)
        return root

    def test_a_package_import_of_an_outer_layer_is_a_finding(self, tmp_path):
        root = self.tree(tmp_path, "import 'package:app/data/db.dart';\n")
        verdict = mechanical_clean(root, "dart", ["lib/usecases/login.dart"])
        assert not verdict.ok
        assert ("layers.use-cases-depends-inward lib/usecases/login.dart:1 -> "
                "lib/data/db.dart") in verdict.message

    def test_a_relative_import_of_an_outer_layer_is_a_finding(self, tmp_path):
        root = self.tree(tmp_path, "import 'dart:async';\nimport '../data/db.dart';\n")
        verdict = mechanical_clean(root, "flutter", ["lib/usecases/login.dart"])
        assert not verdict.ok
        assert "lib/usecases/login.dart:2 -> lib/data/db.dart" in verdict.message

    def test_an_inward_and_a_third_party_import_are_not(self, tmp_path):
        root = self.tree(tmp_path, (
            "import 'package:app/domain/user.dart';\n"
            "import 'package:http/http.dart';\n"
        ))
        verdict = mechanical_clean(root, "dart", ["lib/usecases/login.dart"])
        assert verdict.ok, verdict.message

    def test_an_unresolvable_import_is_unanswerable(self, tmp_path):
        root = self.tree(tmp_path, "import 'package:app/data/gone.dart';\n")
        verdict = mechanical_clean(root, "dart", ["lib/usecases/login.dart"])
        assert not verdict.ok
        assert "unanswerable" in verdict.message


class TestDeliverableTypeScript:
    def tree(self, tmp_path: Path, login: str) -> Path:
        root = tmp_path / "proj"
        write(root, RULESET_REL, TS_LAYERS)
        put(root, "package.json", "{}\n")
        put(root, "tsconfig.json", json.dumps(
            {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}
        ))
        put(root, "src/domain/user.ts", "export class User {}\n")
        put(root, "src/adapters/db.ts", "export class Db {}\n")
        put(root, "src/usecases/login.ts", login)
        return root

    def test_a_tsconfig_paths_import_of_an_outer_layer_is_a_finding(self, tmp_path):
        root = self.tree(tmp_path, "import { Db } from '@/adapters/db';\n")
        verdict = mechanical_clean(root, "node", ["src/usecases/login.ts"])
        assert not verdict.ok
        assert ("layers.use-cases-depends-inward src/usecases/login.ts:1 -> "
                "src/adapters/db.ts") in verdict.message

    def test_a_relative_import_of_an_outer_layer_is_a_finding(self, tmp_path):
        root = self.tree(tmp_path, "const x = 1;\nconst db = require('../adapters/db');\n")
        verdict = mechanical_clean(root, "node", ["src/usecases/login.ts"])
        assert not verdict.ok
        assert "src/usecases/login.ts:2 -> src/adapters/db.ts" in verdict.message

    def test_an_inward_and_a_third_party_import_are_not(self, tmp_path):
        root = self.tree(tmp_path, (
            "import { User } from '@/domain/user';\nimport React from 'react';\n"
        ))
        verdict = mechanical_clean(root, "node", ["src/usecases/login.ts"])
        assert verdict.ok, verdict.message

    def test_an_unresolvable_import_is_unanswerable(self, tmp_path):
        root = self.tree(tmp_path, "import { Gone } from '@/adapters/gone';\n")
        verdict = mechanical_clean(root, "node", ["src/usecases/login.ts"])
        assert not verdict.ok
        assert "unanswerable" in verdict.message
