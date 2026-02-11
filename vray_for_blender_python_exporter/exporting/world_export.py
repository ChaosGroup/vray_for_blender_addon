# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.exporting.plugin_tracker import TrackObj, TrackNode, getObjTrackId, getConnectedTrackIds, log as trackerLog
from vray_blender.exporting.tools import *
from vray_blender.exporting.node_export import *
from vray_blender.lib import export_utils
from vray_blender.lib.plugin_utils import updateValue, DISABLE_GEN_AI
from vray_blender.lib.defs import *
from vray_blender.nodes.tools import isVrayNodeTree
from vray_blender.nodes import utils as NodesUtils

import mathutils

# Default color used in SettingsEnvironment plugin for background, gi, reflection and refraction
DEFAULT_ENVIRONMENT_COLOR = mathutils.Color((0.0, 0.0, 0.0))
ENVIRONMENT_OVERRIDES = (
    ('bg_color', 'bg_tex', 'bg_tex_mult', 'use_bg'),
    ('gi_color', 'gi_tex', 'gi_tex_mult', 'use_gi'),
    ('reflect_color', 'reflect_tex', 'reflect_tex_mult', 'use_reflect'),
    ('refract_color', 'refract_tex', 'refract_tex_mult', 'use_refract'),
    ('secondary_matte_color', 'secondary_matte_tex', 'secondary_matte_tex_mult', 'use_secondary_matte'),
)


def _getExportedEffectsList(nodeCtx: NodeContext):
    """ Parses all effect nodes and sets the 'environment_volume' attribute of SettingsEnvironment plugin """

    worldOutputNode = nodeCtx.node
    effectsSock = getInputSocketByName(worldOutputNode, "Effects")
    environmentVolume = []

    if link := getFarNodeLink(effectsSock):
        effectsNode = link.from_node

        if effectsNode.bl_idname != "VRayNodeEffectsHolder":
            debug.printError("Environment: 'Effects' socket must be connected to a \"V-Ray Effects Container\" node!")
            return

        with TrackNode(nodeCtx.nodeTracker, getNodeTrackId(effectsNode)):
            with nodeCtx.push(effectsNode):
                if nodeCtx.getCachedNodePlugin(effectsNode) is None:
                    nodeCtx.cacheNodePlugin(effectsNode)
                    for inSock in effectsNode.inputs:
                        if effectLink := getFarNodeLink(inSock):
                            effect = exportSocketLink(nodeCtx, effectLink)
                            if type(effect) is AttrPlugin:
                                environmentVolume.append(effect)

    return environmentVolume



def _sockConnectedToDenoiser(sock):
    node = getFarNodeLink(sock).from_node
    return (node is not None) and (node.bl_idname == "VRayNodeRenderChannelDenoiser")

def _sockConnectedToCryptomatte(sock):
    node = getFarNodeLink(sock).from_node
    return (node is not None) and (node.bl_idname == "VRayNodeRenderChannelCryptomatte")

def _sockConnectedToEnhancer(sock):
    node = getFarNodeLink(sock).from_node
    return (node is not None) and (node.bl_idname == "VRayNodeRenderChannelEnhancer")

def _getViewportDenoiserEngine(world: bpy.types.World):
    viewportEngine = world.vray.RenderChannelDenoiser.viewport_engine
    upscaling = False
    if viewportEngine == "3":
        viewportEngine = "1"
        upscaling = True
    return viewportEngine, upscaling

def _exportViewportDenoiser(nodeCtx: NodeContext):
    denoiserPluginName = Names.singletonPlugin("RenderChannelDenoiser")
    viewportDenoiserEngine, upscaling = _getViewportDenoiserEngine(nodeCtx.rootObj)
    # Export denoiser manually for viewport renders if no denoiser node is available.
    pluginDesc = PluginDesc(denoiserPluginName, "RenderChannelDenoiser")
    pluginDesc.setAttribute("enabled", True)
    pluginDesc.setAttribute("engine", viewportDenoiserEngine)
    pluginDesc.setAttribute("upscaling", upscaling)
    export_utils.exportPlugin(nodeCtx.exporterCtx, pluginDesc)

class WorldExporter(ExporterBase):
    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        self.exported = set()
        self.nodeTracker = ctx.nodeTrackers['WORLD']
        self.denoiserExported = False

    def _getNodeContext(self, world: bpy.types.World):
        nodeCtx = NodeContext(self, None, self.ctx.scene, self.renderer)
        nodeCtx.nodeTracker = self.nodeTrackers["WORLD"]
        nodeCtx.ntree = world.node_tree
        nodeCtx.rootObj = world

        return nodeCtx

    def _exportRenderChannels(self, nodeCtx: NodeContext):
        """ Export the SettingsRenderChannels and RenderChannelXXX plugins along 
            with any associated node trees.
            Returns true if a denoiser render element was exported.
        """
        # NOTE: There is no need to track any  plugins here, because render channels
        # (except the denoiser channel which is handled specifically)
        # are only exported in production using full export.

        if not (channelsNode := NodesUtils.getChannelsOutputNode(nodeCtx.ntree)):
            return []

        exportSettingsPlugin = False

        # Export channel plugins and their node trees
        if channelsNode:
            viewportDenoiserEnabled = nodeCtx.scene.world.vray.RenderChannelDenoiser.viewport_enabled

            with nodeCtx.push(channelsNode):
                if nodeCtx.getCachedNodePlugin(channelsNode) is None: # Node already exported
                    nodeCtx.cacheNodePlugin(channelsNode)
                    for channelLink in [getFarNodeLink(s) for s in channelsNode.inputs]:
                        if not channelLink:
                            continue

                        inSock = channelLink.to_socket
                        if nodeCtx.exporterCtx.vantage and (_sockConnectedToCryptomatte(inSock) or _sockConnectedToEnhancer(inSock)):
                            continue
                        if DISABLE_GEN_AI and _sockConnectedToEnhancer(inSock):
                            continue

                        if not nodeCtx.exporterCtx.viewport or (_sockConnectedToDenoiser(inSock) and viewportDenoiserEnabled):
                            exportSocketLink(nodeCtx, channelLink)
                            exportSettingsPlugin = True

        # Only the denoiser render element is exported for viewport IPR.
        self.denoiserExported = nodeCtx.exporterCtx.viewport and exportSettingsPlugin
        if self.denoiserExported:
            # The viewport parameters serve as overrides for the denoiser node.
            denoiserPluginName = Names.singletonPlugin("RenderChannelDenoiser")
            viewportDenoiserEngine, upscaling = _getViewportDenoiserEngine(nodeCtx.rootObj)
            updateValue(nodeCtx.renderer, denoiserPluginName, "engine", int(viewportDenoiserEngine))
            updateValue(nodeCtx.renderer, denoiserPluginName, "optix_use_upscale", upscaling)

        # Export the SettingsRenderChannels plugin if any of its sockets are connected
        if exportSettingsPlugin:
            propGroup = nodeCtx.scene.vray.SettingsRenderChannels
            settingsRenderChannels = PluginDesc(Names.singletonPlugin('SettingsRenderChannels'), "SettingsRenderChannels")
            settingsRenderChannels.setAttribute("unfiltered_fragment_method", propGroup.unfiltered_fragment_method)
            settingsRenderChannels.setAttribute("deep_merge_mode", propGroup.deep_merge_mode)
            settingsRenderChannels.setAttribute("deep_merge_coeff", propGroup.deep_merge_coeff)

            export_utils.exportPlugin(nodeCtx.exporterCtx, settingsRenderChannels)

    def _exportWorld(self, world: bpy.types.World):
        if not world or not world.node_tree:
            return

        nodeOutput = NodesUtils.getOutputNode(world.node_tree, 'WORLD')

        if not world.original.vray.is_vray_class:
            # Cannot export non-vray node trees
            if nodeOutput:
                debug.report(severity="WARNING",
                             msg=f"The World tree '{world.name}' has a V-Ray Output node but no V-Ray node tree."\
                                " Check if 'Use V-Ray World Nodes' has been pressed")
            return

        if not nodeOutput:
            debug.printError(f"Output node not found in world tree '{world.name}'")
            return

        nodeCtx = self._getNodeContext(world)
        with (  nodeCtx,
                nodeCtx.push(nodeOutput),
                TrackObj(self.nodeTracker, getObjTrackId(world)),
                TrackNode(self.nodeTracker, getNodeTrackId(nodeOutput))):
            if nodeCtx.getCachedNodePlugin(nodeOutput) is None:
                nodeCtx.cacheNodePlugin(nodeOutput)
                self._exportEnvironmentSettings(nodeCtx)

        # Render channel export is kept outside the 'Tracking' scope,  
        # as they should be exported only once and remain unchanged.  
        # Removing them during interactive rendering could cause V-Ray to crash.
        if self.fullExport:
            # We are not currently exporting render elements other than the Color image and denoiser in the viewport.
            self._exportRenderChannels(nodeCtx)


    def export(self):
        if self.ctx.scene.world is None:
            return

        world = self.ctx.scene.world.evaluated_get(self.dg)

        self._exportWorld(world)
        if self.viewport and self.fullExport and not self.denoiserExported and world.vray.RenderChannelDenoiser.viewport_enabled:
            # If not render elements were exported then we need to export a denoiser plugin for viewport IPR manually.
            nodeCtx = self._getNodeContext(world)
            _exportViewportDenoiser(nodeCtx)

    def _exportEnvironmentSettings(self, nodeCtx: NodeContext):
        """ Gets settings from 'Environment' node and applies them to SettingsEnvironment plugin """
        pluginDesc = PluginDesc(Names.singletonPlugin("SettingsEnvironment"), "SettingsEnvironment")

        # Parse all effect nodes and sets the 'environment_volume' attribute of SettingsEnvironment plugin
        environmentVolume = _getExportedEffectsList(nodeCtx)
        pluginDesc.setAttribute("environment_volume", environmentVolume)

        globalLightLevel = self.ctx.scene.world.vray.global_light_level
        pluginDesc.setAttribute("global_light_level", mathutils.Color((globalLightLevel, globalLightLevel, globalLightLevel)))

        worldOutputNode = nodeCtx.node
        envSock = getInputSocketByName(worldOutputNode, "Environment")

        if envLink := getFarNodeLink(envSock):
            envNode = envLink.from_node
            
            if envNode.bl_idname != "VRayNodeEnvironment":
                debug.printError("Environment: 'Environment' socket must be connected to 'Environment' node!")
                return

            # Overrides
            with nodeCtx.push(envNode):
                if nodeCtx.getCachedNodePlugin(envNode) is not None: # Node is already exported
                    return

                for input in ENVIRONMENT_OVERRIDES:
                    colorAttrName   = input[0] 
                    texAttrName     = input[1] 
                    texMultAttrName = input[2] 
                    texUseAttrName  = input[3] 
                
                    sock = getInputSocketByAttr(envNode, texAttrName)

                    color = sock.value
                    mult = sock.multiplier
                    
                    if sock.use:
                        if link := getFarNodeLink(sock):
                            tex = exportSocketLink(nodeCtx, link)
                        else:
                            # On GPU, the 'xxx_color' properties do not work correctly. As a workaround, export the color as a plugin.
                            texPlugin = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexColorConstant"), "TexColorConstant")
                            
                            # The multiplier property works as a blend factor on CPU and as a multplier 
                            # on the GPU so we always export it as 1 and apply the multiplication to the color itself.
                            multColor = AColor((color.r * mult, color.g * mult, color.b * mult, 1.0))
                            texPlugin.setAttribute("color", multColor)
                            
                            tex = exportPluginWithStats(nodeCtx, texPlugin)
                            mult = 1.0
                    else:
                        # Reset all properties (needed in GPU mode)
                        color = AColor((0.0, 0.0, 0.0, 1.0))
                        tex = AttrPlugin()
                        mult = 1.0

                    pluginDesc.setAttribute(colorAttrName, color)
                    pluginDesc.setAttribute(texAttrName, tex)
                    pluginDesc.setAttribute(texMultAttrName, mult)
                    pluginDesc.setAttribute(texUseAttrName, sock.use)
        
                nodeCtx.cacheNodePlugin(envNode)
        
        exportPluginWithStats(nodeCtx, pluginDesc)
        

    def prunePlugins(self):
        """ Delete all plugins associated with removed, orphaned or updated worlds """
        assert(self.interactive)

        # Find all lights that are to be shown in the scene
        activeWorlds = [w for w in bpy.data.worlds if w.vray and (not isObjectOrphaned(w))]
        
        # Remove from VRay the lights with node trees whose topology has been updated. 
        # They will be fully re-exported during the current update cycle 
        topologyUpdates = self._getTopologyUpdates()
        updatedWorldIds = [w for w in activeWorlds if self.fullExport or (w.name in topologyUpdates)]

        self._pruneNodeTreePlugins(updatedWorldIds)


    def _pruneNodeTreePlugins(self, removeWorlds):
        """ Remove plugins for 'World' node trees """
        if not self.interactive:
            return
        
        def forgetNodes(worldId, nodeIds):
            if not nodeIds:
                return
            
            for nodeId in nodeIds:
                for pluginName in self.nodeTracker.getNodePlugins(worldId, nodeId):
                    vray.pluginRemove(self.renderer, pluginName)
                    trackerLog(f"REMOVE NODE PLUGIN: {pluginName}")
                self.nodeTracker.forgetNode(worldId, nodeId)

        for w in removeWorlds:
            trackId = getObjTrackId(w)
            nodesForRemoval = self.nodeTracker.getOwnedNodes(trackId)

            # Render Channel nodes should be removed only during full export, otherwise the renderer will crash
            if (not self.fullExport) and (channelsNode := NodesUtils.getChannelsOutputNode(w.node_tree)):
                channelTrackIds = getConnectedTrackIds(channelsNode)
                nodesForRemoval = [n for n in nodesForRemoval if n not in channelTrackIds] 

            forgetNodes(trackId, nodesForRemoval)


    def _getTopologyUpdates(self):
            """ Return the names of the World data objects with node trees whose topology has been updated """
            
            ntreesWithUpdatedTopology = [ u.id for u in self.dg.updates \
                                            if isinstance(u.id, bpy.types.NodeTree) \
                                                and isVrayNodeTree(u.id, 'WORLD')]
        
            # Find the World objects whose node trees have topology updates
            topologyUpdates = [ u.id for u in self.dg.updates if isinstance(u.id, bpy.types.World) \
                                                                and u.id.node_tree in ntreesWithUpdatedTopology]

            return [t.name for t in topologyUpdates]


