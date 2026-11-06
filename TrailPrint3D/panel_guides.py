# Guides for the TrailPrint3D UI panels.
import textwrap

import bpy  # type: ignore
from bpy.app.translations import pgettext_iface as _  # type: ignore

# ---------------------------------------------------------------------------
# Feature detection
# ---------------------------------------------------------------------------


def _has_multiline(layout) -> bool:
    return hasattr(layout, "label_multiline")


def _has_markdown(layout) -> bool:
    return hasattr(layout, "label_markdown")


# ---------------------------------------------------------------------------
# Fallback for Blender 5.1 / 5.2
# ---------------------------------------------------------------------------


def draw_wrapped_text_fallback(layout, text):
    """Manual wrapping used only when label_multiline is unavailable."""
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

        if line.endswith(":") and not line.startswith("-"):
            layout.separator(factor=0.2)
            layout.label(text=line, icon="TRIA_RIGHT")
            continue

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


# ---------------------------------------------------------------------------
# Unified text drawing
# ---------------------------------------------------------------------------


def draw_body_text(layout, text):
    """Prefer label_multiline; fall back to manual wrapping."""
    if _has_multiline(layout):
        layout.label_multiline(text=text)
    else:
        draw_wrapped_text_fallback(layout, text)


def draw_intro_text(layout, intro: str):
    """Draw the first line as a boxed callout with an INFO icon.

    On Blender 5.3+ the text body uses label_markdown so it wraps correctly
    for any language. On 5.1/5.2 it falls back to manual wrapping.
    """
    box = layout.box()

    if _has_markdown(box):
        row = box.row(align=True)
        row.label(text="", icon="INFO")
        row.label_markdown(text=intro)
    else:
        col = box.column(align=True)
        intro_wrap = textwrap.TextWrapper(width=46)
        intro_lines = intro_wrap.wrap(intro) or [intro]
        col.label(text=intro_lines[0], icon="INFO")
        for line in intro_lines[1:]:
            col.label(text=f"   {line}")


# ---------------------------------------------------------------------------
# Panel factory
# ---------------------------------------------------------------------------


def make_help_panel(
    panel_id: str,
    label: str,
    text_content: str,
    doc_url: str | None = None,
):
    def draw(self, context):
        layout = self.layout
        # Wrap the whole content block in the translation context.
        # The context string is optional but helpful if the same English
        # word appears in different panels with different meanings.
        text = _(text_content)

        raw_lines = text.split("\n")
        intro = raw_lines[0].strip()
        body = "\n".join(raw_lines[1:]).strip()

        draw_intro_text(layout, intro)

        if body:
            layout.separator(factor=0.3)
            draw_body_text(layout, body)

        if doc_url:
            layout.separator()
            op = layout.operator("wm.url_open", text=_("Read More"), icon="URL")
            op.url = doc_url

    return type(
        panel_id,
        (bpy.types.Panel,),
        {
            "bl_idname": panel_id,
            "bl_label": _(label),
            "bl_space_type": "VIEW_3D",
            "bl_region_type": "HEADER",
            "bl_options": {"INSTANCED"},
            "bl_ui_units_x": 15,
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
        doc_url="https://trailprint3d.com/howto.html#ht-gpx",
    ),
    make_help_panel(
        "TP3D_PT_help_shape",
        "About: Shape",
        "Choose the map shape and physical size of your 3D print.\n"
        "\n"
        "Resolution controls how smooth edges and terrain curves look. "
        "Higher values give more detail but take longer to process — "
        "for most prints, a value around 7 is a good starting point.",
        doc_url="https://trailprint3d.com/howto.html#ht-shape",
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
        doc_url="https://trailprint3d.com/howto.html#ht-textshapes",
    ),
    make_help_panel(
        "TP3D_PT_help_trail",
        "About: Trail",
        "Controls how much real-world area fits into your print and how "
        "the trail line is printed.\n"
        "\n"
        "Path Scale auto-fits the trail to fill a set percentage of the print.\n"
        "\n"
        "On single-color printers, enable Single Extruder Mode — the trail "
        "becomes a separate object with a matching cutout in the map, so you "
        "can print both pieces in different colors and snap them together.",
        doc_url="https://trailprint3d.com/howto.html#ht-elevation",
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
        doc_url="https://trailprint3d.com/howto.html#ht-resolution",
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
        doc_url="https://trailprint3d.com/howto.html#ht-elements",
    ),
    make_help_panel(
        "TP3D_PT_help_appearance",
        "About: Appearance",
        "Controls whether map elements use a texture or flat per-face colors.\n"
        "Controls how map elements are colored, and whether they use a texture "
        "or flat per-face colors.\n"
        "\n"
        "Color Style picks a built-in or saved set of colors. Open Advanced "
        "Color Settings to adjust individual colors, save them as your own "
        "style, or apply them to maps already generated in this scene, "
        "including Paint textures.\n"
        "\n"
        "Textures produce more accurate results regardless of mesh resolution, "
        "but increase export and slicing time. Including the trail or roads in "
        "the texture means they won't generate as separate objects.\n",
        doc_url="https://trailprint3d.com/howto.html#ht-elements",
    ),
]
