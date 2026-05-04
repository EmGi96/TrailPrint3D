# TrailPrint3D — Agent Coding Instructions

This is a **Blender 4.5+ Python addon** using the Blender Extensions platform. Apply every rule in this file when reading or writing any `.py` file inside `TrailPrint3D/`.

---

## Project Layout

```
TrailPrint3D/
  __init__.py           - register() / unregister(), class list
  addon_preferences.py  - TP3D_AP_preferences
  constants.py          - shared constants (no side-effects at import time)
  export.py             - STL / OBJ / 3MF export helpers
  operators.py          - all bpy.types.Operator subclasses
  panels.py             - all bpy.types.Panel subclasses
  progress.py           - GPU progress overlay + WarningsOverlay
  props.py              - TP3D_PG_properties (scene property group)
  temp.py               - runtime flags (PREMIUMVERSION, has3mf)
  utils/
    __init__.py         - explicit re-exports only (no wildcard imports)
    elevation.py        - elevation API helpers
    generation.py       - runGeneration() orchestration
    geo.py              - coordinate math
    io_gpx.py           - GPX / IGC file parsing
    mesh_ops.py         - bmesh utilities
    metadata.py         - custom property helpers
    osm.py              - Overpass / OSM fetching and caching
    presets.py          - CSV preset load/save
    primitives.py       - curve / mesh creation helpers
    scene.py            - scene-level helpers (show_message_box, etc.)
    terrain.py          - terrain generation pipeline
    text_objects.py     - text and icon mesh helpers
    trail_import.py     - GPX import entry point
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

## 10. No Wildcard Re-exports in `utils/__init__.py`

```python
# ❌
from .mesh_ops import *
from .geo import *

# ✅
from .mesh_ops import selectBottomFaces, recalculateNormals, merge_with_map
from .geo import haversine, convert_to_blender_coordinates
```

Wildcard imports hide name origins, create silent collisions, and break IDE navigation.

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
