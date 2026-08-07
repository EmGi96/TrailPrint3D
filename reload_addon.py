# Dev tool: reloads TrailPrint3D in a running Blender session without a
# restart. Paste into Blender's Text Editor (or save it as a Text
# data-block) and run with Alt+P -- not a standalone script, requires the
# addon to already be enabled and TrailPrint3D importable in Blender's
# Python. Submodule order matters: each module must be reloaded only after
# every module it imports from at class-definition/module-scope time.

import importlib
import TrailPrint3D

# Leaf modules first (no local dependencies)
importlib.reload(TrailPrint3D.constants)
importlib.reload(TrailPrint3D.temp)
importlib.reload(TrailPrint3D.translation)
importlib.reload(TrailPrint3D.progress)
importlib.reload(TrailPrint3D.addon_preferences)
importlib.reload(TrailPrint3D.updater)
importlib.reload(TrailPrint3D.threemf_discovery)
importlib.reload(TrailPrint3D.export)
importlib.reload(TrailPrint3D.picker_server)

# Utils sub-modules (all before utils/__init__)
importlib.reload(TrailPrint3D.utils.geo)
importlib.reload(TrailPrint3D.utils.mesh_ops)
importlib.reload(TrailPrint3D.utils.primitives)
importlib.reload(TrailPrint3D.utils.scene)
importlib.reload(TrailPrint3D.utils.io_gpx)
importlib.reload(TrailPrint3D.utils.text_objects)
importlib.reload(TrailPrint3D.utils.metadata)
importlib.reload(TrailPrint3D.utils.presets)
importlib.reload(TrailPrint3D.utils.elevation)
importlib.reload(TrailPrint3D.utils.osm)
importlib.reload(TrailPrint3D.utils.geometry2d)
importlib.reload(TrailPrint3D.utils.terrain)
importlib.reload(TrailPrint3D.utils.generation)

# Package __init__ after all sub-modules
importlib.reload(TrailPrint3D.utils)

# Premium sub-modules (after utils, before premium/__init__)
importlib.reload(TrailPrint3D.premium.utils_pe)
importlib.reload(TrailPrint3D.premium.operators_pe)
importlib.reload(TrailPrint3D.premium)

# props.py registers update=utils.loadCollections as a function reference at
# class-definition time -- must reload after utils, not in the leaf-modules
# group, or it bakes in the pre-reload function object.
importlib.reload(TrailPrint3D.props)

# UI and operators (after utils and props)
importlib.reload(TrailPrint3D.panels)
importlib.reload(TrailPrint3D.operators)

# Root package last
importlib.reload(TrailPrint3D)

TrailPrint3D.unregister()
TrailPrint3D.register()
print("TrailPrint3D reloaded!")
