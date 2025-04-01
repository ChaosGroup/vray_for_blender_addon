from vray_blender import debug
from vray_blender.lib.defs import ExporterContext, PluginDesc, RendererMode
from vray_blender.lib import  export_utils, plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)

def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):

    if ctx.rendererMode in (RendererMode.Production, RendererMode.Interactive):
        # The scene can only have one active light mix render channel. It must have been
        # set to the context while gathering scene light mix info.
        assert ctx.activeLightMixNode is not None

        node = ctx.activeLightMixNode 
        if  node != pluginDesc.node:
            debug.report('WARNING', f"More than one active LightMix nodes found in the scene. '{node.name}' node will not be exported.")

    return export_utils.exportPluginCommon(ctx, pluginDesc)