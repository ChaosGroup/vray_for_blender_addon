from xml.dom.minidom import Attr
from vray_blender.lib.defs import ExporterContext, PluginDesc, AttrPlugin
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils
from vray_blender.lib.names import Names
from vray_blender.lib.plugin_utils import objectToAttrPlugin, AttrListValue

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

    def fillLightExclusionAttrs(illuminationShadowType, listAttrName: str, flagAttrName: str, includeExcludeFlag: bool):
        if lightObj.vray.illumination_shadow == illuminationShadowType or lightObj.vray.illumination_shadow == '2':
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

    scene = ctx.dg.scene
    sceneLightObjs = [o.data for o in scene.objects if o.type == 'LIGHT']

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

    for lightObj in sceneLightObjs:
                
        if lightObj.vray.include_exclude == '0':
            continue
        
        lightListsAreValid = True

        # Each list starts with the light object itsels
        excludeList = [plugin_utils.AttrPlugin(Names.object(lightObj))]

        # Get the list of affected objects from the object selector
        excludeList += [objectToAttrPlugin(o) for o in lightObj.vray.objectList.getSelectedItems(ctx.ctx, searchCollection='')]
        
        # include_exclude_light_flags and include_exclude_shadow_flags
        # take '0' for exclusion and '1' for inclusion
        includeExcludeFlag = int(lightObj.vray.include_exclude) - 1

        # Light inclusion/exclusion
        fillLightExclusionAttrs('0', _IGNORED_LIGHTS_ATTR, _INCLUDE_EXCLUDE_LIGHT_ATTR, includeExcludeFlag)
        # Shadows inclusion/exclusion
        fillLightExclusionAttrs('1', _IGNORED_SHADOWS_ATTR, _INCLUDE_EXCLUDE_SHADOW_ATTR, includeExcludeFlag)

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