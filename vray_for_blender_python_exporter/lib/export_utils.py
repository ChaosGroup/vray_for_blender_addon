
import os
import contextlib


from vray_blender.lib import attribute_utils, plugin_utils
from vray_blender.lib.defs import ExporterContext, PluginDesc, AttrPlugin, AColor
from vray_blender.nodes.utils import getNonExportablePluginProperties, getNodeOfPropGroup
from vray_blender.plugins import getPluginModule, DEFAULTS_OVERRIDES
from vray_blender import debug
from vray_blender.lib import attribute_types, path_utils, blender_utils

from vray_blender.bin import VRayBlenderLib as vray


def _getOverridesFor(ctx: ExporterContext, pluginType: str):
    mode = "PRODUCTION"
    if ctx.preview:
        mode = "PREVIEW"
    elif ctx.interactive:
        mode = "VIEWPORT"

    overrides: dict = DEFAULTS_OVERRIDES[mode]
    return overrides.get(pluginType, {})


def _convertEnumValue(value):
    # Enum attribute is being reset
    if type(value) is AttrPlugin and value.isEmpty():
        return value

    # Attribute that takes a list of enums
    if type(value) is list:
        return [_convertEnumValue(v) for v in value]

    # Some enums, for whatever reason, are defined only as strings in AppSDK.
    # Try the default conversion to int, if that doesn't work, try converting to string
    with contextlib.suppress(ValueError):
        return int(value)
    return str(value)
        

def _exportPluginParams(ctx: ExporterContext, pluginDesc: PluginDesc):
    overriddenParams = _getOverridesFor(ctx, pluginDesc.type)
    pluginModule = getPluginModule(pluginDesc.type)

    nonExportableParams = getNonExportablePluginProperties(pluginModule)
    
    for attrDesc in pluginModule.Parameters:
        attrName = attrDesc['attr']
        attrType = attrDesc['type']

        # Skip parameters marked as non-exportable in the plugin description
        if attrName in nonExportableParams:
            continue

        options = attrDesc.get('options', {})
        
        # Some attributes are not plugin properties ( i.e. they are only used in Blender ), 
        # but are added to the json descriptions to make them available through the standard 
        # property description mechanism. They are marked with a 'derived' field set to 'true'
        if options.get('derived', False):
            continue
        
        # Skip output attributes
        if attrType in attribute_types.NodeOutputTypes:
            continue

        isExplicit = attrName in pluginDesc.attrs 

        # Type could be skipped, but mappedParams could contain a manually defined value for it
        if attrType in attribute_types.SkippedTypes and not isExplicit:
            continue

        # Skip attributes that should only be exported when their input socket is linked.
        if attrType in attribute_types.AllNodeInputTypes \
                and options.get('linked_only', False) \
                and (not isExplicit):
            continue

        value = None

        if attrName in overriddenParams:
            # Use the mode-specific user override read from the per-rendering-mode overrides/*.json file.
            value = overriddenParams[attrName]
        elif attrName in pluginDesc.attrs:
            # Use the value set from the export code for the plugin. If a node is created for the plugin,
            # the values of the node input sockets have already been added to the attrs collection.
            value = pluginDesc.getAttribute(attrName)

            # This allows us to use None to skip particular parameter export
            if value is None:
                continue
        else:
            # There is no node for the plugin, get the value from its propGroup
            value = getattr(pluginDesc.vrayPropGroup, attrName, None)

        if value is None:
            value = attrDesc['default']
       
        if value is None:
            # 'None' is a valid default for some attribuute types
            value = AttrPlugin()
            
        # Handle special attribute types
        match attrType:
            case 'ENUM':
                value = _convertEnumValue(value)

            case 'STRING':
                subtype = attrDesc.get('subtype')
                
                if subtype == 'FILE_PATH':
                    value = blender_utils.getFullFilepath(value)
                    _copyDrAsset(ctx, value)
                elif subtype == 'DIR_PATH':
                    # Add a trailing slash to directory paths
                    value = os.path.normpath(value) + os.sep
            
            case "COLOR_TEXTURE":
                # Change the name of the attribute to export to the correct sub-attribute of the meta attribute.
                attrName = attrDesc['tex_prop'] if type(value) == AttrPlugin else attrDesc['color_prop']

                # Resetting the 'tex_prop' because most plugins will use it instead of 'color_prop'.
                if attrName == attrDesc['color_prop']: 
                    plugin_utils.updateValue(ctx.renderer, pluginDesc.name, attrDesc['tex_prop'], AttrPlugin())

            case "PLUGIN_USE":
                attrNameUse = attrDesc['use_prop']
                valueUse = getattr(pluginDesc.vrayPropGroup, attrNameUse, None) 
                plugin_utils.updateValue(ctx.renderer, pluginDesc.name, attrNameUse, attribute_utils.convertUIValueToVRay(attrDesc, valueUse))
                continue

        plugin_utils.updateValue(ctx.renderer, pluginDesc.name, attrName, attribute_utils.convertUIValueToVRay(attrDesc, value))
        

    return AttrPlugin(pluginDesc.name)


def _exportTemplates(ctx: ExporterContext, pluginDesc: PluginDesc):
    pluginModule = getPluginModule(pluginDesc.type)

    for attrDesc in [p for p in pluginModule.Parameters if p['type'] == 'TEMPLATE']:
        attrName = attrDesc['attr']
        template = getattr(pluginDesc.vrayPropGroup, attrName)
        
        if not attrDesc['options']['template'].get('custom_exporter', False):
            template.exportToPluginDesc(ctx, pluginDesc)


# Export plugin parameters that do not require special handling
# NOTE: You could use this function from inside module's 'exportCustom'
# @param overrideParams - override the default param export 
def exportPluginCommon(ctx: ExporterContext, pluginDesc: PluginDesc) -> AttrPlugin:
    vray.pluginCreate(ctx.renderer, pluginDesc.name, pluginDesc.type)
    
    _exportTemplates(ctx, pluginDesc)
    return _exportPluginParams(ctx, pluginDesc)


def exportPlugin(ctx: ExporterContext, pluginDesc: PluginDesc) -> AttrPlugin:
    pluginModule = getPluginModule(pluginDesc.type)
    
    if not hasattr(pluginModule, 'Parameters'):
        debug.printError(f"Module {pluginDesc.type} doesn't have any parameters defined!")
        return AttrPlugin()
    
    try:
        if hasattr(pluginModule, 'exportCustom'):
            # Plugins may override part or all of the export procedure
            return pluginModule.exportCustom(ctx, pluginDesc)

        result = exportPluginCommon(ctx, pluginDesc)
    except Exception as ex:
        debug.printError(f"{type(ex).__name__} while exporting plugin {pluginDesc.name}::{pluginDesc.type}: {ex}")
        # Rethrow the original exception
        raise

    return result


def _copyDrAsset( ctx: ExporterContext, filePath: str):
    scene = ctx.dg.scene
    vrayDR = scene.vray.VRayDR
    if vrayDR.on:
        if vrayDR.assetSharing == 'SHARE':
            value = path_utils.copyDRAsset(scene, filePath)


def removePlugin(exporterCtx: ExporterContext, pluginName):
    vray.pluginRemove(exporterCtx.renderer, pluginName)