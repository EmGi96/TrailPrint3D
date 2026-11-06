#!/usr/bin/env python3
"""One-off cleanup: remove every dead translation key from translation.py.

Dead keys come from the Extract_Translations audit -- entries present in
translations_dict but no longer found anywhere in the addon source. Removes
them from every language, preserving the surviving entries exactly as written.

Reads:  ./TrailPrint3D/translation.py
Writes: ./TrailPrint3D/translation.py  (backup at .py.bak)
"""

import ast
import shutil
import sys
from pathlib import Path

# Reuse the audit's main() to get the current dead keys
sys.path.insert(0, str(Path(__file__).parent))
from Extract_Translations import main  # noqa: E402

TRANSLATION_FILE = Path("./TrailPrint3D/translation.py")


def find_translations_dict_assign(tree):
    """Return the Assign / AnnAssign node whose target is translations_dict."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "translations_dict":
                    return node
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == "translations_dict"
            ):
                return node
    return None


def format_dict(d, indent):
    """Format a nested dict of {lang: {(ctx, src): trans}} in a readable,
    Blender-conventional style. One entry per line, keys sorted by source
    string for stable output."""
    pad = "    " * indent
    inner = "    " * (indent + 1)
    lines = ["{"]
    for lang in sorted(d.keys()):
        lines.append(f"{inner}{lang!r}: {{")
        inner2 = "    " * (indent + 2)
        entries = d[lang]
        # Sort by (context, source) so identical runs stay adjacent across runs
        for k in sorted(
            entries.keys(),
            key=lambda kk: (kk[0], kk[1]) if isinstance(kk, tuple) else (str(kk), ""),
        ):
            lines.append(f"{inner2}{k!r}: {entries[k]!r},")
        lines.append(f"{inner}}},")
    lines.append(f"{pad}}}")
    return "\n".join(lines)


def main_cleanup():
    print("Running audit to find dead keys...")
    result = main()

    dead = set()
    for lang in result["languages"]:
        dead.update(result["per_lang_dead"][lang])
    print(f"Dead keys to remove: {len(dead)}")
    if not dead:
        print("Nothing to do.")
        return

    src = TRANSLATION_FILE.read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    node = find_translations_dict_assign(tree)
    if node is None:
        raise SystemExit("translations_dict assignment not found")

    current = ast.literal_eval(node.value)

    filtered = {}
    total_removed = 0
    for lang, lang_dict in current.items():
        new_dict = {}
        for k, v in lang_dict.items():
            if isinstance(k, tuple) and len(k) >= 2 and k[1] in dead:
                total_removed += 1
                continue
            new_dict[k] = v
        filtered[lang] = new_dict
        print(
            f"  {lang}: removed {len(lang_dict) - len(new_dict)}, kept {len(new_dict)}"
        )

    print(f"Total removed: {total_removed}")

    # Backup
    backup = TRANSLATION_FILE.with_suffix(".py.bak")
    shutil.copy2(TRANSLATION_FILE, backup)
    print(f"Backup written to {backup}")

    # Replace the assignment's source span
    lines = src.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno

    new_block = f"translations_dict = {format_dict(filtered, 0)}\n"
    new_src = "".join(lines[:start]) + new_block + "".join(lines[end:])

    TRANSLATION_FILE.write_text(new_src, encoding="utf-8")
    print(f"Rewrote {TRANSLATION_FILE}")


if __name__ == "__main__":
    main_cleanup()
