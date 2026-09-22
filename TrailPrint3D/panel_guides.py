# Guides for the TrailPrint3D UI panels.
import textwrap

import bpy  # type: ignore
from bpy.app.translations import (  # type: ignore
    pgettext_iface as _,  # For Translation of Text Required
)


def draw_wrapped_text(layout, text):
    body_wrap = textwrap.TextWrapper(width=50)
    bullet_wrap = textwrap.TextWrapper(width=43)

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1

        if not line:
            layout.separator(factor=0.4)
            continue

        # Sub-header: short line ending with ':'
        if line.endswith(":") and not line.startswith("-"):
            layout.separator(factor=0.2)
            layout.label(text=line, icon="TRIA_RIGHT")
            continue

        # Bullet list: collect the full run into a single box
        if line.startswith("- "):
            bullet_lines = [line]
            while i < len(lines) and lines[i].strip().startswith("- "):
                bullet_lines.append(lines[i].strip())
                i += 1
            box = layout.box()
            col = box.column(align=True)
            for b in bullet_lines:
                content = b[2:]
                wrapped = bullet_wrap.wrap(content) or [content]
                col.label(text=wrapped[0], icon="DOT")
                for continuation in wrapped[1:]:
                    col.label(text=f"    {continuation}")
            continue

        for w in body_wrap.wrap(line) or [line]:
            layout.label(text=w)


def make_help_panel(
    panel_id,
    label,
    text_content,
    doc_url=None,
):
    def draw(self, context):
        layout = self.layout
        text = _(text_content)

        # First line → callout box with INFO icon
        raw_lines = text.split("\n")
        intro = raw_lines[0].strip()
        body = "\n".join(raw_lines[1:]).strip()

        intro_wrap = textwrap.TextWrapper(width=46)
        box = layout.box()
        col = box.column(align=True)
        intro_lines = intro_wrap.wrap(intro) or [intro]
        col.label(text=intro_lines[0], icon="INFO")
        for line in intro_lines[1:]:
            col.label(text=f"   {line}")

        if body:
            layout.separator(factor=0.3)
            draw_wrapped_text(layout, body)

        if doc_url:
            layout.separator()
            op = layout.operator("wm.url_open", text="Read More", icon="URL")
            op.url = doc_url

    return type(
        panel_id,
        (bpy.types.Panel,),
        {
            "bl_idname": panel_id,
            "bl_label": _(label),
            "bl_space_type": "VIEW_3D",
            "bl_region_type": "UI",
            "bl_ui_units_x": 14,
            "draw": draw,
        },
    )


classes = [
    make_help_panel(
        "TP3D_PT_help_source",
        "About: Source",
        "Load your trail file and set where the result gets saved.\n"
        "\n"
        "Both GPX and IGC files are supported. The name is optional — "
        "leave it blank to use the filename from your trail file.",
    ),
    make_help_panel(
        "TP3D_PT_help_shape",
        "About: Shape",
        "Choose the map shape and physical size of your 3D print.\n"
        "\n"
        "Resolution controls how smooth edges and terrain curves look. "
        "Higher values give more detail but take longer to process — "
        "for most prints, a value around 7 is a good starting point.",
        doc_url="https://trailprint3d.com/howto.html#ht-resolution",
    ),
    make_help_panel(
        "TP3D_PT_help_shape_extras",
        "About: Shape Extras",
        "Add text or symbols around the border of your shape.\n"
        "\n"
        "Use formatting tokens to "
        "pull data directly from your trail file:\n"
        "- {name} — trail name\n"
        "- {length} — total distance\n"
        "- {elevation} — elevation gain\n"
        "- {date} — recorded date\n"
        "- {speed} — average speed\n"
        "- {scale} — map scale",
    ),
    make_help_panel(
        "TP3D_PT_help_scale",
        "About: Scale",
        "Controls how much real-world area fits into your print.\n"
        "\n"
        "Two modes:\n"
        "- Map Scale: auto-fits the trail to fill a set percentage of the print.\n"
        "- Coordinates: pin two real GPS points to exact spots on your print "
        "for a precise, fixed scale.",
    ),
    make_help_panel(
        "TP3D_PT_help_trail",
        "About: Trail",
        "Controls how the trail line is printed.\n"
        "\n"
        "On single-color printers, enable Single Extruder Mode — the trail "
        "becomes a separate object with a matching cutout in the map, so you "
        "can print both pieces in different colors and snap them together.",
    ),
    make_help_panel(
        "TP3D_PT_help_terrain",
        "About: Terrain",
        "Controls how the landscape is shaped in your print.\n"
        "\n"
        "Elevation modes:\n"
        "- Proportional: terrain heights stay true to real-world ratios.\n"
        "- Fixed height: the tallest point is scaled to an exact height.\n"
        "\n"
        "Use Extra Height to add thickness below the terrain, and Smooth "
        "Terrain to reduce blockiness on lower-resolution elevation data.",
    ),
    make_help_panel(
        "TP3D_PT_help_elements",
        "About: Map Elements",
        "Adds real-world features like roads, buildings, and forests to your print.\n"
        "\n"
        "Data sources:\n"
        "- OSM: detailed map data, best for urban or close-up areas.\n"
        "- WorldCover: satellite land-cover, better for wide natural landscapes.\n"
        "\n"
        "In Single Extruder mode, each element is generated as its own "
        "separate printable object.",
    ),
    make_help_panel(
        "TP3D_PT_help_appearance",
        "About: Appearance",
        "Controls whether map elements use a texture or flat per-face colors.\n"
        "\n"
        "Textures produce more accurate results regardless of mesh resolution, "
        "but increase export and slicing time. Including the trail or roads in "
        "the texture merges them into a single object instead of separate parts.",
    ),
]