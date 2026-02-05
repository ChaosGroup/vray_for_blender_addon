import bpy

from vray_blender.lib.defs import ExporterContext, PluginDesc, AttrPlugin
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils
from vray_blender.lib.names import Names
from vray_blender.lib.plugin_utils import objectToAttrPlugin, AttrListValue
from vray_blender.exporting.light_export import getLightMeshInstanceNames

plugin_utils.loadPluginOnModule(globals(), __name__)

# To avoid exporting SettingsLightLinker when there are no changes,
# these variables are stored to compare the previous settings with the current ones.
# This is necessary because the change tracker in ZmqServer does not handle
# lists of plugins
_prevIgnoredLights  = AttrListValue()
_prevIgnoredShadows = AttrListValue()
_prevLightFlags     = []
_prevShadowFlags    = []

_IGNORED_LIGHTS_ATTR          = 'ignored_lights'
_IGNORED_SHADOWS_ATTR         = 'ignored_shadow_lights'
_INCLUDE_EXCLUDE_LIGHT_ATTR   = 'include_exclude_light_flags'
_INCLUDE_EXCLUDE_SHADOW_ATTR  = 'include_exclude_shadow_flags'

def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    global _prevIgnoredLights
    global _prevIgnoredShadows
    global _prevLightFlags
    global _prevShadowFlags

    # These variables are captured by the local functions. Listed here just for clarity.
    light: bpy.types.Light  = None
    excludeList             = []

    def fillLightExclusionAttrs(illuminationShadowType, listAttrName: str, flagAttrName: str, includeExcludeFlag: bool):
        if light.vray.illumination_shadow == illuminationShadowType or light.vray.illumination_shadow == '2':
            if not pluginDesc.getAttribute(listAttrName):
                pluginDesc.setAttribute(listAttrName, plugin_utils.AttrListValue())
            
            pluginDesc.getAttribute(listAttrName).append(excludeList)
            pluginDesc.appendToListAttribute(flagAttrName, includeExcludeFlag)
        
    def listHasChanges(listAttrName: str, listPrevValue):
        return (pluginDesc.getAttribute(listAttrName) or AttrListValue()) != listPrevValue
    
    def flagListHasChanges(listAttrName: str, listPrevValue):
        return (pluginDesc.getAttribute(listAttrName) or []) != listPrevValue


    # "_prevIgnoredLights" and "_prevIgnoredShadows" are reset on full export,
    # to ensure their states remain in sync when renders do not use cache (in production)
    # or renderer is reset
    if ctx.fullExport:
        _resetStoredLists()

    sceneLightObjs = [o for o in ctx.dg.objects if o.type == 'LIGHT']

    # Set all attributes to empty lists so that we could compare against the stored state even
    # if some of the lists are empty.
    pluginDesc.setAttributes({
        _IGNORED_LIGHTS_ATTR: AttrListValue(),
        _IGNORED_SHADOWS_ATTR: AttrListValue(),
        _INCLUDE_EXCLUDE_LIGHT_ATTR: [],
        _INCLUDE_EXCLUDE_SHADOW_ATTR: []
    })

    # Will be set to True if at least one light is included in the light lists
    lightListsAreValid = False

    for obj in sceneLightObjs:
        light = obj.data
        lightPlugins = [] # Light plugins that need exclusion/inclusion lists to be set.

        if light.vray.include_exclude == '0':
            continue

        if light.vray.light_type == 'MESH':
            # All mesh light plugins related to the current mesh light property group
            parentlLightMeshName = Names.object(obj)
            lightPlugins = [AttrPlugin(n) for n in getLightMeshInstanceNames(ctx, parentlLightMeshName)] 
        else:
            lightPlugins = [objectToAttrPlugin(obj)]

        
        for lightPlugin in lightPlugins:
                
            # Each list starts with the light object itsels
            excludeList = [lightPlugin]

            # Get the list of affected objects from the object selector
            excludeList += [objectToAttrPlugin(o) for o in light.vray.objectList.getSelectedItems(ctx.ctx, searchCollection='')]
            
            # include_exclude_light_flags and include_exclude_shadow_flags
            # take '0' for exclusion and '1' for inclusion
            includeExcludeFlag = int(light.vray.include_exclude) - 1

            # Light inclusion/exclusion
            fillLightExclusionAttrs('0', _IGNORED_LIGHTS_ATTR, _INCLUDE_EXCLUDE_LIGHT_ATTR, includeExcludeFlag)
            # Shadows inclusion/exclusion
            fillLightExclusionAttrs('1', _IGNORED_SHADOWS_ATTR, _INCLUDE_EXCLUDE_SHADOW_ATTR, includeExcludeFlag)

            lightListsAreValid = True



    if lightListsAreValid:
        if  (not listHasChanges(_IGNORED_LIGHTS_ATTR, _prevIgnoredLights)) and \
            (not listHasChanges(_IGNORED_SHADOWS_ATTR, _prevIgnoredShadows)) and \
            (not flagListHasChanges(_INCLUDE_EXCLUDE_LIGHT_ATTR, _prevLightFlags)) and \
            (not flagListHasChanges(_INCLUDE_EXCLUDE_SHADOW_ATTR, _prevShadowFlags)):
                return AttrPlugin(pluginDesc.name)
    
        # Store the current state so that we could check for changes agains it during the next export
        _prevIgnoredLights  = pluginDesc.getAttribute(_IGNORED_LIGHTS_ATTR) or AttrListValue()
        _prevIgnoredShadows = pluginDesc.getAttribute(_IGNORED_SHADOWS_ATTR) or AttrListValue()
        _prevLightFlags     = pluginDesc.getAttribute(_INCLUDE_EXCLUDE_LIGHT_ATTR) or []
        _prevShadowFlags    = pluginDesc.getAttribute(_INCLUDE_EXCLUDE_SHADOW_ATTR) or []
    else:
        # Inclusion mode for all lights is set to 'None'
        _resetStoredLists()

        # Reset the SettingsLigtLinker plugin
        pluginDesc.resetAttribute(_IGNORED_LIGHTS_ATTR)
        pluginDesc.resetAttribute(_IGNORED_SHADOWS_ATTR)
        pluginDesc.resetAttribute(_INCLUDE_EXCLUDE_LIGHT_ATTR)
        pluginDesc.resetAttribute(_INCLUDE_EXCLUDE_SHADOW_ATTR)
    
        
    return export_utils.exportPluginCommon(ctx, pluginDesc)


def _resetStoredLists():
    global _prevIgnoredLights, _prevIgnoredShadows, _prevLightFlags, _prevShadowFlags

    _prevIgnoredLights  = AttrListValue()
    _prevIgnoredShadows = AttrListValue()
    _prevLightFlags     = []
    _prevShadowFlags    = []