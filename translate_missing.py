#!/usr/bin/env python3
"""Fill missing translations in translation.py via the DeepL API.

Reads the audit (Extract_Translations.py) to learn which strings are
missing per language, calls DeepL for each, then rewrites translation.py's
translations_dict with the new entries inserted alongside the existing
ones. Existing entries are preserved verbatim.

Requires:
    pip install deepl

Usage:
    python translate_missing.py
"""

import ast
import sys
import time
from pathlib import Path

try:
    import deepl
except ImportError:
    sys.exit("Missing dependency: pip install deepl")


# --- Configuration --------------------------------------------------------

TRANSLATION_FILE = Path("./TrailPrint3D/translation.py")

# DeepL target-language codes. Add rows here if you add more languages.
DEEPL_LANG = {
    "de_DE":   "DE",
    "fr_FR":   "FR",
    "es":      "ES",
    "it_IT":   "IT",
    "pt_BR":   "PT-BR",
    "ru_RU":   "RU",
    "zh_HANS": "ZH-HANS",
}

# Free tier allows 1 request per second; batch up to 50 strings per request.
BATCH_SIZE = 50
RATE_LIMIT_SEC = 1.05  # 1 second + small safety margin

# Context tag every existing entry uses. Checked against the file — if it
# ever changes, update both this constant and the audit's key handling.
CONTEXT = "*"


# --- Helpers --------------------------------------------------------------


def find_dict_assignment(tree):
    """Locate the `translations_dict = {...}` assignment node."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "translations_dict":
                    return node
    return None


def is_translatable(s):
    """Skip strings DeepL can't meaningfully translate.

    Empty / whitespace-only strings and bare URLs fall through. Format-only
    strings like "%s" go through and come back unchanged; cheaper than
    special-casing them.
    """
    if not s or not s.strip():
        return False
    if s.startswith(("http://", "https://")):
        return False
    return True


def format_dict(d):
    """Serialise the dict to source, matching the file's existing style.

    Produces:
        {
            'lang': {
                ('*', 'src'): 'trans',
                ...
            },
            ...
        }
    with languages sorted alphabetically and, within each, entries sorted
    by source string. Python's repr() picks the correct quote style
    automatically (single quotes normally, double quotes when the string
    contains an apostrophe).
    """
    lines = ["{"]
    for lang in sorted(d.keys()):
        lines.append(f"    {lang!r}: {{")
        for k in sorted(d[lang].keys(), key=lambda kk: kk[1]):
            lines.append(f"        {k!r}: {d[lang][k]!r},")
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines)


# --- Main -----------------------------------------------------------------


def main():
    # Import lazily so this script doesn't run the audit at import time.
    from Extract_Translations import main as audit

    auth_key = input("DeepL API key: ").strip()
    if not auth_key:
        sys.exit("No API key given.")

    translator = deepl.Translator(auth_key)

    # Report remaining quota up front so you can abort before burning through.
    try:
        usage = translator.get_usage()
        if usage.character.valid:
            remaining = usage.character.limit - usage.character.count
            print(
                f"DeepL usage: {usage.character.count:,} / "
                f"{usage.character.limit:,} chars "
                f"({remaining:,} remaining this period)"
            )
    except Exception as exc:
        print(f"(Could not query DeepL usage: {exc!r})")

    print("\nRunning audit to find missing strings...")
    result = audit()

    src = TRANSLATION_FILE.read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    node = find_dict_assignment(tree)
    if node is None:
        sys.exit("Could not find `translations_dict` assignment.")

    current = ast.literal_eval(node.value)

    # Make sure every language we plan to translate has a slot.
    for lang in DEEPL_LANG:
        if lang not in current:
            print(f"NOTE: {lang} not in translations_dict yet, creating entry.")
            current[lang] = {}

    total_added = 0
    for lang, target in DEEPL_LANG.items():
        strings = [s for s in result["per_lang_missing"].get(lang, []) if is_translatable(s)]
        if not strings:
            print(f"\n[{lang}] Nothing missing, skipping.")
            continue

        print(f"\n[{lang}] Translating {len(strings)} strings → {target}...")

        for i in range(0, len(strings), BATCH_SIZE):
            batch = strings[i : i + BATCH_SIZE]
            try:
                results = translator.translate_text(
                    batch, source_lang="EN", target_lang=target
                )
            except deepl.DeepLException as exc:
                print(f"  DeepL error on batch {i}–{i + len(batch)}: {exc!r}")
                print("  Skipping this batch; re-run later to pick it up.")
                continue

            for original, translated in zip(batch, results):
                current[lang][(CONTEXT, original)] = translated.text
                total_added += 1

            done = min(i + BATCH_SIZE, len(strings))
            print(f"  {done}/{len(strings)}")

            # Rate-limit between batches (free tier is 1 req/sec).
            if done < len(strings):
                time.sleep(RATE_LIMIT_SEC)

    if total_added == 0:
        print("\nNothing to write.")
        return

    print(f"\nTotal new translations: {total_added}")

    # Replace only the assignment's source span; keep the header comment
    # and everything after the dict untouched.
    lines = src.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno
    new_block = f"translations_dict = {format_dict(current)}\n"
    new_src = "".join(lines[:start]) + new_block + "".join(lines[end:])

    TRANSLATION_FILE.write_text(new_src, encoding="utf-8")
    print(f"Wrote {TRANSLATION_FILE}")


if __name__ == "__main__":
    main()
