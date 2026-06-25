# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.exporting.plugin_tracker import TrackObj, TrackNode, getObjTrackId, getConnectedTrackIds, log as trackerLog
from vray_blender.exporting.tools import *
from vray_blender.exporting.node_export import *
from vray_blender.lib import export_utils
from vray_blender.lib.plugin_utils import updateValue, isGenAIDisabled
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


def _exportToonOutlinesVolume(nodeCtx: NodeContext):
    """ Return a singleton VolumeVRayToon AttrPlugin if any material uses the Outlines socket,
        otherwise return None.  Mirrors C4D's addOutlinesToonVolume/updateSettingsEnvironment:
        a single VolumeVRayToon with toonMaterialOnly=2 activates outlines for every object
        whose material has a BRDFToonOverride connected to the Material Output Outlines socket.
    """
    hasToonOutlines = any(
        (outNode := NodesUtils.getOutputNode(mtl.node_tree, 'MATERIAL'))
        and (sock := getInputSocketByName(outNode, 'Outlines'))
        and sock.is_linked
        for mtl in bpy.data.materials
        if mtl.use_nodes and mtl.node_tree
    )

    if not hasToonOutlines:
        return None

    plDesc = PluginDesc('_outlinesVolumeVRayToon', 'VolumeVRayToon')
    plDesc.setAttribute('toonMaterialOnly', 2)
    return export_utils.exportPlugin(nodeCtx.exporterCtx, plDesc)


def _getExportedEffectsList(nodeCtx: NodeContext):
    """ Parses all effect nodes and sets the 'environment_volume' attribute of SettingsEnvironment plugin """

    worldOutputNode = nodeCtx.node
    effectsSock = getInputSocketByName(worldOutputNode, "Effects")
    environmentVolume = []

    if link := getFarNodeLink(effectsSock):
        effectsNode = link.from_node

        if effectsNode.bl_idname != "VRayNodeEffectsHolder":
            debug.printError("Environment: 'Effects' socket must be connected to a \"V-Ray Effects Container\" node!")
            return []

        with TrackNode(nodeCtx.nodeTracker, getNodeTrackId(effectsNode)):
            with nodeCtx.push(effectsNode), nodeCtx.pushGroupPath(link.groupPath):
                if nodeCtx.getCachedNodePlugin(effectsNode) is None:
                    nodeCtx.cacheNodePlugin(effectsNode)
                    for inSock in effectsNode.inputs:
                        if effectLink := getFarNodeLink(inSock):
                            effect = exportSocketLink(nodeCtx, effectLink)
                            if type(effect) is AttrPlugin:
                                environmentVolume.append(effect)

    return environmentVolume



def sockConnectedToDenoiser(sock: bpy.types.NodeSocket):
    link = getFarNodeLink(sock)
    return link is not None and link.from_node.bl_idname == "VRayNodeRenderChannelDenoiser"

def sockConnectedToCryptomatte(sock: bpy.types.NodeSocket):
    link = getFarNodeLink(sock)
    return link is not None and link.from_node.bl_idname == "VRayNodeRenderChannelCryptomatte"

def sockConnectedToObjectSelect(sock: bpy.types.NodeSocket):
    link = getFarNodeLink(sock)
    return link is not None and link.from_node.bl_idname == "VRayNodeRenderChannelObjectSelect"

def sockConnectedToEnhancer(sock: bpy.types.NodeSocket):
    link = getFarNodeLink(sock)
    return link is not None and link.from_node.bl_idname == "VRayNodeRenderChannelEnhancer"

def _exportViewportDenoiser(nodeCtx: NodeContext):
    # Export denoiser for the viewport. Except for the engine selection, 
    # always use the default settings in order to have the best performance.
    denoiserPluginName = Names.singletonPlugin("RenderChannelDenoiser")
    viewportDenoiserEngine = nodeCtx.scene.vray.Exporter.viewport_denoiser_engine
    pluginDesc = PluginDesc(denoiserPluginName, "RenderChannelDenoiser")
    pluginDesc.setAttribute("enabled", True)
    pluginDesc.setAttribute("engine", viewportDenoiserEngine)
    export_utils.exportPlugin(nodeCtx.exporterCtx, pluginDesc)


def _exportSettingsRenderChannels(nodeCtx: NodeContext):
    propGroup = nodeCtx.scene.vray.SettingsRenderChannels
    settingsRenderChannels = PluginDesc(Names.singletonPlugin('SettingsRenderChannels'), "SettingsRenderChannels")
    settingsRenderChannels.setAttribute("unfiltered_fragment_method", propGroup.unfiltered_fragment_method)
    settingsRenderChannels.setAttribute("deep_merge_mode", propGroup.deep_merge_mode)
    settingsRenderChannels.setAttribute("deep_merge_coeff", propGroup.deep_merge_coeff)

    export_utils.exportPlugin(nodeCtx.exporterCtx, settingsRenderChannels)

class WorldExporter(ExporterBase):
    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        self.exported = set()
        self.nodeTracker = ctx.nodeTrackers['WORLD']

    def _getNodeContext(self, world: bpy.types.World):
        nodeCtx = NodeContext(self, None, self.ctx.scene, self.renderer)
        nodeCtx.nodeTracker = self.nodeTrackers["WORLD"]
        nodeCtx.rootObj = world
        if world is not None:
            nodeCtx.ntree = world.node_tree

        return nodeCtx

    def _exportRenderChannels(self, nodeCtx: NodeContext):
        """ Export the SettingsRenderChannels and RenderChannelXXX plugins along
            with any associated node trees.
        """
        # NOTE: There is no need to track any  plugins here, because render channels
        # (except the denoiser channel which is handled specifically)
        # are only exported in production using full export.

        # Resolve the channels node via the world output's Channels socket so we
        # also capture the group path when the node lives inside a VRayGroup.
        # This runs outside the world-output push, so look the output node up
        # directly rather than reading nodeCtx.node.
        worldOutput = NodesUtils.getOutputNode(nodeCtx.ntree, 'WORLD')
        if not worldOutput:
            return
        channelsSock = getInputSocketByName(worldOutput, 'Channels')
        if not (channelsSock and (channelsLink := getFarNodeLink(channelsSock))):
            return
        channelsNode = channelsLink.from_node

        exportSettingsPlugin = False

        # Export channel plugins and their node trees
        with nodeCtx.push(channelsNode), nodeCtx.pushGroupPath(channelsLink.groupPath):
            if nodeCtx.getCachedNodePlugin(channelsNode) is None: # Node already exported
                nodeCtx.cacheNodePlugin(channelsNode)
                for channelLink in [getFarNodeLink(s) for s in channelsNode.inputs]:
                    if not channelLink:
                        continue

                    inSock = channelLink.to_socket
                    if nodeCtx.exporterCtx.vantage and (sockConnectedToCryptomatte(inSock) or sockConnectedToEnhancer(inSock)):
                        continue
                    if isGenAIDisabled() and sockConnectedToEnhancer(inSock):
                        continue

                    if not nodeCtx.exporterCtx.viewport:
                        exportSocketLink(nodeCtx, channelLink)
                        exportSettingsPlugin = True

        # Export the SettingsRenderChannels plugin if any of its sockets are connected
        if exportSettingsPlugin:
            _exportSettingsRenderChannels(nodeCtx)


    def _exportWorld(self, nodeCtx: NodeContext) -> bool:
        """ Returns True if the world is a valid V-Ray world that owns environment settings export. """
        world = nodeCtx.rootObj
        assert world is not None

        if not world.node_tree:
            return False

        nodeOutput = NodesUtils.getOutputNode(world.node_tree, 'WORLD')

        if not world.original.vray.is_vray_class:
            # Cannot export non-vray node trees
            if nodeOutput:
                debug.report(severity="WARNING",
                             msg=f"The World tree '{world.name}' has a V-Ray Output node but no V-Ray node tree."\
                                " Check if 'Use V-Ray World Nodes' has been pressed")
            return False

        if not nodeOutput:
            debug.printError(f"Output node not found in world tree '{world.name}'")
            return False

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
        if self.fullExport and not self.preview:
            # We are not currently exporting render elements other than the Color image and denoiser in the viewport.
            self._exportRenderChannels(nodeCtx)

        return True


    def _exportOutlinesWithoutWorld(self, nodeCtx: NodeContext):
        """ Export the outline toon volume and a minimal SettingsEnvironment when there is no V-Ray world.
            Needed because _exportEnvironmentSettings (which normally handles this) only runs with a V-Ray world.
        """

        if toonVolume := _exportToonOutlinesVolume(nodeCtx):
            envDesc = PluginDesc(Names.singletonPlugin("SettingsEnvironment"), "SettingsEnvironment")
            envDesc.setAttribute("environment_volume", [toonVolume])
            export_utils.exportPlugin(self, envDesc)


    def export(self):
        worldValid = False
        world = self.ctx.scene.world
        if world is not None:
            world = world.evaluated_get(self.dg)
        nodeCtx = self._getNodeContext(world)

        if world is not None:
            worldValid = self._exportWorld(nodeCtx)

        # Export viewport denoiser if enabled as _exportWorld won't export any
        # render channels for viewport renders.
        if (self.viewport and self.fullExport 
                and self.ctx.scene.vray.Exporter.viewport_denoiser_enabled):
            _exportViewportDenoiser(nodeCtx)
            _exportSettingsRenderChannels(nodeCtx)

        if not worldValid:
            self._exportOutlinesWithoutWorld(nodeCtx)

    def _exportEnvironmentSettings(self, nodeCtx: NodeContext):
        """ Gets settings from 'Environment' node and applies them to SettingsEnvironment plugin """
        pluginDesc = PluginDesc(Names.singletonPlugin("SettingsEnvironment"), "SettingsEnvironment")

        # Parse all effect nodes and sets the 'environment_volume' attribute of SettingsEnvironment plugin
        environmentVolume = _getExportedEffectsList(nodeCtx)

        # Add a singleton VolumeVRayToon when any material uses the Outlines socket
        if toonVolume := _exportToonOutlinesVolume(nodeCtx):
            environmentVolume.append(toonVolume)

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
            with nodeCtx.push(envNode), nodeCtx.pushGroupPath(envLink.groupPath):
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

        # Find all worlds that are to be shown in the scene
        activeWorlds = [w for w in bpy.data.worlds if w.vray and (not isObjectOrphaned(w))]

        # Remove from VRay the worlds with node trees whose topology has been updated.
        # They will be fully re-exported during the current update cycle
        topologyUpdates = self._getTopologyUpdates()
        updatedWorlds = [w for w in activeWorlds if self.fullExport or (getObjTrackId(w) in topologyUpdates)]

        self._pruneNodeTreePlugins(updatedWorlds)


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

        return [getObjTrackId(t) for t in topologyUpdates]


