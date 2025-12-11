import bpy
import math

from vray_blender import debug
from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.lib import export_utils, plugin_utils
from vray_blender.lib.defs import AttrPlugin, ExporterContext, PluginDesc
from vray_blender.lib.names import Names
from vray_blender.lib.plugin_utils import objectToAttrPlugin, stringToIntList
from vray_blender.lib.sys_utils import isGPUEngine
from vray_blender.nodes.filters import filterRenderMasks
from vray_blender.plugins.geometry.GeomHair import getGeomHairPluginName
from vray_blender.bin import VRayBlenderLib as vray

plugin_utils.loadPluginOnModule(globals(), __name__)


def onUpdateSamplesLimit(propGroup, context, attrName):
    propGroup.dmc_maxSubdivs = _samplesLimitToMaxSubdivs(propGroup.samples_limit)


def widgetDrawExternalProperties(context, layout: bpy.types.UILayout, propGroup, widgetAttr):
    vrayScene = context.scene.vray
    attrName = widgetAttr['name']

    match attrName:
        case "gpu_ray_bundle":
            layout.prop(vrayScene.SettingsRTEngine, "gpu_bundle_size")
            layout.prop(vrayScene.SettingsRTEngine, "gpu_samples_per_pixel")
        case "animated_noise_pattern":
            layout.prop(vrayScene.SettingsDMCSampler, "time_dependent")
        case "use_blue_noise_optimization":
            layout.prop(vrayScene.SettingsDMCSampler, "use_blue_noise_optimization")


def isCpuProgressive(propGroup, node):
    return not isGPUEngine(bpy.context.scene) and (propGroup.type == '3')

def isCpuAdaptive(propGroup, node):
    return not isGPUEngine(bpy.context.scene) and (propGroup.type == '1')

def isGpuProgressive(propGroup, node):
    return isGPUEngine(bpy.context.scene) and (propGroup.type == '3')

def isGpuAdaptive(propGroup, node):
    return isGPUEngine(bpy.context.scene) and (propGroup.type == '1')


# Converters
def _samplesLimitToMaxSubdivs(samplesLimit: int):
    return max(1, int(math.sqrt(float(samplesLimit)) - 2.0 ))

def _raysPerPixelToMinSubdivs(raysPerPixel: int):
    return int(math.sqrt(float(raysPerPixel) * 4.0))

def _maxSubdivsCPUToSamplesLimit(maxSubdivs: int):
    return (maxSubdivs * 2 + 2)**2

def _maxSubdivsGPUToSamplesLimit(maxSubdivs: int):
    return (maxSubdivs + 2)**2



# Export
def exportCustom(exporterCtx: ExporterContext, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup
    context = exporterCtx.ctx
    vrayScene = context.scene.vray
    isGpuEngine = isGPUEngine(context.scene)
    isAdaptive = propGroup.type == '1'

    if isGpuEngine:
        pluginDesc.setAttribute('dmc_minSubdivs', _raysPerPixelToMinSubdivs(vrayScene.SettingsRTEngine.gpu_samples_per_pixel))
        pluginDesc.setAttribute('dmc_maxSubdivs', _samplesLimitToMaxSubdivs(propGroup.samples_limit))
    else:
        minSubdivs = propGroup.dmc_minSubdivs
        maxSubdivs = propGroup.dmc_maxSubdivs

        if isAdaptive and propGroup.lock_subdivs:
            maxSubdivs = minSubdivs

        if maxSubdivs < minSubdivs:
            # Swap values if the range is reversed
            minSubdivs, maxSubdivs = maxSubdivs, minSubdivs

        pluginDesc.setAttribute('dmc_minSubdivs', minSubdivs)
        pluginDesc.setAttribute('dmc_maxSubdivs', maxSubdivs)

    if (not isAdaptive) and (propGroup.progressive_minSubdivs > propGroup.progressive_maxSubdivs):
        pluginDesc.setAttribute('progressive_minSubdivs', propGroup.progressive_maxSubdivs)
        pluginDesc.setAttribute('progressive_maxSubdivs', propGroup.progressive_minSubdivs)


    if propGroup.subdivision_minRate > propGroup.subdivision_maxRate:
        pluginDesc.setAttribute('subdivision_minRate', propGroup.subdivision_maxRate)
        pluginDesc.setAttribute('subdivision_maxRate', propGroup.subdivision_minRate)

    match propGroup.render_mask_mode:
        case '1': # Texture
            pluginTexture = _exportRenderMaskTexture(exporterCtx, propGroup.render_mask_texture_file)
            pluginDesc.setAttributes({
                'render_mask_mode': 1,
                'render_mask_texture': pluginTexture,
                "render_mask_objects": [],
                "render_mask_object_ids": [],
            })

        case '2' | '4': # Objects / Selected
            if propGroup.render_mask_mode == '2':
                selected = set(propGroup.object_selector.getSelectedItems(exporterCtx.ctx, asIncludes=True))
            else:
                selected = {o for o in exporterCtx.ctx.selected_objects if filterRenderMasks(o)}

            # V-Ray Fur requires special handling because we need to add the gizmo objects to the render mask
            # instead of the V-Ray Fur object.
            removeFromSelected = set()
            addToPlugins = set()

            for o in selected:
                if o.vray.isVRayFur:
                    if furInfo := next((i for i in exporterCtx.activeFurInfo if i.parentObjTrackId == getObjTrackId(o))):
                        furNodeName = Names.vrayNode(getGeomHairPluginName(furInfo.parentName, furInfo.gizmoObjName))
                        removeFromSelected.add(o)
                        addToPlugins.add(AttrPlugin(furNodeName, pluginType='Node'))

            objPlugins = [objectToAttrPlugin(o) for o in selected.difference(removeFromSelected)]
            objPlugins.extend(addToPlugins)

            for plugin in objPlugins:
                vray.pluginCreate(exporterCtx.renderer, plugin.name, plugin.pluginType)

            pluginDesc.setAttributes({
                'render_mask_mode': 2,
                'render_mask_objects': objPlugins,
                'render_mask_texture': AttrPlugin(),
                "render_mask_object_ids": [],
            })

        case '3': # Object IDs
            if not propGroup.render_mask_object_ids_list:
                pluginDesc.setAttribute('render_mask_mode', '0')
            else:
                if (maskObjectIDs := stringToIntList(propGroup.render_mask_object_ids_list, ';')) is not None:
                    pluginDesc.setAttribute('render_mask_object_ids', [int(id) for id in maskObjectIDs])
                else:
                    debug.reportError(f"Invalid object ID list in V-Ray Render Mask settings: {propGroup.render_mask_object_ids_list}")

            pluginDesc.setAttributes({
                'render_mask_mode': 3,
                'render_mask_texture': AttrPlugin(),
                "render_mask_objects": [],
            })

    return export_utils.exportPluginCommon(exporterCtx, pluginDesc)


def _exportRenderMaskTexture(ctx: ExporterContext, imageFileName, allowNegativeColors=True):
    """ Export the plugin chain for a render mask of type 'texture' read from an external file """

    uvwGenName = "settingsImageSampler|uvwgen"
    uvwGen = PluginDesc(uvwGenName, 'UVWGenEnvironment')
    uvwGen.setAttribute("mapping_type", "screen")
    pluginUVWGen = export_utils.exportPlugin(ctx, uvwGen)

    bitmap = export_utils.exportRenderMaskBitmap(ctx, imageFileName, pluginUVWGen, "settingsImageSampler", False)
    bitmap.output = "out_intensity"
    return bitmap


def _purgeRenderMaskTexturePlugins(ctx: ExporterContext):
    # Keep name creation in the same order as in _exportRenderMaskTexture
    uvwGenName = "settingsImageSampler|uvwgen"

    export_utils.removePlugin(ctx, uvwGenName)
    export_utils.removeBitmapMaskPlugins(ctx, "settingsImageSampler")