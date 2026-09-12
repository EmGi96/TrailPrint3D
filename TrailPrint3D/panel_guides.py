# Guides for the TrailPrint3D UI panels.
import textwrap

import bpy  # type: ignore
from bpy.app.translations import (  # type: ignore
    pgettext_iface as _,  # For Translation of Text Required
)


def draw_wrapped_text(layout, text):
    wrapper = textwrap.TextWrapper(width=50)
    # Split on single newlines to catch any manual line breaks
    lines = text.split("\n")

    for line in lines:
        if not line.strip():
            # If it's an empty line (like from a \n\n), just add a blank label
            layout.label(text="")
            continue

        wrapped_lines = wrapper.wrap(text=line)
        for w_line in wrapped_lines:
            layout.label(text=w_line)


def make_help_panel(
    panel_id,
    label,
    text_content,
    doc_url=None,
):
    def draw(self, context):
        layout = self.layout

        # The main tutorial text
        draw_wrapped_text(layout, _(text_content))

        # Optional Link to full documentation/video
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
        "Choose your GPX file, an export path, and a name \n\n"
        "Your GPX file ",
        "https://example.com/source-guide",
    ),
    make_help_panel(
        "TP3D_PT_help_shape",
        "About: Shape",
        "The physical footprint of the print. \n\nText-based shapes (e.g. Hexagon Outer Text) add a backplate for a title — their extra settings appear below once you pick one. Shell styles add a hollow wall instead of a solid print.",
    ),
    make_help_panel(
        "TP3D_PT_help_scale",
        "About: Scale",
        "Choose how to find the area of your map. \n\nMap Scale: Scale your map so the trail fits to this amount of your maps area. \nCoordinates: Calculate the scale by using four Latitude/Longitude coordinates.",
    ),
    make_help_panel(
        "TP3D_PT_help_trail",
        "About: Trail",
        "Settings for the printed trail line itself.  \n\nSet the mm width of the trail, choose if you want it to be made in Single-color mode, and adjust its height from the terrain if applicable.",
    ),
    make_help_panel(
        "TP3D_PT_help_terrain",
        "About: Terrain",
        "Controls for the terrain surrounding the trail. \n\nAdjust the height, smoothing, and other terrain-specific settings to achieve the desired topography.",
    ),
    make_help_panel(
        "TP3D_PT_help_elements",
        "About: Elements",
        "Settings for the various map elements such as roads, buildings, and vegetation. \n\nAdjust their visibility, height, and other properties to customize the map's appearance.",
    ),
    make_help_panel(
        "TP3D_PT_help_appearance",
        "About: Appearance",
        "Settings for the visual appearance of the map. \n\nColors, textures, and other aesthetic options to enhance the overall look of the printed map.",
    ),
    make_help_panel(
        "TP3D_PT_help_elementSource",
        "About: Element Sources",
        "Choose the source of map elements \n\nOSM: OpenStreetMap, good for maps less than 500km (310 miles) in size",
    ),
]
