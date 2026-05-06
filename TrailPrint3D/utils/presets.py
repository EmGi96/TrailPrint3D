import bpy  # type: ignore
import os
import csv
from .. import constants as const


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

            try:
                value = getattr(props, name)
            except:
                continue

            # Convert lists/tuples to string
            if isinstance(value, (list, tuple)):
                value = ",".join(map(str, value))

            writer.writerow([name, value])

def appendCollection():

    addon_dir = os.path.dirname(__file__)
    filepath = os.path.join(addon_dir, "assets", bpy.context.scene.tp3d.specialBlendFile)
    collection_name = bpy.context.scene.tp3d.specialCollectionName

    #If the collection already exists, delete it and its contents
    collection = bpy.data.collections.get(collection_name)
    if collection:
        bpy.context.scene.collection.children.unlink(collection)
        bpy.data.collections.remove(collection)

    print(f"Collection to Import: {collection_name}")
    with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
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

            #eg jigzaw or slider puzzles
            if "BLANK" in obj.name or "Blank" in obj.name:
                bpy.ops.object.select_all(action='DESELECT')
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj

                parts = collection_name.split("_")
                for part in parts[1:]:          # everything after a "_"
                    num = part.split("mm")[0]   # take what's before "mm"
                    if "mm" in part and num.replace(".", "", 1).isdigit():
                        bpy.context.scene.tp3d.objSize = int(num)
                        break

                return_obj = obj
            if "objSize" in obj.keys():
                scaleFactor = 1/100 * bpy.context.scene.tp3d.objSize
                obj.scale = (scaleFactor, scaleFactor, scaleFactor)

        scene_col.children.unlink(col)
        bpy.data.collections.remove(col)

        return return_obj

    else:
        return None

def get_external_collections(path):
    if not os.path.exists(path):
        return []
    with bpy.data.libraries.load(path, link=True) as (data_from, _):
        return list(data_from.collections)

def loadCollections(self, context):


    addon_dir = os.path.dirname(__file__)
    path = os.path.join(addon_dir, "assets", bpy.context.scene.tp3d.specialBlendFile)
    names = get_external_collections(path)

    const.specialCollection = [(name, name, "") for name in names]

    if not names:
        return

    first_name = names[0]
    if first_name in [item.identifier for item in bpy.context.scene.tp3d.bl_rna.properties["specialCollectionName"].enum_items]:
        bpy.context.scene.tp3d.specialCollectionName = first_name

    bpy.context.scene.tp3d.specialCollectionName = first_name

    print(f"First name: {first_name}")

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

            if not hasattr(props, name):
                continue  # skip unknown properties

            current = getattr(props, name)

            try:
                # Convert back to correct type
                if isinstance(current, bool):
                    value = value.lower() == "true"

                elif isinstance(current, int):
                    value = int(value)

                elif isinstance(current, float):
                    value = float(value)

                elif isinstance(current, (list, tuple)):
                    # Split list stored as comma-separated string
                    value = [float(v) for v in value.split(",")]

                # strings stay strings
            except:
                # Failed conversion → keep original
                continue

            try:
                setattr(props, name, value)
            except:
                pass

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
    except Exception as e:
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
