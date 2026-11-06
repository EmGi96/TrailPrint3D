import csv
import os

import bpy  # type: ignore

from .. import constants as const


def _is_array_like(value):
    """True for lists, tuples, bpy_prop_array, IDPropertyArray, etc.
    False for strings, bytes, and numeric/boolean scalars."""
    if isinstance(value, (str, bytes, bool, int, float)):
        return False
    return isinstance(value, (list, tuple)) or (
        hasattr(value, "__len__") and hasattr(value, "__getitem__")
    )


def _array_to_csv(value):
    return ",".join(str(v) for v in value)


def _csv_to_array(value_str, prop_def):
    parts = value_str.split(",")
    if prop_def is not None and prop_def.type == "BOOL_ARRAY":
        return [p.strip().lower() == "true" for p in parts]
    if prop_def is not None and prop_def.type == "INT_ARRAY":
        return [int(p) for p in parts]
    return [float(p) for p in parts]


def save_myproperties_to_csv(filename):
    """
    Save all writable properties of a MyProperties instance to a CSV file.
    Each row is: property_name , value
    """
    folder = const.preset_dir
    os.makedirs(folder, exist_ok=True)

    filepath = os.path.join(folder, filename + ".csv")

    props = bpy.context.scene.tp3d

    print(filepath)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["property", "value"])  # header

        for p in props.bl_rna.properties:
            name = p.identifier
            if name == "rna_type" or p.is_readonly:
                continue

            if name == "road_types":
                # CollectionProperty -- not a bool/int/float/str/list/tuple,
                # so the generic getattr/writerow below can't handle it.
                # One row per tier: "road_types.<road_id>", "True"/"False".
                for item in props.road_types:
                    writer.writerow([f"road_types.{item.road_id}", item.active])
                continue

            try:
                value = getattr(props, name)
            except AttributeError:
                continue

            # Convert lists/tuples/vector properties to string
            if _is_array_like(value):
                value = _array_to_csv(value)

            writer.writerow([name, value])


def _special_blend_path():
    """Path of the selected specialBlendFile, or None if there's no such file.
    specialBlendFile is a dynamic enum stored as an index, so a file saved in
    Premium (puzzles.blend first) and opened in the free build can read back
    '' (index past the free list's end) -- don't hand that to libraries.load."""
    addon_dir = os.path.dirname(os.path.dirname(__file__))
    _blend = bpy.context.scene.tp3d.specialBlendFile
    if not _blend:
        return None
    _prem = os.path.join(addon_dir, "premium", "assets", _blend)
    path = _prem if os.path.isfile(_prem) else os.path.join(addon_dir, "assets", _blend)
    return path if os.path.isfile(path) else None


def appendCollection():

    filepath = _special_blend_path()
    if filepath is None:
        print("Special collection file not found.")
        return None
    collection_name = bpy.context.scene.tp3d.specialCollectionName

    # If the collection already exists, delete it and its contents
    collection = bpy.data.collections.get(collection_name)
    if collection:
        bpy.context.scene.collection.children.unlink(collection)
        bpy.data.collections.remove(collection)

    print(f"Collection to Import: {collection_name}")
    with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to): # type: ignore
        if collection_name in data_from.collections:
            data_to.collections.append(collection_name)
        else:
            print(f"Collection '{collection_name}' not found.")
            return

    col = bpy.data.collections.get(collection_name)
    if col:
        bpy.context.scene.collection.children.link(col)

        scene_col = bpy.context.scene.collection
        objs = list(col.objects)
        roots = [o for o in objs if not o.parent]
        if roots:
            roots[0].location = bpy.context.scene.cursor.location

        return_obj = None
        for obj in objs:
            scene_col.objects.link(obj)
            col.objects.unlink(obj)

            # eg jigzaw or slider puzzles
            if "BLANK" in obj.name or "Blank" in obj.name or "MpGround" in obj.name:
                bpy.ops.object.select_all(action="DESELECT")
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj

                parts = collection_name.split("_")
                for part in parts[1:]:  # everything after a "_"
                    num = part.split("mm")[0]  # take what's before "mm"
                    if "mm" in part and num.replace(".", "", 1).isdigit():
                        bpy.context.scene.tp3d.objSize = int(num)
                        break

                return_obj = obj
            if "objSize" in obj:
                from ..props import (
                    get_effective_footprint_size,  # deferred to avoid circular import at load time
                )

                # Shapes with a plate (medal/hexagon/octagon backplates) are
                # bigger than the bare objSize by the outer border -- scale the
                # holder to that actual footprint so the plate fits inside it.
                footprint = get_effective_footprint_size(bpy.context.scene.tp3d)
                scaleFactor = footprint / 100
                obj.scale = (scaleFactor, scaleFactor, scaleFactor)

        scene_col.children.unlink(col)
        bpy.data.collections.remove(col)

        return return_obj

    else:
        return None


def get_external_collections(path):
    if not path or not os.path.isfile(path):
        return []
    try:
        with bpy.data.libraries.load(path, link=True) as (data_from, _): # type: ignore
            return list(data_from.collections)
    except OSError as e:
        print(f"Could not read collections from {path}: {e}")
        return []


def loadCollections(self, context):

    names = get_external_collections(_special_blend_path())

    const.specialCollection = [(name, name, "") for name in names]

    if not names:
        return

    first_name = names[0]
    if first_name in [
        item.identifier
        for item in bpy.context.scene.tp3d.bl_rna.properties[
            "specialCollectionName"
        ].enum_items
    ]:
        bpy.context.scene.tp3d.specialCollectionName = first_name

    bpy.context.scene.tp3d.specialCollectionName = first_name


def load_myproperties_from_csv(filename):
    """
    Load all properties from a CSV file and overwrite the values in MyProperties.
    """
    folder = const.preset_dir
    filepath = os.path.join(folder, filename + ".csv")

    if not os.path.isfile(filepath):
        print("Preset file not found:", filepath)
        return

    props = bpy.context.scene.tp3d

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # skip header

        for row in reader:
            if len(row) < 2:
                continue

            name, value = row[0], row[1]

            if name.startswith("road_types."):
                # See save_myproperties_to_csv's matching special-case.
                from ..props import set_road_active  # deferred to avoid circular import

                road_id = name.split(".", 1)[1]
                set_road_active(props, road_id, value.lower() == "true")
                continue

            if not hasattr(props, name):
                continue  # skip unknown properties

            current = getattr(props, name)
            prop_def = props.bl_rna.properties.get(name)

            try:
                # Convert back to correct type
                if isinstance(current, bool):
                    value = value.lower() == "true"

                elif isinstance(current, int):
                    value = int(value)

                elif isinstance(current, float):
                    value = float(value)

                elif _is_array_like(current):
                    value = _csv_to_array(value, prop_def)

                # strings stay strings
            except (ValueError, TypeError):
                # Failed conversion → keep original
                continue

            try:
                setattr(props, name, value)
            except (AttributeError, TypeError, ValueError) as e:
                print(f"Skipping '{name}': {e}")
                continue


def delete_preset_file(preset_name):
    """
    Deletes a preset .csv file from the Blender CONFIG/presets folder.
    preset_name = name WITHOUT extension
    """
    folder = const.preset_dir
    filepath = os.path.join(folder, preset_name + ".csv")

    if not os.path.isfile(filepath):
        print("File not found:", filepath)
        return False

    try:
        os.remove(filepath)
        print("Deleted:", filepath)
        return True
    except OSError as e:
        print("Error deleting file:", e)
        return False


def list_files_callback(self, context):
    folder = const.preset_dir
    items = []

    if os.path.isdir(folder):
        for fname in os.listdir(folder):
            if os.path.isfile(os.path.join(folder, fname)):
                name_no_ext = os.path.splitext(fname)[0]
                items.append((name_no_ext, name_no_ext, ""))

    # Show placeholder if empty
    if not items:
        items.append(("none", "-- No files found --", ""))

    return items
