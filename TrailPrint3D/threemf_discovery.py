# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2025 Jack Smith (Clonephaze)
"""
3MF API Discovery Helper — Copy this file into your addon.

Provides a single, reliable way to access the 3MF Import/Export addon's
public API from another Blender addon.  Works across Blender restarts and
regardless of addon load order.

**Recommended pattern** (put this at the top of your module)::

    from .threemf_discovery import get_threemf_api

    def my_operator_execute(self, context):
        api = get_threemf_api()
        if api is None:
            self.report({'ERROR'}, "3MF Format addon not installed/enabled")
            return {'CANCELLED'}

        result = api.import_3mf("/path/to/model.3mf")
        if result.status == "FINISHED":
            self.report({'INFO'}, f"Imported {result.num_loaded} objects")
        return {'FINISHED'}

**Alternative — direct try/except** (no helper needed)::

    try:
        from io_mesh_3mf.api import import_3mf, export_3mf
    except ImportError:
        import_3mf = export_3mf = None

Both approaches survive Blender restarts.  The helper adds automatic
addon-path resolution via ``addon_utils`` when a direct import isn't
possible (e.g. the extension repo prefix varies between installs).
"""

from typing import TYPE_CHECKING, Optional, Tuple

import bpy

if TYPE_CHECKING:
    from io_mesh_3mf import api as ThreeMFAPI
else:
    ThreeMFAPI = None

# ── Internal constants ────────────────────────────────────────────────────
_REGISTRY_KEY = "io_mesh_3mf"

# Module-level cache so discovery only runs once per session.
_cached_api: Optional["ThreeMFAPI"] = None


# ── Core discovery ────────────────────────────────────────────────────────

def _discover_api() -> Optional["ThreeMFAPI"]:
    """Locate the 3MF API module using a layered fallback strategy.

    1. ``bpy.app.driver_namespace`` (instant, set by the addon's register())
    2. Direct ``importlib`` import (works when the extension is on sys.path)
    3. ``addon_utils`` scan (handles any extension repo prefix)
    """
    global _cached_api

    # Strategy 1: driver_namespace cache (fastest).
    api = bpy.app.driver_namespace.get(_REGISTRY_KEY)
    if api is not None:
        _cached_api = api
        return api

    import importlib

    # Strategy 2: Direct import — covers the common case where the
    # extension directory is already on sys.path.
    for mod_name in ("io_mesh_3mf.api",):
        try:
            api = importlib.import_module(mod_name)
            # Module is in memory but the addon may have been disabled.
            if getattr(api, "_explicitly_disabled", False):
                return None
            _ensure_registered(api)
            _cached_api = api
            return api
        except ImportError:
            continue

    # Strategy 3: addon_utils scan — finds the addon regardless of the
    # extension repo prefix (blender_org, user_default, custom, …).
    try:
        import addon_utils
        for mod in addon_utils.modules():
            mod_name = mod.__name__
            if mod_name.endswith("ThreeMF_io") or mod_name == "io_mesh_3mf":
                _, is_enabled = addon_utils.check(mod_name)
                if not is_enabled:
                    continue
                try:
                    api = importlib.import_module(mod_name + ".api")
                    _ensure_registered(api)
                    _cached_api = api
                    return api
                except ImportError:
                    continue
    except Exception:
        pass

    return None


def _ensure_registered(api_module) -> None:
    """Make sure the API module is in driver_namespace for fast future lookups.

    Respects ``_explicitly_disabled`` — won't re-register an addon that
    the user intentionally disabled in Preferences.
    """
    if _REGISTRY_KEY not in bpy.app.driver_namespace:
        if getattr(api_module, "_explicitly_disabled", False):
            return
        register_fn = getattr(api_module, "_register_api", None)
        if register_fn is not None:
            try:
                register_fn()
            except Exception:
                pass


# ── Public functions ──────────────────────────────────────────────────────

def get_threemf_api() -> Optional["ThreeMFAPI"]:
    """Return the 3MF API module, or *None* if the addon isn't available.

    This is the **recommended single entry point**.  It resolves the
    addon's import path automatically, caches the result, and ensures
    the module is registered for fast subsequent lookups.

    :return: The ``io_mesh_3mf.api`` module, or ``None``.

    Example::

        api = get_threemf_api()
        if api:
            result = api.import_3mf("/model.3mf")
    """
    global _cached_api
    # driver_namespace is authoritative — always check it first.
    # It's cleared by _unregister_api() when the addon is disabled.
    api = bpy.app.driver_namespace.get(_REGISTRY_KEY)
    if api is not None:
        _cached_api = api
        return api
    # Namespace is empty.  Might be a restart (harmless) or an explicit
    # disable (should return None).  Clear stale cache and re-discover.
    _cached_api = None
    return _discover_api()


def is_threemf_available() -> bool:
    """Check whether the 3MF addon is installed and enabled.

    Equivalent to ``get_threemf_api() is not None`` but reads better
    in boolean contexts.

    :return: True if the 3MF API can be used.
    """
    return get_threemf_api() is not None


def get_threemf_version() -> Optional[Tuple[int, int, int]]:
    """Return the 3MF API version tuple ``(major, minor, patch)``, or *None*.

    :return: Version tuple like ``(1, 0, 0)``, or ``None`` if unavailable.
    """
    api = get_threemf_api()
    if api is not None:
        return getattr(api, "API_VERSION", None)
    return None


def check_threemf_version(minimum: Tuple[int, int, int]) -> bool:
    """Check if the installed 3MF API meets a minimum version requirement.

    :param minimum: Tuple of ``(major, minor, patch)`` minimum version.
    :return: True if the API version >= *minimum*, False otherwise.

    Example::

        if check_threemf_version((1, 2, 0)):
            # Safe to use features added in v1.2.0
            ...
    """
    version = get_threemf_version()
    if version is None:
        return False
    return version >= minimum


def has_threemf_capability(capability: str) -> bool:
    """Check if a specific API capability is supported.

    Use this for forward-compatible feature detection instead of version
    checks.  New capabilities may be added in minor versions.

    Capabilities include:

    - ``"import"``, ``"export"``, ``"inspect"``, ``"batch"``
    - ``"callbacks"`` (on_progress, on_warning, on_object_created)
    - ``"target_collection"``, ``"orca_format"``, ``"prusa_format"``
    - ``"paint_mode"``, ``"project_template"``, ``"object_settings"``
    - ``"building_blocks"``, ``"global_scale"``, ``"compression"``
    - ``"thumbnail"``, ``"use_components"``, ``"auto_smooth"``
    - ``"subdivision_depth"``

    :param capability: Capability name string.
    :return: True if the capability is supported.
    """
    api = get_threemf_api()
    if api is None:
        return False
    capabilities = getattr(api, "API_CAPABILITIES", frozenset())
    return capability in capabilities


# ── Convenience wrappers ──────────────────────────────────────────────────
# These let you call import/export/inspect without touching get_threemf_api()
# directly.  They return None when the addon isn't installed.

def import_3mf(filepath: str, **kwargs):
    """Import a 3MF file.  Returns :class:`ImportResult` or *None*.

    See ``io_mesh_3mf.api.import_3mf`` for full parameter docs.
    """
    api = get_threemf_api()
    if api is None:
        return None
    return api.import_3mf(filepath, **kwargs)


def export_3mf(filepath: str, **kwargs):
    """Export to a 3MF file.  Returns :class:`ExportResult` or *None*.

    See ``io_mesh_3mf.api.export_3mf`` for full parameter docs.
    """
    api = get_threemf_api()
    if api is None:
        return None
    return api.export_3mf(filepath, **kwargs)


def inspect_3mf(filepath: str):
    """Inspect a 3MF file without importing.  Returns :class:`InspectResult` or *None*.

    See ``io_mesh_3mf.api.inspect_3mf`` for full parameter docs.
    """
    api = get_threemf_api()
    if api is None:
        return None
    return api.inspect_3mf(filepath)
