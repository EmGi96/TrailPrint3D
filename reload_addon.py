# Dev tool: reloads TrailPrint3D in a running Blender session without a
# restart. Paste into Blender's Text Editor (or save it as a Text
# data-block) and run with Alt+P -- not a standalone script, requires the
# addon to already be enabled and TrailPrint3D importable in Blender's
# Python. Submodule order matters: each module must be reloaded only after
# every module it imports from at class-definition/module-scope time.

import importlib
import sys

# Blender 4.2+ extensions are namespaced under bl_ext; legacy addons are not.
_EXT_PKG = "bl_ext.user_default.TrailPrint3D"
_LEG_PKG = "TrailPrint3D"
_pkg = _EXT_PKG if _EXT_PKG in sys.modules else _LEG_PKG
if _pkg not in sys.modules:
    raise RuntimeError("TrailPrint3D is not loaded — enable the addon first, then reload.")

T = sys.modules[_pkg]

def _imp(subpath):
    """Import a submodule that may not yet be an attribute of the package."""
    return importlib.import_module(f"{_pkg}.{subpath}")

# Leaf modules first (no local dependencies)
importlib.reload(T.constants)
importlib.reload(T.temp)
importlib.reload(T.translation)
importlib.reload(T.progress)
importlib.reload(T.addon_preferences)
importlib.reload(T.updater)
importlib.reload(T.threemf_discovery)
importlib.reload(T.export)
importlib.reload(T.picker_server)
importlib.reload(T.panel_guides)

# progress_win.py is normally spawned as its own subprocess (progress.py),
# and only pulled into *this* process via picker_server.py's deferred
# (function-scope) `from . import progress_win` for icon loading -- so like
# satellite/texture/io_geojson below, it may not be an attribute of
# TrailPrint3D yet on a fresh session. Import it explicitly before reloading.
_imp("progress_win")
importlib.reload(T.progress_win)

# Utils sub-modules (all before utils/__init__)
# dataclasses.py has no local deps and is imported at module scope by
# geometry2d/primitives/terrain/elevation/generation.*/utils __init__ --
# must be reloaded before all of them.
importlib.reload(T.utils.dataclasses)
importlib.reload(T.utils.geo)
importlib.reload(T.utils.mesh_ops)
importlib.reload(T.utils.primitives)
importlib.reload(T.utils.scene)
importlib.reload(T.utils.io_gpx)
importlib.reload(T.utils.text_objects)
importlib.reload(T.utils.metadata)
importlib.reload(T.utils.presets)
importlib.reload(T.utils.elevation)
importlib.reload(T.utils.geometry2d)
importlib.reload(T.utils.ui_state)

# satellite.py, texture.py, io_geojson.py, and geotiff.py are only pulled in
# via deferred (function-scope) imports (io_geojson.py specifically only via
# the premium add-on's operators_pe.py), so they may not be an attribute of
# TrailPrint3D.utils yet on a fresh session. Import them explicitly before
# reloading, same as the osm sub-package below.
_imp("utils.satellite")
importlib.reload(T.utils.satellite)
_imp("utils.texture")
importlib.reload(T.utils.texture)
_imp("utils.io_geojson")
importlib.reload(T.utils.io_geojson)
_imp("utils.geotiff")
importlib.reload(T.utils.geotiff)

# osm sub-package: its submodules are only pulled in via deferred (function-
# scope) imports, so reloading TrailPrint3D.utils.osm alone (its empty
# __init__.py) does NOT refresh them -- each must be imported and reloaded
# explicitly, in dependency order.
_imp("utils.osm.bbox_snap")
_imp("utils.osm.exclusions")
_imp("utils.osm.fetch_utils")
_imp("utils.osm.fetch_solo")
_imp("utils.osm.fetch_group")
_imp("utils.osm.gen")
_imp("utils.osm.roads")
_imp("utils.osm.buildings")
_imp("utils.osm.water_polygons")
_imp("utils.osm.prefetch")
# bbox_snap/exclusions are leaves imported by fetch_solo/fetch_group at module
# scope; prefetch imports bbox_snap at module scope (rest is deferred).
importlib.reload(T.utils.osm.bbox_snap)
importlib.reload(T.utils.osm.exclusions)
importlib.reload(T.utils.osm.fetch_utils)
importlib.reload(T.utils.osm.fetch_solo)
importlib.reload(T.utils.osm.fetch_group)
importlib.reload(T.utils.osm.gen)
importlib.reload(T.utils.osm.roads)
importlib.reload(T.utils.osm.buildings)
importlib.reload(T.utils.osm.water_polygons)
importlib.reload(T.utils.osm.prefetch)
importlib.reload(T.utils.osm)

importlib.reload(T.utils.terrain)

# generation sub-package: its submodules are only pulled in via generation/
# __init__.py's own imports, so reloading TrailPrint3D.utils.generation alone
# does NOT refresh them -- each must be imported and reloaded explicitly, in
# dependency order (input/terrain_gen/elements/output have no generation-
# internal deps; orchestrator and tile_orchestrator depend on those four).
_imp("utils.generation.input")
_imp("utils.generation.terrain_gen")
_imp("utils.generation.elements")
_imp("utils.generation.output")
_imp("utils.generation.orchestrator")
_imp("utils.generation.tile_orchestrator")
importlib.reload(T.utils.generation.input)
importlib.reload(T.utils.generation.terrain_gen)
importlib.reload(T.utils.generation.elements)
importlib.reload(T.utils.generation.output)
importlib.reload(T.utils.generation.orchestrator)
importlib.reload(T.utils.generation.tile_orchestrator)
importlib.reload(T.utils.generation)

# Package __init__ after all sub-modules
importlib.reload(T.utils)

# Premium sub-modules (optional — only present in the paid build)
_premium_pkg = f"{_pkg}.premium"
if _premium_pkg in sys.modules:
    _imp("premium.utils_pe")
    _imp("premium.operators_pe")
    importlib.reload(T.premium.utils_pe)
    importlib.reload(T.premium.operators_pe)
    importlib.reload(T.premium)
    print("TrailPrint3D: premium modules reloaded.")
else:
    print("TrailPrint3D: premium not present, skipping.")

# props.py registers update=utils.loadCollections as a function reference at
# class-definition time -- must reload after utils, not in the leaf-modules
# group, or it bakes in the pre-reload function object.
importlib.reload(T.props)

# UI and operators (after utils and props)
importlib.reload(T.panels)
importlib.reload(T.operators)

# Root package last
importlib.reload(T)

T.unregister()
T.register()
print(f"TrailPrint3D reloaded! (package: {_pkg})")
