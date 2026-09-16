# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import bpy.utils.previews
from mathutils import Color

import os


def _getUIIcons():
    # Keep the imports here to avoid circular dependencies
    from vray_blender.ui import menus as ui
    from vray_blender import menu, operators as ops

    return  {
        ui.VRAY_OT_add_physical_camera            : 'PHYSICAL_CAMERA',
        ui.VRAY_OT_add_object_vray_light_ambient  : 'LIGHT_AMBIENT',
        ui.VRAY_OT_add_object_vray_light_direct   : 'LIGHT_DIRECT',
        ui.VRAY_OT_add_object_vray_light_dome     : 'LIGHT_DOME',
        ui.VRAY_OT_add_object_vray_light_ies      : 'LIGHT_IES',
        ui.VRAY_OT_add_object_vray_light_mesh     : 'LIGHT_MESH',
        ui.VRAY_OT_create_mesh_light              : 'LIGHT_MESH',
        ui.VRAY_OT_add_object_vray_light_luminaire: 'LIGHT_LUMINAIRE',
        ui.VRAY_OT_add_object_vray_light_omni     : 'LIGHT_OMNI',
        ui.VRAY_OT_add_object_vray_light_rect     : 'LIGHT_RECT',
        ui.VRAY_OT_add_object_vray_light_sphere   : 'LIGHT_SPHERE',
        ui.VRAY_OT_add_object_vray_light_spot     : 'LIGHT_SPOT',
        ui.VRAY_OT_add_object_vray_light_sun      : 'LIGHT_SUN',
        ui.VRAY_OT_add_object_vray_sun_sky        : 'SUN_SKY',
        ui.VRAY_OT_add_object_vrayscene           : 'VRAY_SCENE',
        ui.VRAY_OT_add_object_proxy               : 'VRAY_PROXY',
        ui.VRAY_OT_add_object_splat               : 'VRAY_GAUSSIANS',
        ui.VRAY_OT_add_object_fur                 : 'VRAY_FUR',
        ui.VRAY_OT_add_object_decal               : 'VRAY_DECAL',
        ui.VRAY_OT_add_object_infinite_plane      : 'VRAY_INFINITE_PLANE',

        menu.VRAY_OT_show_about_dialog            : 'INFO_ABOUT',
        menu.VRAY_OT_open_collaboration           : 'VRAY_LOGO',
        menu.VRAY_OT_open_cosmos_browser          : 'COSMOS',
        menu.VRAY_OT_open_cosmos_ai_generator     : 'COSMOS_AI_GENERATOR',
        menu.VRAY_OT_veras_from_viewport          : 'VERAS_VIEWPORT',
        menu.VRAY_OT_veras_from_vfb               : 'VERAS_VFB',
        menu.VRAY_OT_relink_cosmos_assets         : 'COSMOS_RELINK_ASSETS',
        menu.VRAY_OT_convert_materials            : 'CONVERT_MATERIALS',
        menu.VRAY_OT_make_shadow_catcher          : 'SHADOW_CATCHER',
        menu.VRAY_OT_open_vfb                     : 'VFB',
        ops.VRAY_OT_cloud_submit                  : 'CLOUD',

        menu.VRAY_OT_render                       : 'RENDER_PROD',
        ops.VRAY_OT_render_interactive            : 'RENDER_IPR'
    }

_ICON_FILES = [
        #(ICON_KEY,             FILE_NAME),
        ("VRAY_LOGO",           "vray_logo.svg"),
        ("VRAY_PLACEHOLDER",    "VRayPlaceholder.svg"),

        ("LIGHT_AMBIENT",       "VRayLightAmbient.svg"),
        ("LIGHT_DIRECT",        "VRayLightDirect.svg"),
        ("LIGHT_DOME",          "VRayLightDome.svg"),
        ("LIGHT_IES",           "VRayLightIES.svg"),
        ('LIGHT_LUMINAIRE',     "VRayLightLuminaire.svg"),
        ('LIGHT_MESH',          "VRayLightMesh.svg"),
        ('LIGHT_OMNI',          "VRayLightOmni.svg"),
        ('LIGHT_RECT',          "VRayLightRectangle.svg"),
        ('LIGHT_SPHERE',        "VRayLightSphere.svg"),
        ('LIGHT_SPOT',          "VRayLightSpot.svg"),
        ('LIGHT_SUN',           "VRayLightSun.svg"),
        ('SUN_SKY',             "VRaySunSky.svg"),

        ('PHYSICAL_CAMERA',     "VRayPhysicalCamera.svg"),

        ('VRAY_SCENE',          "VRayScene.svg"),
        ('VRAY_PROXY',          "VRayProxy.svg"),
        ('VRAY_GAUSSIANS',      "VRayGaussians.svg"),
        ('VRAY_FUR',            "VRayFur.svg"),
        ('VRAY_DECAL',          "VRayDecal.svg"),
        ('VRAY_INFINITE_PLANE', "VRayInfinitePlane.svg"),
        ('CHAOS_SCATTER',       "ChaosScatter.svg"),

        ('VERAS_VIEWPORT',      "VerasViewport.svg"),
        ('VERAS_VFB',           "VerasVFB.svg"),

        ('COSMOS',              "CosmosBrowser.svg"),
        ('COSMOS_AI_GENERATOR', "CosmosAIGenerator.svg"),
        ('COSMOS_RELINK_ASSETS',"CosmosRelinkAssets.svg"),
        ('CONVERT_MATERIALS',   "VRayConvertMaterials.svg"),
        ('SHADOW_CATCHER',      "VRayShadowCatcher.svg"),
        ('INFO_ABOUT',          "VRayAbout.svg"),
        ('CHECK_FOR_UPDATES',   "VRayCheckForUpdates.svg"),

        ("CLOUD",               "CloudRendering.svg"),
        ('VFB',                 "VRayVFB.svg"),
        ('RENDER_PROD',         "VRayProductionRender.svg"),
        ("RENDER_IPR_START",    "vray_ipr.svg"),
        ("RENDER_IPR_STOP",     "vray_ipr_stop.svg"),
        ('RENDER_IPR',          "VRayInteractiveRender.svg"),

        ('MTL_VRAY',            "VRayMtl.png"),
        ('MTL_AL_SURFACE',      "VRayAlSurface.png"),
        ('MTL_FAST_SSS2',       "VRayFastSSS2.png"),
        ('MTL_BLEND',           "VRayBlend.png"),
        ('MTL_BUMP',            "VRayBump.png"),
        ('MTL_LIGHT',           "VRayLightMtl.png"),
        ('MTL_HAIR_NEXT',       "VRayHairNext.png"),
        ('MTL_CAR_PAINT2',      "VRayCarPaint2.png"),
        ('MTL_FLAKES',          "VRayFlakesMtl.png"),
        ('MTL_SCANNED',         "VRayScanned.png"),
        ('MTL_STOCHASTIC_FLAKES', "VRayStochasticFlakes.png"),
        ('MTL_TOON',            "VRayToonMtl.png"),
        ('MTL_SWITCH',          "VRaySwitch.png"),
        ('MTL_2SIDED',          "VRay2Sided.png"),
        ('MTL_OVERRIDE',        "VRayOverride.png"),
        ('MTL_DISPLACEMENT',    "VRayDisplacement.png"),
        ('MTL_VRMAT',           "VRayMatMtl.png"),
    ]


# Icon previews collection, will be loaded during add-on registration
_VRAY_ICONS: bpy.utils.previews.ImagePreviewCollection = None

# Map from a UI element to an icon ID
_UI_ICONS = {}


def getIcon(idIcon: str):
    """ Get Blender's ID of a custom icon """
    return _VRAY_ICONS[idIcon].icon_id

    
def getSolidColorIcon(colorLinear):
    """ Get the ID of a dynamic icon with the specified color.

        One persistent icon is cached per distinct color so that several swatches with
        different colors can be shown at the same time (e.g. the lister's per-light Kelvin
        column). A single shared icon would make them all draw the last-written color. """
    import struct

    size = 16

    # Convert scene linear to perceptual color, which is always sRGB
    # in Blender's color picker.
    perceptualColor = Color.from_scene_linear_to_srgb(colorLinear)

    r = int(max(0.0, min(1.0, perceptualColor.r)) * 255)
    g = int(max(0.0, min(1.0, perceptualColor.g)) * 255)
    b = int(max(0.0, min(1.0, perceptualColor.b)) * 255)

    iconKey = f"_SOLID_COLOR_ICON_{r}_{g}_{b}"
    if iconKey in _VRAY_ICONS:
        return _VRAY_ICONS[iconKey].icon_id

    icon = _VRAY_ICONS.new(iconKey)
    icon.icon_size = (size, size)
    icon.is_icon_custom = True

    pixelUnsigned = (255 << 24) | (b << 16) | (g << 8) | r
    pixelSigned = struct.unpack('i', struct.pack('I', pixelUnsigned))[0]

    icon.icon_pixels = [pixelSigned] * (size * size)

    return icon.icon_id


def getUIIcon(element: bpy.types.Struct):
    """ Get Belnder's ID of a custom icon for a UI element """
    return getIcon(_UI_ICONS.get(element, None))


def _loadVRayIcons():
    from vray_blender.lib.path_utils import getIconsDir

    icons = bpy.utils.previews.new()
    iconsDir = getIconsDir()

    for iconKey, fileName in _ICON_FILES:
        preview = icons.load(iconKey, os.path.join(iconsDir, fileName), 'IMAGE')

        # load() only schedules the rasterization, and an icon first drawn in a Preferences
        # window stays blank because that window drops the repaint. Reading pixels forces it.
        _ = preview.icon_pixels_float[:1]

    return icons


def register():
    global _UI_ICONS
    global _VRAY_ICONS

    _UI_ICONS   = _getUIIcons()
    _VRAY_ICONS = _loadVRayIcons()


def unregister():
    if _VRAY_ICONS:
        bpy.utils.previews.remove(_VRAY_ICONS)