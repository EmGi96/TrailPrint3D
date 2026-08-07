# TrailPrint3D — Agent Coding Instructions

This is a **Blender 5.1+ Python addon** using the Blender Extensions platform. Apply every rule in this file when reading or writing any `.py` file inside `TrailPrint3D/`. §18 (CHANGELOG) applies to the repo-root `CHANGELOG` file instead.

---

## Project Layout

```
TrailPrint3D/                 - Blender addon package (installed as a Blender extension)
  __init__.py                 - register() / unregister(), class list
  addon_preferences.py        - TP3D_AP_preferences
  blender_manifest.toml       - Blender Extensions platform manifest
  constants.py                - shared constants (no side-effects at import time)
  export.py                   - STL / OBJ / 3MF export helpers
  headless_ui.py               - local HTTP config UI for --background headless mode
  operators.py                 - all bpy.types.Operator subclasses
  panels.py                    - all bpy.types.Panel subclasses
  picker_server.py             - local HTTP map-picker window (draw a bbox, POST coords back to Blender)
  progress.py                  - GPU progress overlay + WarningsOverlay
  progress_win.py              - standalone frameless progress window (subprocess)
  props.py                     - TP3D_PG_properties (scene property group)
  temp.py                      - runtime flags (PREMIUMVERSION, has3mf)
  threemf_discovery.py         - discovery helper for the bundled 3MF Import/Export addon
  translation.py               - translations_dict (DE/ZH UI strings)
  updater.py                   - GitHub/Patreon release checker + auto-download
  puzzleGenerator.html         - free Puzzle Configurator (browser UI)
  assets/                      - .blend asset libraries (connectors, holder, other) + progress-overlay SVG icons
  wheels/                      - bundled Shapely / Mapbox Earcut wheels (per-platform)
  utils/
    __init__.py                - re-exports from submodules (wildcards OK here, see §10)
    elevation.py                - elevation API helpers
    generation.py                - runGeneration() orchestration
    geo.py                       - coordinate math
    geometry2d.py                - Shapely-based 2D geometry helpers (OSM pipeline)
    io_geojson.py                - GeoJSON boundary import
    io_gpx.py                    - GPX / IGC file parsing
    mesh_ops.py                  - bmesh utilities
    metadata.py                  - custom property helpers
    osm.py                       - Overpass / OSM fetching and caching
    presets.py                   - CSV preset load/save
    primitives.py                - curve / mesh creation helpers
    scene.py                     - scene-level helpers (show_message_box, etc.)
    terrain.py                   - terrain generation pipeline
    text_objects.py              - text and icon mesh helpers
    trail_import.py              - GPX import entry point

premium/                       - Premium-only source, absent from the free build (see §15)
  __init__.py
  operators_pe.py               - premium bpy.types.Operator subclasses
  utils_pe.py                   - premium-only utility functions
  multitile_configurator.html   - premium multi-tile map configurator (browser UI)
  puzzleGenerator_pe.html       - premium Puzzle Configurator (hex/radial piece shapes, multi-GPX)
  assets/                       - premium-only .blend asset libraries (puzzles.blend)

tests/                         - standalone test suite, run inside Blender's own Python (not pytest)
  run_all_tests.py              - runs every test_*.py and prints a combined pass/fail summary
  headless_generate.py          - headless generator with a browser-based config UI (manual/dev use)
  test_generation_pipeline.py   - end-to-end runGeneration() tests against real elevation/Overpass APIs
  test_model_shape_matrix.py    - runGeneration() across every shape/shape-extra/medal-handle combo, exported as 3MF
  test_geo_elevation.py         - unit tests for pure-math functions in geo.py / elevation.py
  test_geojson_import.py        - tests for the GeoJSON boundary reader
  test_geometry2d.py            - unit tests for utils/geometry2d.py
  test_gpx.py                   - tests for the GPX reader
  test_osm_pipeline.py          - unit/integration tests for the OSM data pipeline
  test_updater.py               - unit tests for updater.py
  Resources/                    - GPX/GeoJSON fixture files used by the tests above
```

Tests run directly with Blender's bundled Python, not `pytest`, e.g.:
```powershell
& "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/run_all_tests.py
```

---

## 1. Class Naming Convention

Use Blender's `PREFIX_TYPE_suffix` scheme. The project prefix is **`TP3D`**.

| Base class | Type tag | Example class name |
|---|---|---|
| `bpy.types.Operator` | `OT` | `TP3D_OT_export_stl` |
| `bpy.types.Panel` | `PT` | `TP3D_PT_generate` |
| `bpy.types.Menu` | `MT` | `TP3D_MT_presets` |
| `bpy.types.PropertyGroup` | `PG` | `TP3D_PG_properties` |
| `bpy.types.AddonPreferences` | `AP` | `TP3D_AP_preferences` |

- The suffix after the tag is always `snake_case`. Never PascalCase or camelCase.
- Do not use legacy prefixes found in the codebase: `_Op_`, `_P_`, `_Pop_`, `TRAILPRINT_OT_`. Standardise everything on `TP3D_`.

---

## 2. `bl_idname` Convention

### Operators — `"tp3d.snake_case"`

```python
# ✅
class TP3D_OT_export_stl(bpy.types.Operator):
    bl_idname = "tp3d.export_stl"

# ❌ — wm.* is reserved for Blender's Window Manager
class TP3D_Op_ExportSTL(bpy.types.Operator):
    bl_idname = "wm.exportstl"

# ❌ — invented namespace
bl_idname = "pop.merge"
```

### Panels — `"TP3D_PT_snake_case"` (matches class name exactly)

```python
# ✅
class TP3D_PT_generate(bpy.types.Panel):
    bl_idname = "TP3D_PT_generate"

# ❌ — illegal '+', wrong prefix, doesn't match class name
class TP3D_P_Generate(bpy.types.Panel):
    bl_idname = "PT_EmGi_3DPath+"
```

### No duplicates
Every `bl_idname` must be unique across the entire addon. A duplicate silently overwrites the first class.

### Canonical name map

```
TP3D_OT_run_generation      "tp3d.run_generation"
TP3D_OT_export_stl          "tp3d.export_stl"
TP3D_OT_export_obj          "tp3d.export_obj"
TP3D_OT_export_three_mf     "tp3d.export_three_mf"
TP3D_OT_rescale             "tp3d.rescale"
TP3D_OT_thicken             "tp3d.thicken"
TP3D_OT_magnet_holes        "tp3d.magnet_holes"
TP3D_OT_cut_pin_socket      "tp3d.cut_pin_socket"
TP3D_OT_dovetail            "tp3d.dovetail"
TP3D_OT_bottom_mark         "tp3d.bottom_mark"
TP3D_OT_color_mountain      "tp3d.color_mountain"
TP3D_OT_contour_lines       "tp3d.contour_lines"
TP3D_OT_save_preset         "tp3d.save_preset"
TP3D_OT_load_preset         "tp3d.load_preset"
TP3D_OT_delete_preset       "tp3d.delete_preset"
TP3D_OT_clear_cache         "tp3d.clear_cache"
TP3D_OT_pin_coords          "tp3d.pin_coords"
TP3D_OT_import_text         "tp3d.import_text"
TP3D_OT_import_svg          "tp3d.import_svg"
TP3D_OT_import_pin          "tp3d.import_pin"
TP3D_OT_install_three_mf    "tp3d.install_three_mf"
TP3D_OT_open_website        "tp3d.open_website"
TP3D_OT_join_discord        "tp3d.join_discord"
TP3D_OT_info_video          "tp3d.info_video"
TP3D_OT_popup_merge         "tp3d.popup_merge"
TP3D_OT_popup_text          "tp3d.popup_text"
TP3D_OT_popup_svg           "tp3d.popup_svg"
TP3D_OT_popup_pin           "tp3d.popup_pin"
TP3D_OT_warnings_mouse      "tp3d.warnings_mouse"

TP3D_PT_generate            "TP3D_PT_generate"
TP3D_PT_advanced            "TP3D_PT_advanced"
TP3D_PT_shapes              "TP3D_PT_shapes"

TP3D_PG_properties          registered as bpy.types.Scene.tp3d
TP3D_AP_preferences         bl_idname = __package__
```

---

## 3. `register()` / `unregister()` Rules

- Every class registered in `register()` must be unregistered in `unregister()` in **reverse order**.
- `bpy.app.handlers` callbacks added in `register()` must be removed in `unregister()`.
- `bpy.types.Scene.*` attributes added in `register()` must be deleted in `unregister()`.
- Wrap each `bpy.utils.unregister_class()` call in `try/except RuntimeError`, not bare `except`.

---

## 4. No Side-Effects at Module / Import Level

`constants.py` and every other module must not perform filesystem I/O at import time. `os.makedirs()`, file reads/writes, and directory creation belong in `register()` or a lazy first-use helper.

```python
# ❌ — runs on every Blender startup before register()
if not os.path.exists(cache_dir):
    os.makedirs(cache_dir)

# ✅ — call this from register()
def _ensure_dirs():
    os.makedirs(cache_dir, exist_ok=True)
```

Do not read or write `bpy.context` at module level — it is not valid during registration.

---

## 5. Operator Return Values and Error Reporting

- Validation failure → `self.report({'ERROR'}, "message")` then `return {'CANCELLED'}`.
- Success → `return {'FINISHED'}`.
- Never return `{'FINISHED'}` when the operation did not complete (misleads undo history).
- Do **not** call `utils.show_message_box()` inside `execute()`. It invokes `bpy.ops` internally, which is re-entrant and forbidden in Blender 4.x execute context. Use `self.report()` instead.

```python
# ✅
def execute(self, context):
    if not context.selected_objects:
        self.report({'ERROR'}, "No objects selected.")
        return {'CANCELLED'}
    ...
    return {'FINISHED'}
```

---

## 6. `bl_options` on Operators

- Operators that mutate the scene must include `'UNDO'` so Ctrl-Z works.
- Read-only operators (open URL, show info popup) do not need `'UNDO'`.

```python
bl_options = {'REGISTER', 'UNDO'}  # for any operator that creates/edits/moves objects
```

---

## 7. Use the `context` Parameter, Not `bpy.context`

Inside `execute()`, `invoke()`, and `draw()`, always use the `context` argument that Blender passes in. Never reach for the global `bpy.context` inside these methods.

```python
# ✅
selected = context.selected_objects

# ❌
selected = bpy.context.selected_objects
```

---

## 8. Do Not Shadow the `props` Module

`from . import props` imports the props module. Using `props = context.scene.tp3d` inside an operator silently shadows it. Use `tp3d` as the local name for the property group instance.

```python
# ✅
tp3d = context.scene.tp3d

# ❌ — shadows the imported props module
props = context.scene.tp3d
```

---

## 9. Third-Party Dependencies (`requests`)

`requests` is not bundled with Blender. A bare `import requests` at the top level breaks the addon on any clean install. It must either be:

- Declared as a wheel in `blender_manifest.toml` under `[wheels]`, **or**
- Guarded with `try/except ImportError` that surfaces a clear user-facing error.

---

## 10. Wildcard Imports in `utils/__init__.py`

`utils/__init__.py` exists purely as an aggregation shim so callers can write `utils.show_message_box()` instead of importing from individual submodules. Wildcard re-exports are acceptable **only** in this file for that purpose.

```python
# ✅ — fine in utils/__init__.py (aggregation shim)
from .mesh_ops import *
from .scene import *

# ✅ — explicit is also fine and preferred where collisions are a concern
from .mesh_ops import selectBottomFaces, recalculateNormals, merge_with_map
```

In every other file — operators, panels, regular utility modules — wildcard imports are forbidden. They hide name origins, create silent shadowing between submodules, and break IDE navigation.

```python
# ❌ — in operators.py, panels.py, or any non-aggregator module
from .utils.mesh_ops import *
```

---

## 11. Error Handling Specificity

- Never use bare `except:` — it catches `SystemExit` and `KeyboardInterrupt`.
- Use specific types: `except (OSError, json.JSONDecodeError):`, `except requests.RequestException:`.
- Use `except Exception:` only as a last resort, and always log it.

---

## 12. Export Path Validation — Don't Repeat Yourself

The path-validation block is duplicated across three export operators. Use the shared helper in `export.py` rather than copy-pasting the check.

---

## 13. Long-Running Work Must Not Block the Main Thread

`time.sleep()` on Blender's main thread freezes the viewport and makes Blender appear crashed. All API calls (elevation, OSM, etc.) that include rate-limiting sleeps must run inside a worker thread (`threading.Thread`), communicating progress back via the existing `SubprocessProgress` / `ProgressOverlay` API.

---

## 14. Renaming a Class or `bl_idname` — Four Places Must Change Atomically

When renaming any Blender type, all four of the following must be updated in the **same commit / edit pass**. Changing only some of them leaves the addon broken until all are done.

1. **The class definition** in `operators.py`, `panels.py`, or `progress.py` — class name and `bl_idname`.
2. **The `classes` list** in `__init__.py` — references classes by Python object (`operators.OldName`), so the attribute name must match the new class name.
3. **Every `layout.operator("old.idname")`** call in `panels.py` (and anywhere else a string idname is used to invoke the operator).
4. **`_PREMIUM_CLASS_NAMES`** in `__init__.py` — a plain string list of class names for premium operators loaded dynamically from `operators_pe.py`. If a premium class is renamed, this list must be updated too.

```python
# __init__.py — both of these must stay in sync after a rename:
classes = [
    operators.TP3D_OT_export_stl,   # ← Python object reference
    ...
]
_PREMIUM_CLASS_NAMES = [
    "TP3D_OT_terrain",              # ← plain string, must match operators_pe.py class name
    ...
]
```

---

## 15. Premium Module Pattern

Premium-only operators live in `operators_pe.py` and `utils_pe.py` (not present in the free build). These files are loaded dynamically inside `register()` only when `temp.PREMIUMVERSION` is `True` (detected by the presence of `operators_pe.py` on disk).

- **Do not import `operators_pe` at the top level** of any module — it won't exist in free builds.
- **Do not edit `operators_pe.py` class names** without also updating `_PREMIUM_CLASS_NAMES` in `__init__.py`.
- Premium idnames follow the same `"tp3d.snake_case"` convention as free operators.

---

## 16. Translation — Keep `_()` Wrappers

User-facing strings (labels, descriptions, messages) are wrapped with `_()` (`pgettext_iface`) for Chinese and German translation support. Do not remove these wrappers when editing strings. Do not add `_()` to non-user-facing strings (file paths, identifiers, print statements).

```python
# ✅
bl_label = _("Export STL")
self.report({'ERROR'}, _("No objects selected."))

# ❌ — don't wrap internal strings
bl_idname = _("tp3d.export_stl")
```

---

## 17. Building the Addon

```powershell
# From the repo root:
.\build.ps1
# Which runs:
blender --command extension build --source-dir "./TrailPrint3D/"
```

This produces a `.zip` in the repo root that can be installed via Blender → Preferences → Add-ons → Install from Disk. Run this after any structural change to verify Blender can load the addon without errors (watch the system console for `RuntimeError` on registration).

---

## 18. CHANGELOG Format

The repo-root `CHANGELOG` file (no extension) tracks user-facing changes per version.

- **Only touch it when explicitly asked.** Do not add entries as a side effect of unrelated work, even in the same session.
- **Format per line**: `Added:` / `Changed:` / `Fixed:` prefix, one line per entry, no sub-bullets, no nested lists.
- **Compactness**: a single cohesive feature or rework — even one that touches several behaviors — collapses into **one line** under the category that best fits (usually the most invasive change: `Changed` beats `Added` if the entry is mostly a rework of something existing). Do not split one feature's sub-parts across multiple lines or multiple categories.
- **Premium tagging**: any entry describing a Patreon/premium-gated feature or option gets a trailing `(Premium)` tag, e.g. `Added: Hexagonal and Concentric-Ring puzzle piece shapes in the Puzzle Configurator (Premium)`.
- **Placement**: add new entries to the current top-most version block (the open/unreleased one). Do not create a new `Version X.Y` header or bump the version number unless explicitly told to.

```
# ✅ — one compact line for a multi-part rework
Changed: Reworked Contour Lines — now subtracts its bands from the map instead of only intersecting, supports real-world-meter distance/offset (elevation-scale aware, enabled by default) alongside mm, generates at the map's own origin instead of the 3D cursor, and warns instead of failing when distance is smaller than thickness

# ❌ — same feature fragmented across lines/categories
Added: Real-world-meter distance/offset for Contour Lines
Changed: Contour Lines now generated at the map's own origin
Fixed: Contour Lines no longer overlap the map
```
