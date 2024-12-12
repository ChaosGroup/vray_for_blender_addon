import bpy
import bpy.utils.previews

import os


def _getUIIcons():
    # Keep the imports here to avoid circular dependencies
    from vray_blender.ui import menus as ui
    from vray_blender import menu, operators as ops

    return  {
        ui.VRAY_OT_add_dome_camera                : 'DOME_CAMERA',
        ui.VRAY_OT_add_physical_camera            : 'PHYSICAL_CAMERA',
        ui.VRAY_OT_add_object_vray_light_ambient  : 'LIGHT_AMBIENT',
        ui.VRAY_OT_add_object_vray_light_direct   : 'LIGHT_DIRECT',
        ui.VRAY_OT_add_object_vray_light_dome     : 'LIGHT_DOME',
        ui.VRAY_OT_add_object_vray_light_ies      : 'LIGHT_IES',
        ui.VRAY_OT_add_object_vray_light_mesh     : 'LIGHT_MESH',
        ui.VRAY_OT_add_object_vray_light_omni     : 'LIGHT_OMNI',
        ui.VRAY_OT_add_object_vray_light_rect     : 'LIGHT_RECT',
        ui.VRAY_OT_add_object_vray_light_sphere   : 'LIGHT_SPHERE',
        ui.VRAY_OT_add_object_vray_light_spot     : 'LIGHT_SPOT',
        ui.VRAY_OT_add_object_vray_light_sun      : 'LIGHT_SUN',
        ui.VRAY_OT_add_object_vray_sun_sky        : 'SUN_SKY',
        ui.VRAY_OT_add_object_vrayscene           : 'VRAY_SCENE',
        ui.VRAY_OT_add_object_proxy               : 'VRAY_PROXY',
        
        menu.VRAY_OT_show_about_dialog            : 'INFO_ABOUT',
        menu.VRAY_OT_open_collaboration           : 'VRAY_LOGO',
        menu.VRAY_OT_open_cosmos_browser          : 'COSMOS',
        menu.VRAY_OT_open_vfb                     : 'VFB',
        ops.VRAY_OT_cloud_submit                  : 'CLOUD',

        menu.VRAY_OT_render                       : 'RENDER',
        ops.VRAY_OT_render_interactive            : 'RENDER_INTERACTIVE',
        ops.VRAY_OT_render_interactive_stop       : 'RENDER_INTERACTIVE_STOP',
    }

_ICON_FILES = [
        #(ICON_KEY,     FILE_NAME),
        ("VRAY_LOGO",      "vray_logo.png"),
        
        ("LIGHT_AMBIENT",  "VRayLightAmbient.png"),
        ("LIGHT_DIRECT",   "VRayLightDirect.png"),
        ("LIGHT_DOME",     "VRayLightDome.png"),
        ("LIGHT_IES",      "VRayLightIES.png"),
        ('LIGHT_MESH',     "VRayLightMesh.png"),
        ('LIGHT_OMNI',     "VRayLightOmni.png"),
        ('LIGHT_RECT',     "VRayLightRectangle.png"),
        ('LIGHT_SPHERE',   "VRayLightSphere.png"),
        ('LIGHT_SPOT',     "VRayLightSpot.png"),
        ('LIGHT_SUN',      "VRayLightSun.png"),
        ('SUN_SKY',        "VRaySunSky.png"),
        
        ('DOME_CAMERA',     "VRayDomeCamera.png"),
        ('PHYSICAL_CAMERA', "VRayPhysicalCamera.png"),

        ('VRAY_SCENE',      "VRayScene.png"),
        ('VRAY_PROXY',      "VRayProxy.png"),
        
        ('COSMOS',          "CosmosBrowser.png"),
        ('INFO_ABOUT',      "VRayAbout.png"),
        
        ("CLOUD",           "CloudRendering.png"),
        ('VFB',                "VRayVFB.png"),
        ('RENDER',             "VRayProductionRender.png"),
        ("RENDER_INTERACTIVE", "vray_ipr.png"),
        ("RENDER_INTERACTIVE_STOP", "vray_ipr_stop.png"),
        ('RENDER_VIEWPORT',    "VRayInteractiveRender.png"),
    ]


# Icon previews collection, will be loaded during add-on registration
_VRAY_ICONS: bpy.utils.previews.ImagePreviewCollection = None

# Map from a UI element to an icon ID
_UI_ICONS = {}


def getIcon(idIcon: str):
    """ Get Blender's ID of a custom icon """
    return _VRAY_ICONS[idIcon].icon_id


def getUIIcon(element: bpy.types.Struct):
    """ Get Belnder's ID of a custom icon for a UI element """
    return getIcon(_UI_ICONS.get(element, None))


def _loadVRayIcons():
    from vray_blender.lib.path_utils import getIconsDir
    
    icons = bpy.utils.previews.new()
    iconsDir = getIconsDir()
    
    for iconKey, fileName in _ICON_FILES:
        icons.load(iconKey, os.path.join(iconsDir, fileName), 'IMAGE')

    return icons


def register():
    global _UI_ICONS
    global _VRAY_ICONS

    _UI_ICONS   = _getUIIcons()
    _VRAY_ICONS = _loadVRayIcons()


def unregister():
    if _VRAY_ICONS:
        bpy.utils.previews.remove(_VRAY_ICONS)