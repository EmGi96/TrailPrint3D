#!/usr/bin/env python3
"""Extract translatable strings from the TrailPrint3D addon and diff them
against translation.py's existing dictionaries. Also flags text that bypasses
Blender's translation system entirely.

Read-only: never modifies the addon source. Writes one .ods report.

Requires odfpy (for writing the .ods report)
`pip install odfpy`
"""

import ast
from pathlib import Path

from odf.opendocument import OpenDocumentSpreadsheet
from odf.style import Style, TableCellProperties, TableColumnProperties, TextProperties
from odf.table import Table, TableCell, TableColumn, TableRow
from odf.text import P

ADDON_ROOT = Path("./TrailPrint3D")
TRANSLATION_FILE = ADDON_ROOT / "translation.py"
OUTPUT_ODS = "./tp3d-translation-audit.ods"

# Directories/files not part of the addon's own translatable UI surface.
EXCLUDE_DIRS = {"tests", "__pycache__"}
EXCLUDE_FILES = {
    "translation.py",
    "headless_ui.py",
    "picker_server.py",
    "puzzleGenerator.html",
    "map_generator.html",
    "map_generator_pe.html",
    "multitile_generator.html",
    "puzzleGenerator_pe.html",
    "slidingPUzzleGenerator.html",
}

TRANSLATE_KWARGS = {"text", "name", "description"}
BL_CLASS_ATTRS = {"bl_label", "bl_description"}

# Expected pgettext variant, keyed by how the string is being used.
# A call site whose variant doesn't match gets flagged in "Wrong Variant".
#   iface -> UI labels, panel titles, layout.prop(text=...), bl_label
#   tip   -> tooltips, bl_description, description= on properties
#   rpt   -> self.report(...), add_warning(...)
#   data  -> new datablock names (not currently auto-detected)
CONTEXT_EXPECTED_VARIANT = {
    "text": "iface",
    "name": "iface",
    "bl_label": "iface",
    "bl_description": "tip",
    "description": "tip",
    "self.report": "rpt",
    "add_warning": "rpt",
}

# --- Things that must NEVER be translated -----------------------------------
#
# Exception classes whose messages are developer-facing diagnostics only.
# Raise sites using one of these are ignored for translation review; if the
# message *is* wrapped in a pgettext variant, it's flagged as incorrectly
# wrapped instead of as a translation target.
INTERNAL_EXCEPTION_CLASSES = {
    "GeoTiffError",
}

# Attribute-chain suffixes for calls whose `name=` kwarg is a Blender /
# threading identifier, never shown to the user. If one of these values is
# wrapped in a pgettext variant, it's flagged as incorrectly wrapped.
#
# Matching is on `call_qualified_name()` output, either exact ("Thread") or
# suffix-after-a-dot ("obj.modifiers.new", "bpy.data.materials.new", etc.).
INTERNAL_NAME_CALL_SUFFIXES = (
    # threading.Thread(name=...)
    "Thread",
    # bpy.data.materials.new / .get / .load
    "materials.new",
    "materials.get",
    "materials.load",
    # Blender datablock factories whose `name=` is an internal identifier
    "modifiers.new",
    "vertex_groups.new",
    "vertex_colors.new",
    "node_groups.new",
    "collections.new",
    "textures.new",
    "images.new",
)

# --- Heuristic patterns for "incorrectly wrapped" (extend as you find more) --
#
# Strings that should never be wrapped in a pgettext variant even outside the
# contexts above. Checked against the *literal* inside `_(...)` / `_tip(...)`.
INCORRECTLY_WRAPPED_LITERAL_PATTERNS = (
    # URLs are language-independent.
    ("url", lambda s: s.startswith(("http://", "https://"))),
)


def iter_py_files():
    for path in ADDON_ROOT.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.name in EXCLUDE_FILES:
            continue
        yield path


def scan_import_aliases(tree):
    """Return {local_name: variant} for every pgettext* import in *tree*.

    e.g. `from bpy.app.translations import pgettext_iface as _`
         -> {"_": "iface"}
         `from bpy.app.translations import pgettext as _`
         -> {"_": "plain"}
    """
    aliases = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "bpy.app.translations":
            continue
        for name in node.names:
            if not name.name.startswith("pgettext"):
                continue
            if name.name == "pgettext":
                variant = "plain"
            else:
                # "pgettext_iface" -> "iface", "pgettext_rpt" -> "rpt"
                variant = name.name.split("_", 1)[1]
            aliases[name.asname or name.name] = variant
    return aliases


def is_wrapped_in_gettext(node, aliases):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in aliases
    )


def variant_used(node, aliases):
    """Return the variant name if *node* is a call to a known alias, else None."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in aliases
    ):
        return aliases[node.func.id]
    return None


def const_str(node):
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )


def call_qualified_name(node):
    """Dotted name of the call target, e.g. 'bpy.data.materials.new', 'Thread',
    'obj.modifiers.new'. Empty string if it can't be determined statically."""
    if not isinstance(node, ast.Call):
        return ""
    parts = []
    func = node.func
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def is_internal_name_call(node):
    """True if this call's `name=` kwarg is an internal identifier.

    Covers Thread(...), Blender datablock factories (materials, modifiers,
    vertex groups, node groups, collections, ...) whose names are never shown
    to the user, and are therefore never translatable."""
    qual = call_qualified_name(node)
    if not qual:
        return False
    for suffix in INTERNAL_NAME_CALL_SUFFIXES:
        if qual == suffix or qual.endswith("." + suffix):
            return True
    return False


def literal_is_incorrectly_wrapped(s):
    """True if a literal string matches one of the always-untranslatable
    patterns (URLs, etc.)."""
    if s is None:
        return False
    for _label, pred in INCORRECTLY_WRAPPED_LITERAL_PATTERNS:
        try:
            if pred(s):
                return True
        except Exception:
            pass
    return False


class Extractor(ast.NodeVisitor):
    def __init__(self, filepath, rel, aliases):
        self.filepath = filepath
        self.rel = rel
        self.aliases = aliases
        self.tracked = []
        self.bypasses = []
        self.unwrapped = []
        self.needs_review = []
        self.wrong_variant = []
        self.incorrectly_wrapped = []

    def visit_Call(self, node):
        # --- Any pgettext* call gets recorded, regardless of which variant ---
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in self.aliases
            and node.args
        ):
            arg = node.args[0]
            s = const_str(arg)
            if s is not None:
                if literal_is_incorrectly_wrapped(s):
                    self.incorrectly_wrapped.append(
                        ("wrapped literal", self.rel, node.lineno, s)
                    )
                else:
                    self.tracked.append((s, self.rel, node.lineno))
            elif isinstance(arg, ast.JoinedStr):
                self.needs_review.append(
                    (
                        "f-string still inside _()",
                        self.rel,
                        node.lineno,
                        ast.unparse(arg)[:80],
                    )
                )
            else:
                self.needs_review.append(
                    (
                        "non-literal argument to _()",
                        self.rel,
                        node.lineno,
                        ast.unparse(arg)[:80],
                    )
                )

        # --- self.report ---
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "report"
            and len(node.args) >= 2
        ):
            msg = node.args[1]
            variant = variant_used(msg, self.aliases)
            if variant is None:
                s = const_str(msg)
                if s is not None:
                    self.bypasses.append(("self.report", self.rel, node.lineno, s))
                elif isinstance(msg, ast.JoinedStr):
                    self.bypasses.append(
                        (
                            "self.report (f-string)",
                            self.rel,
                            node.lineno,
                            ast.unparse(msg)[:100],
                        )
                    )
            elif variant != CONTEXT_EXPECTED_VARIANT["self.report"]:
                self.wrong_variant.append(
                    (
                        "self.report",
                        self.rel,
                        node.lineno,
                        variant,
                        "rpt",
                        ast.unparse(msg)[:100],
                    )
                )

        # --- add_warning ---
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_warning"
            and node.args
        ):
            msg = node.args[0]
            variant = variant_used(msg, self.aliases)
            if variant is None:
                s = const_str(msg)
                if s is not None:
                    self.bypasses.append(("add_warning", self.rel, node.lineno, s))
                elif isinstance(msg, ast.JoinedStr):
                    self.bypasses.append(
                        (
                            "add_warning (f-string)",
                            self.rel,
                            node.lineno,
                            ast.unparse(msg)[:100],
                        )
                    )
            elif variant != CONTEXT_EXPECTED_VARIANT["add_warning"]:
                self.wrong_variant.append(
                    (
                        "add_warning",
                        self.rel,
                        node.lineno,
                        variant,
                        "rpt",
                        ast.unparse(msg)[:100],
                    )
                )

        # --- keyword arguments: text=, name=, description= ---
        internal_name = is_internal_name_call(node)
        for kw in node.keywords:
            if kw.arg not in CONTEXT_EXPECTED_VARIANT:
                continue

            # --- internal identifier: never translate, flag if wrapped ---
            if kw.arg == "name" and internal_name:
                variant = variant_used(kw.value, self.aliases)
                if variant is not None:
                    self.incorrectly_wrapped.append(
                        (
                            "internal name=",
                            self.rel,
                            node.lineno,
                            ast.unparse(kw.value)[:100],
                        )
                    )
                continue

            expected = CONTEXT_EXPECTED_VARIANT[kw.arg]
            variant = variant_used(kw.value, self.aliases)
            if variant is None:
                s = const_str(kw.value)
                if s:
                    self.unwrapped.append((kw.arg, self.rel, node.lineno, s))
            elif variant != expected:
                self.wrong_variant.append(
                    (
                        f"{kw.arg}=",
                        self.rel,
                        node.lineno,
                        variant,
                        expected,
                        ast.unparse(kw.value)[:100],
                    )
                )

        self.generic_visit(node)

    def visit_Assign(self, node):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in BL_CLASS_ATTRS:
                if const_str(node.value) == "TrailPrint3D":
                    continue
                expected = CONTEXT_EXPECTED_VARIANT[target.id]
                variant = variant_used(node.value, self.aliases)
                if variant is None:
                    s = const_str(node.value)
                    if s:
                        self.unwrapped.append((target.id, self.rel, node.lineno, s))
                elif variant != expected:
                    self.wrong_variant.append(
                        (
                            target.id,
                            self.rel,
                            node.lineno,
                            variant,
                            expected,
                            ast.unparse(node.value)[:100],
                        )
                    )
        self.generic_visit(node)

    def visit_Raise(self, node):
        exc = node.exc
        if isinstance(exc, ast.Call) and exc.args:
            arg = exc.args[0]
            exc_name = ast.unparse(exc.func)

            # --- internal exception: console-only, never translate ---
            if exc_name in INTERNAL_EXCEPTION_CLASSES:
                variant = variant_used(arg, self.aliases)
                if variant is not None:
                    self.incorrectly_wrapped.append(
                        (
                            f"{exc_name} raise",
                            self.rel,
                            node.lineno,
                            ast.unparse(arg)[:100],
                        )
                    )
                self.generic_visit(node)
                return

            s = const_str(arg)
            if s is not None:
                self.needs_review.append(
                    ("raised exception", self.rel, node.lineno, f"{exc_name}: {s[:80]}")
                )
            elif isinstance(arg, ast.JoinedStr):
                self.needs_review.append(
                    (
                        "raised exception (f-string)",
                        self.rel,
                        node.lineno,
                        f"{exc_name}: {ast.unparse(arg)[:80]}",
                    )
                )
        self.generic_visit(node)


def extract_all():
    (
        tracked,
        bypasses,
        unwrapped,
        needs_review,
        wrong_variant,
        incorrectly_wrapped,
    ) = [], [], [], [], [], []
    for path in iter_py_files():
        rel = str(path.relative_to(ADDON_ROOT))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError as e:
            needs_review.append(("FILE FAILED TO PARSE", rel, e.lineno or 0, str(e)))
            continue
        ex = Extractor(path, rel, scan_import_aliases(tree))
        ex.visit(tree)
        tracked.extend(ex.tracked)
        bypasses.extend(ex.bypasses)
        unwrapped.extend(ex.unwrapped)
        needs_review.extend(ex.needs_review)
        wrong_variant.extend(ex.wrong_variant)
        incorrectly_wrapped.extend(ex.incorrectly_wrapped)
    return (
        tracked,
        bypasses,
        unwrapped,
        needs_review,
        wrong_variant,
        incorrectly_wrapped,
    )


def load_translation_dict():
    tree = ast.parse(TRANSLATION_FILE.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "translations_dict":
                    return ast.literal_eval(node.value)
    raise RuntimeError("translations_dict assignment not found in translation.py")


def add_ods_sheet(doc, title, rows, header_style, alt_style):
    table = Table(name=title)

    # Pre-calculate column widths based on content
    col_widths = [0] * (len(rows[0]) if rows else 0)
    for row in rows:
        for col_idx, cell_data in enumerate(row):
            col_widths[col_idx] = max(col_widths[col_idx], len(str(cell_data)))

    # Add width-styled columns before rows
    for col_idx, char_count in enumerate(col_widths):
        # Roughly 0.25cm per character, cap max width at 15cm for readability
        width_cm = max(2.5, min((char_count + 2) * 0.25, 15.0))

        col_style = Style(name=f"{title}_Col_{col_idx}", family="table-column")
        col_style.addElement(TableColumnProperties(columnwidth=f"{width_cm}cm"))
        doc.automaticstyles.addElement(col_style)

        table.addElement(TableColumn(stylename=col_style))

    # Populate rows and cells
    for row_idx, row_data in enumerate(rows):
        tr = TableRow()
        for cell_data in row_data:
            if isinstance(cell_data, (int, float)):
                tc = TableCell(valuetype="float", value=str(cell_data))
            else:
                tc = TableCell(valuetype="string")

            tc.addElement(P(text=str(cell_data)))

            if row_idx == 0:
                tc.setAttribute("stylename", header_style)
            elif row_idx % 2 == 0:
                tc.setAttribute("stylename", alt_style)

            tr.addElement(tc)
        table.addElement(tr)

    doc.spreadsheet.addElement(table)


def write_ods_report(data, out_path):
    doc = OpenDocumentSpreadsheet()

    # Define Header Style
    header_style = Style(name="HeaderStyle", family="table-cell")
    header_style.addElement(TableCellProperties(backgroundcolor="#3F4B5B"))
    header_style.addElement(TextProperties(fontweight="bold", color="#FFFFFF"))
    doc.automaticstyles.addElement(header_style)

    # Define Alternating Row Style
    alt_style = Style(name="AltStyle", family="table-cell")
    alt_style.addElement(TableCellProperties(backgroundcolor="#F5F7FA"))
    doc.automaticstyles.addElement(alt_style)

    # 1. Overview Sheet
    overview_data = [
        ["Metric", "Count"],
        ["Tracked Strings", len(data["master"])],
    ]
    for lang in data["languages"]:
        overview_data.append([f"[{lang}] Missing", len(data["per_lang_missing"][lang])])
        overview_data.append([f"[{lang}] Dead Keys", len(data["per_lang_dead"][lang])])
    overview_data.extend(
        [
            ["Bypasses", len(data["bypasses"])],
            ["Unwrapped", len(data["unwrapped"])],
            ["Needs Review", len(data["needs_review"])],
            ["Wrong Variant", len(data["wrong_variant"])],
            ["Incorrectly Wrapped", len(data["incorrectly_wrapped"])],
        ]
    )
    add_ods_sheet(doc, "Overview", overview_data, header_style, alt_style)

    # 2. Missing Strings Sheet
    missing_data = [["Language", "String", "File", "Line"]]
    for lang in data["languages"]:
        for s in data["per_lang_missing"][lang]:
            info = data["master"].get(s, {})
            missing_data.append([lang, s, info.get("file", ""), info.get("line", "")])
    if len(missing_data) == 1:
        missing_data.append(["", "None", "", ""])
    add_ods_sheet(doc, "Missing Translations", missing_data, header_style, alt_style)

    # 3. Dead Keys Sheet
    dead_data = [["Language", "String"]]
    for lang in data["languages"]:
        for s in data["per_lang_dead"][lang]:
            dead_data.append([lang, s])
    if len(dead_data) == 1:
        dead_data.append(["", "None"])
    add_ods_sheet(doc, "Dead Keys", dead_data, header_style, alt_style)

    # 4. Bypasses Sheet
    bypass_data = [["Kind", "File", "Line", "Message"]] + data["bypasses"]
    if len(bypass_data) == 1:
        bypass_data.append(["", "None", "", ""])
    add_ods_sheet(doc, "Bypasses", bypass_data, header_style, alt_style)

    # 5. Unwrapped Sheet
    unwrapped_data = [["Keyword", "File", "Line", "String"]] + data["unwrapped"]
    if len(unwrapped_data) == 1:
        unwrapped_data.append(["", "None", "", ""])
    add_ods_sheet(doc, "Unwrapped", unwrapped_data, header_style, alt_style)

    # 6. Needs Review Sheet
    review_data = [["Issue", "File", "Line", "Snippet"]] + data["needs_review"]
    if len(review_data) == 1:
        review_data.append(["", "None", "", ""])
    add_ods_sheet(doc, "Needs Review", review_data, header_style, alt_style)

    # 7. Wrong Variant Sheet
    wrong_data = [["Context", "File", "Line", "Used", "Expected", "Snippet"]]
    wrong_data += data["wrong_variant"]
    if len(wrong_data) == 1:
        wrong_data.append(["", "None", "", "", "", ""])
    add_ods_sheet(doc, "Wrong Variant", wrong_data, header_style, alt_style)

    # 8. Incorrectly Wrapped Sheet
    incorrect_data = [["Reason", "File", "Line", "Expression"]]
    incorrect_data += data["incorrectly_wrapped"]
    if len(incorrect_data) == 1:
        incorrect_data.append(["", "None", "", ""])
    add_ods_sheet(doc, "Incorrectly Wrapped", incorrect_data, header_style, alt_style)

    doc.save(out_path)


def main():
    (
        tracked,
        bypasses,
        unwrapped,
        needs_review,
        wrong_variant,
        incorrectly_wrapped,
    ) = extract_all()
    translations = load_translation_dict()
    languages = sorted(translations.keys())

    master = {}
    for s, rel, lineno in tracked:
        if s not in master:
            master[s] = {"file": rel, "line": lineno, "count": 0}
        master[s]["count"] += 1

    per_lang_missing = {lang: [] for lang in languages}
    per_lang_dead = {lang: [] for lang in languages}
    for lang in languages:
        lang_dict = translations[lang]
        keys = {k[1] for k in lang_dict}
        for s in master:
            if s not in keys:
                per_lang_missing[lang].append(s)
        for s in keys:
            if s not in master:
                per_lang_dead[lang].append(s)

    return {
        "master": master,
        "languages": languages,
        "translations": translations,
        "per_lang_missing": per_lang_missing,
        "per_lang_dead": per_lang_dead,
        "bypasses": bypasses,
        "unwrapped": unwrapped,
        "needs_review": needs_review,
        "wrong_variant": wrong_variant,
        "incorrectly_wrapped": incorrectly_wrapped,
    }


if __name__ == "__main__":
    result = main()

    print(f"Tracked (unique English strings): {len(result['master'])}")
    print(f"Languages found: {result['languages']}")
    for lang in result["languages"]:
        print(
            f"  {lang}: {len(result['per_lang_missing'][lang])} missing, "
            f"{len(result['per_lang_dead'][lang])} dead keys"
        )
    print(f"Bypasses translation entirely: {len(result['bypasses'])}")
    print(f"Unwrapped but likely fine: {len(result['unwrapped'])}")
    print(f"Needs manual review: {len(result['needs_review'])}")
    print(f"Wrong translation variant: {len(result['wrong_variant'])}")
    print(f"Incorrectly wrapped: {len(result['incorrectly_wrapped'])}")

    print(f"\nWriting styled report to {OUTPUT_ODS}...")
    write_ods_report(result, OUTPUT_ODS)
    print("Done.")
