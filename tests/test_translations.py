"""Ensure translation files stay in sync with strings.json — and pass hassfest.

The key-parity test catches a translation that drifted; the value tests
reproduce the rules `script/hassfest/translations.py` applies to every
translated string, so a value hassfest would reject fails here instead of in
someone else's CI. Only the rules that apply to *values* are reproduced (the
schema-shape rules are core-only: hassfest skips most of its work for custom
integrations, but the value validators are what a reviewer copies into a
core PR).
"""

from __future__ import annotations

import json
import re
import string
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "airplay_notifier"
)

TRANSLATION_FILES = (
    INTEGRATION_DIR / "strings.json",
    *(INTEGRATION_DIR / "translations" / f"{lang}.json" for lang in ("en", "fr", "es")),
)

# Verbatim from `script/hassfest/translations.py` (Home Assistant 2026.9.1),
# where `string_no_single_quoted_placeholders` rejects any value it matches.
# A placeholder wrapped in single quotes is what blocked the first release:
# `the domain '{domain}' is …`.
RE_PLACEHOLDER_IN_SINGLE_QUOTES = re.compile(r"'{\w+}'")


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Return every dotted key path in a nested translation mapping."""
    flat: dict[str, str] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat |= _flatten(value, path)
        else:
            flat[path] = value
    return flat


def _load(path: Path) -> dict[str, str]:
    return _flatten(json.loads(path.read_text(encoding="utf-8")))


def test_translations_match_strings_reference() -> None:
    """en, fr and es must expose exactly the same keys as strings.json."""
    reference_keys = set(_load(INTEGRATION_DIR / "strings.json"))
    assert reference_keys, "strings.json should not be empty"

    for language in ("en", "fr", "es"):
        translation_keys = set(
            _load(INTEGRATION_DIR / "translations" / f"{language}.json")
        )
        assert translation_keys == reference_keys, (
            f"translations/{language}.json is missing "
            f"{reference_keys - translation_keys} and has extra "
            f"{translation_keys - reference_keys}"
        )


def _single_quoted_placeholders(value: str) -> list[str]:
    """Return the placeholders `value` wraps in single quotes."""
    return RE_PLACEHOLDER_IN_SINGLE_QUOTES.findall(value)


def _placeholders(value: str) -> list[str]:
    """Return the placeholder names in `value`, or raise on malformed braces.

    `string.Formatter().parse` is what hassfest's `validate_placeholders`
    uses; it raises `ValueError` on a stray `{` or `}`, which is how the
    "no braces other than placeholders" rule falls out.
    """
    return [
        field_name
        for _, field_name, _, _ in string.Formatter().parse(value)
        if field_name
    ]


@pytest.mark.parametrize("path", TRANSLATION_FILES, ids=lambda p: p.name)
def test_no_placeholder_inside_single_quotes(path: Path) -> None:
    """hassfest rejects `'{placeholder}'`; quote with « » or not at all.

    This is the exact rule that made the first release un-mergeable:
    `exceptions.source_domain_denied.message` read `the domain '{domain}'
    is …`. Note the regex only looks at ASCII single quotes, so the French
    and Spanish guillemets around the same placeholder are fine.
    """
    for key, value in _load(path).items():
        assert not _single_quoted_placeholders(value), (
            f"{path.name}: {key} wraps "
            f"{_single_quoted_placeholders(value)} in single quotes; hassfest's "
            "string_no_single_quoted_placeholders rejects that"
        )


@pytest.mark.parametrize("path", TRANSLATION_FILES, ids=lambda p: p.name)
def test_placeholders_are_valid_identifiers(path: Path) -> None:
    """Every `{placeholder}` must be a plain `[a-zA-Z_][a-zA-Z0-9_]*` name."""
    for key, value in _load(path).items():
        for placeholder in _placeholders(value):
            assert placeholder.isidentifier(), (
                f"{path.name}: {key} uses placeholder {placeholder!r}, which is "
                "not a valid identifier"
            )


@pytest.mark.parametrize("path", TRANSLATION_FILES, ids=lambda p: p.name)
def test_no_braces_other_than_placeholders(path: Path) -> None:
    """A stray `{` or `}` breaks `str.format` at render time."""
    for key, value in _load(path).items():
        try:
            _placeholders(value)
        except ValueError as err:  # pragma: no cover - only on a broken string
            pytest.fail(f"{path.name}: {key} has malformed braces ({err})")


@pytest.mark.parametrize("path", TRANSLATION_FILES, ids=lambda p: p.name)
def test_translated_placeholders_match_the_reference(path: Path) -> None:
    """A translation may not invent or drop a placeholder.

    A `{deny_domains}` that a translator turned into `{denied_domains}`
    raises `KeyError` when Home Assistant formats the message, at exactly
    the moment the user is being told why their announcement was refused.
    """
    reference = _load(INTEGRATION_DIR / "strings.json")
    for key, value in _load(path).items():
        assert set(_placeholders(value)) == set(_placeholders(reference[key])), (
            f"{path.name}: {key} has placeholders {sorted(_placeholders(value))}, "
            f"reference has {sorted(_placeholders(reference[key]))}"
        )


def test_the_single_quote_rule_is_the_one_hassfest_applies() -> None:
    """Guard the reproduced regex against drifting from core's.

    Every value in the four files was run through the real
    `script.hassfest.translations.validate_translation_value` from
    home-assistant-core 2026.9.1 and accepted; this pins the behaviour that
    check depends on. If it ever fails against a newer core, the rule
    changed and the tests above are checking something core no longer does.
    """
    assert RE_PLACEHOLDER_IN_SINGLE_QUOTES.search("the domain '{domain}' is denied")
    assert RE_PLACEHOLDER_IN_SINGLE_QUOTES.search("'{a}'")
    # Not matched: guillemets, double quotes, quoted literals, bare placeholders.
    assert not RE_PLACEHOLDER_IN_SINGLE_QUOTES.search("the domain « {domain} » is")
    assert not RE_PLACEHOLDER_IN_SINGLE_QUOTES.search("the domain «{domain}» is")
    assert not RE_PLACEHOLDER_IN_SINGLE_QUOTES.search('the domain "{domain}" is')
    assert not RE_PLACEHOLDER_IN_SINGLE_QUOTES.search("the 'data' payload is invalid")
    assert not RE_PLACEHOLDER_IN_SINGLE_QUOTES.search("the domain {domain} is denied")


def test_entity_section_is_absent_everywhere() -> None:
    """No `entity.*` section: `_attr_name = None` makes it unreachable.

    `Entity._name_internal` returns `_attr_name` before it ever looks up a
    `name_translation_key`, so an `entity.notify.speak.name` would be a
    translated string nothing can ever display.
    """
    for path in TRANSLATION_FILES:
        assert not any(key.startswith("entity.") for key in _load(path)), path.name
