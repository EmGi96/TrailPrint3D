"""Unit tests for persisted generation-setting snapshots."""

import os
import sys
import traceback

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_passed = 0
_failed = 0


def _run(name, fn):
    global _passed, _failed
    try:
        fn()
        print(f"  PASS  {name}")
        _passed += 1
    except Exception:  # noqa: BLE001
        print(f"  FAIL  {name}")
        traceback.print_exc()
        _failed += 1


def _assert_all_passed():
    print(f"\n{'=' * 60}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 60}\n")
    if _failed:
        raise SystemExit(1)


def test_snapshot_groups_and_filters_metadata():
    from TrailPrint3D.utils.generation_snapshot import GenerationSnapshot

    snapshot = GenerationSnapshot.from_mapping(
        {
            "Object type": "MAP",
            "Shape": "HEXAGON",
            "Elevation Scale": 1.5,
            "road_primary_active": True,
            "map_polygon_wkt": "POLYGON (...)",
            "_private": "hidden",
        }
    )

    assert snapshot.object_type == "MAP"
    assert [section.title for section in snapshot.sections] == [
        "Object",
        "Map",
        "Terrain and Elevation",
        "Map Features",
    ]
    keys = {
        setting.key
        for section in snapshot.sections
        for setting in section.settings
    }
    assert "road_primary_active" in keys
    assert "map_polygon_wkt" not in keys
    assert "_private" not in keys


def test_snapshot_json_round_trip():
    from TrailPrint3D.utils.generation_snapshot import GenerationSnapshot

    snapshot = GenerationSnapshot.from_mapping(
        {
            "Object type": "MAP",
            "Shape": "CIRCLE",
            "Dovetail": False,
            "Map Size in Km": 2.75,
        }
    )
    restored = GenerationSnapshot.from_json(snapshot.to_json())
    assert restored == snapshot
    assert restored.setting_count == 4


def test_snapshot_markdown_format():
    from TrailPrint3D.utils.generation_snapshot import GenerationSnapshot

    snapshot = GenerationSnapshot.from_mapping(
        {
            "Object type": "MAP",
            "Shape": "CIRCLE",
            "Dovetail": False,
            "name": "Lake *Loop*",
        }
    )
    assert snapshot.to_markdown() == (
        "# TrailPrint3D Generation Settings\n"
        "\n"
        "## Object\n"
        "- **Object type:** MAP\n"
        "- **Name:** Lake \\*Loop\\*\n"
        "\n"
        "## Map\n"
        "- **Shape:** CIRCLE\n"
        "\n"
        "## Post Processing\n"
        "- **Dovetail:** Disabled"
    )


def test_snapshot_formats_version_array():
    from TrailPrint3D.utils.generation_snapshot import GenerationSnapshot

    snapshot = GenerationSnapshot.from_mapping(
        {"Object type": "MAP", "Version": (3, 2, 1)}
    )

    assert "- **Version:** 3.2.1" in snapshot.to_markdown()


def test_snapshot_detects_stale_version_display_artifact():
    from TrailPrint3D.utils.generation_snapshot import GenerationSnapshot

    snapshot = GenerationSnapshot.from_json(
        '{"schema_version":1,"object_type":"MAP","sections":['
        '{"title":"Object","settings":['
        '{"key":"Version","value":"<bpy id property array [3]>"}]}]}'
    )

    assert snapshot.has_legacy_display_artifacts


def test_snapshot_rejects_unknown_schema():
    from TrailPrint3D.utils.generation_snapshot import GenerationSnapshot

    try:
        GenerationSnapshot.from_json(
            '{"schema_version":99,"object_type":"MAP","sections":[]}'
        )
    except ValueError as error:
        assert "Unsupported generation snapshot version" in str(error)
    else:
        raise AssertionError("Unknown snapshot schema was accepted")


def test_write_metadata_persists_snapshot():
    import bpy

    from TrailPrint3D import constants as const
    from TrailPrint3D.utils.generation_snapshot import GenerationSnapshot
    from TrailPrint3D.utils.metadata import writeMetadata

    mesh = bpy.data.meshes.new("SnapshotTestMesh")
    obj = bpy.data.objects.new("SnapshotTestObject", mesh)
    try:
        writeMetadata(obj, "OTHER")
        stored = obj.get(const.GENERATION_SNAPSHOT_PROPERTY)
        assert isinstance(stored, str)
        snapshot = GenerationSnapshot.from_json(stored)
        assert snapshot.object_type == "OTHER"
        assert snapshot.setting_count == 2
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)


def test_copy_markdown_operator_writes_clipboard():
    from types import SimpleNamespace

    from TrailPrint3D import panels

    reports = []
    operator = SimpleNamespace(
        report=lambda levels, message: reports.append((levels, message))
    )
    context = SimpleNamespace(
        active_object={"Object type": "MAP", "Shape": "HEXAGON"},
        window_manager=SimpleNamespace(clipboard=""),
    )

    result = panels.TP3D_OT_copy_generation_settings_markdown.execute(
        operator, context
    )

    assert result == {"FINISHED"}
    assert context.window_manager.clipboard.startswith(
        "# TrailPrint3D Generation Settings"
    )
    assert "- **Shape:** HEXAGON" in context.window_manager.clipboard
    assert reports[-1][0] == {"INFO"}


_run("snapshot groups and filters metadata", test_snapshot_groups_and_filters_metadata)
_run("snapshot JSON round trip", test_snapshot_json_round_trip)
_run("snapshot Markdown format", test_snapshot_markdown_format)
_run("snapshot formats version array", test_snapshot_formats_version_array)
_run(
    "snapshot detects stale version display artifact",
    test_snapshot_detects_stale_version_display_artifact,
)
_run("snapshot rejects unknown schema", test_snapshot_rejects_unknown_schema)
_run("writeMetadata persists snapshot", test_write_metadata_persists_snapshot)
_run(
    "copy Markdown operator writes clipboard",
    test_copy_markdown_operator_writes_clipboard,
)
_assert_all_passed()
