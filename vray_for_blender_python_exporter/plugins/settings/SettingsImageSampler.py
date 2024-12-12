
from vray_blender.exporting.tools import GEOMETRY_OBJECT_TYPES
from vray_blender.lib.defs import AttrPlugin, ExporterContext, PluginDesc
from vray_blender.lib.names import Names, NamedCounter
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)


def onUpdateRenderMaskMode(srcPropGroup, context, attrName):
    srcPropGroup['dirtyRenderMaskMode'] = True
    

def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup

    if propGroup.get('dirtyRenderMaskMode', False):
        # Recreate the plugin, because the render mask might not update correctly in IPR otherwise.
        _purgeRenderMaskTexturePlugins(ctx)
        export_utils.removePlugin(ctx, pluginDesc.name)
        propGroup['dirtyRenderMaskMode'] = False
    
    if propGroup.type == "1":
        pluginDesc.setAttribute('dmc_minSubdivs', propGroup.dmc_minSubdivs)
        if propGroup.lock_subdivs:
            pluginDesc.setAttribute('dmc_maxSubdivs', propGroup.dmc_minSubdivs)
        elif propGroup.dmc_minSubdivs > propGroup.dmc_maxSubdivs:
            pluginDesc.setAttribute('dmc_minSubdivs', propGroup.dmc_maxSubdivs)
            pluginDesc.setAttribute('dmc_maxSubdivs', propGroup.dmc_minSubdivs)


    if propGroup.progressive_minSubdivs > propGroup.progressive_maxSubdivs:
        pluginDesc.setAttribute('progressive_minSubdivs', propGroup.progressive_maxSubdivs)
        pluginDesc.setAttribute('progressive_maxSubdivs', propGroup.progressive_minSubdivs)


    if propGroup.subdivision_minRate > propGroup.subdivision_maxRate:
        pluginDesc.setAttribute('subdivision_minRate', propGroup.subdivision_maxRate)
        pluginDesc.setAttribute('subdivision_maxRate', propGroup.subdivision_minRate)

    match propGroup.render_mask_mode:
        case '1': # Texture
            pluginTexture = _exportRenderMaskTexture(ctx, propGroup.render_mask_texture_file)
            pluginDesc.setAttributes({
                'render_mask_texture': pluginTexture,
                "render_mask_objects": [],
                "render_mask_object_ids": [],
            })

        case '2': # Objects
            renderMaskObjectsCollection = propGroup.render_mask_collection_selector
            renderMaskObjectPlugins = plugin_utils.collectionToPluginList(renderMaskObjectsCollection, GEOMETRY_OBJECT_TYPES)
            
            if renderMaskObject := propGroup.render_mask_object_selector:
                objPlugin = plugin_utils.objectToAttrPlugin(renderMaskObject)
                if not any([pl for pl in renderMaskObjectPlugins if pl.name == objPlugin.name]):
                    renderMaskObjectPlugins.append(objPlugin)

            if renderMaskObjectPlugins:
                # Remove duplicates which could result from 
                pluginDesc.setAttribute('render_mask_objects', renderMaskObjectPlugins)
            else:
                pluginDesc.setAttribute('render_mask_mode', '0')

            pluginDesc.setAttributes({
                'render_mask_texture': AttrPlugin(),
                "render_mask_object_ids": [],
            })

        case '3': # Object IDs
            if not propGroup.render_mask_object_ids_list:
                pluginDesc.setAttribute('render_mask_mode', '0')
            else:
                maskObjectIDs = propGroup.render_mask_object_ids_list.split(";")
                pluginDesc.setAttribute('render_mask_object_ids', [int(id) for id in maskObjectIDs])

            pluginDesc.setAttributes({
                'render_mask_texture': AttrPlugin(),
                "render_mask_objects": [],
            })

    return export_utils.exportPluginCommon(ctx, pluginDesc)


def _exportRenderMaskTexture(ctx: ExporterContext, imageFileName):
    """ Export the plugin chain for a render mask of type 'texture' read from an external file """

    counter = NamedCounter("settingsImageSampler")

    bitmapBufferName = Names.nextFromCounter(counter, 'BitmapBuffer')
    bitmapBuffer = PluginDesc(bitmapBufferName, 'BitmapBuffer')
    bitmapBuffer.setAttributes({
            "file": imageFileName,
            "rgb_color_space": "",
            "transfer_function": 2 # sRGB
        })
    pluginBitmapBuffer = export_utils.exportPlugin(ctx, bitmapBuffer)

    uvwGenName = Names.nextFromCounter(counter, 'UVWGenEnvironment')
    uvwGen = PluginDesc(uvwGenName,'UVWGenEnvironment')
    uvwGen.setAttribute("mapping_type", "screen")
    pluginUVWGen = export_utils.exportPlugin(ctx, uvwGen)

    texBitmapName = Names.nextFromCounter(counter, 'TexBitmap')
    texBitmap = PluginDesc(texBitmapName, 'TexBitmap')
    texBitmap.setAttributes({
        "bitmap": pluginBitmapBuffer,
        "uvwgen": pluginUVWGen
    })
    pluginTexBitmap = export_utils.exportPlugin(ctx, texBitmap)

    texColorToFloatName = Names.nextFromCounter(counter, 'TexColorToFloat')
    texColorToFloat = PluginDesc(texColorToFloatName, 'TexColorToFloat')
    texColorToFloat.setAttribute("input", pluginTexBitmap)

    return export_utils.exportPlugin(ctx, texColorToFloat)


def _purgeRenderMaskTexturePlugins(ctx: ExporterContext):
    counter = NamedCounter("settingsImageSampler")
    
    # Keep name creation in the same order as in _exportRenderMaskTexture
    bitmapBufferName = Names.nextFromCounter(counter, 'BitmapBuffer')
    uvwGenName = Names.nextFromCounter(counter, 'UVWGenEnvironment')
    texBitmapName = Names.nextFromCounter(counter, 'TexBitmap')
    texColorToFloatName = Names.nextFromCounter(counter, 'TexColorToFloat')

    export_utils.removePlugin(ctx, bitmapBufferName)
    export_utils.removePlugin(ctx, uvwGenName)
    export_utils.removePlugin(ctx, texBitmapName)
    export_utils.removePlugin(ctx, texColorToFloatName)