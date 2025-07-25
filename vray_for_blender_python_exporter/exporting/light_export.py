import bpy

from vray_blender.exporting.tools import *
from vray_blender.exporting.plugin_tracker import getObjTrackId, getNodeTrackId, TrackObj, log as trackerLog
from vray_blender.exporting.update_tracker import UpdateFlags, UpdateTracker, UpdateTarget 
from vray_blender.exporting.node_export import exportNodeTree
from vray_blender.lib.lib_utils import  LightVrayTypeToBlender, getLightPropGroup, getLightPluginType
from vray_blender.lib.defs import *
from vray_blender.lib.names import Names
from vray_blender.lib.settings_defs import LightSelectMode
from vray_blender.lib import export_utils, plugin_utils
from vray_blender.nodes.tools import isVrayNodeTree
from vray_blender.nodes.utils import getInputSocketByVRayAttr, getLightOutputNode, areNodesInterconnected, getOutputNode
from vray_blender.plugins.light.LightMesh import getLightMeshPluginName


from vray_blender.bin import VRayBlenderLib as vray
from vray_blender import debug

# Epsilon for light cone angle comparison
ANGLE_EPSILON = 0.000001

def fixBlenderLights():
    """ For stock Blender lights, translate the Blender properties used for displaying the light gizmos 
        into properties supported by V-Ray.
        
        NOTE: This function should be called from a context outside the depsgraph update procedure.
    """
    for l in [o for o in bpy.context.scene.objects if o.type == 'LIGHT']:
    
        match l.data.type:
            case 'AREA':
                _fixBlenderRectLight(l.data)
            
            case 'SPOT':
                _fixBlenderSpotLight(l.data)
                

def syncLightMeshInfo(exporterCtx: ExporterContext):
    """ Collect info about the visible and updated objects associated with LightMesh exports. """
    from vray_blender.plugins.light.LightMesh import collectLightMeshInfo
    
    # Collect the info for the current update 
    activePairs, updatedPairs = collectLightMeshInfo(exporterCtx)
    exporterCtx.activeMeshLightsInfo = activePairs
    exporterCtx.updatedMeshLightsInfo = updatedPairs


def syncLightMeshInfoPostExport(exporterCtx: ExporterContext):
    # Save a snapshot of the currently active mesh light gizmos to be used during the next update
    # cachedMeshLightsInfo is not owned by ExporterContext, so make sure to not redirect the reference.
    exporterCtx.cachedMeshLightsInfo.clear()
    exporterCtx.cachedMeshLightsInfo.update(exporterCtx.activeMeshLightsInfo)


def collectLightMixInfo(exporterCtx: ExporterContext):
    """ At the start of an export, collect information required to correctly export LightMix """
    
    if (world := exporterCtx.dg.scene.world) and isVrayNodeTree(world.node_tree, 'WORLD'):
        if not (outputNode := getOutputNode(world.node_tree, 'WORLD')):
            return
        
        for node in [n for n in world.node_tree.nodes if n.bl_idname == 'VRayNodeRenderChannelLightMix']:
            if areNodesInterconnected(node, outputNode):
                exporterCtx.activeLightMixNode = node
                break 

    if exporterCtx.activeLightMixNode:
        # Store list of lights by collection
        registeredLights = []
        for coll in bpy.data.collections:
            if lights := ([o for o in coll.objects if o.type == 'LIGHT']):
                exporterCtx.lightCollections[coll] = lights
                registeredLights.extend(lights)
        
        # Store a special list of all lights not part of any collection
        freeLights = [o for o in exporterCtx.sceneObjects if (o.type == 'LIGHT') and (o not in registeredLights)]
        exporterCtx.lightCollections[""] = freeLights


def linkLightToRenderChannel(exporterCtx: ExporterContext, objLight: bpy.types.Object, channelPropName: str, lightSelectPlugin: AttrPlugin):
    """ Set a reference to the lightSelectPlugin in the corresponding channels_xxx property of the 
        light plugins exported for objLight.

        Parameters:
        @param  objLight(Object) -              A scene light.
        @param  channelPropName(str) -          The name of the 'channel_xxx' property in the light plugin in which to set 
                                                the reference.
        @param lightSelectPlugin(AttrPlugin) -  The RenderChannelLightSelect plugin to reference.
    """
    lightPluginName = getPluginName(objLight)
    
    if objLight.data.vray.light_type == 'MESH':
        # A LightMesh 'lamp' object is exported as multiple instances of the LightMesh plugin, 
        # one for each object referenced by the lamp.
        for gizmoObjTrackId in [li.gizmoObjTrackId for li in exporterCtx.activeMeshLightsInfo if li.lightName == lightPluginName]:
            lightMeshPluginName = getLightMeshPluginName(lightPluginName, gizmoObjTrackId)
            exporterCtx.linkPluginToRenderChannel(lightMeshPluginName, channelPropName, lightSelectPlugin)
    else:
        exporterCtx.linkPluginToRenderChannel(lightPluginName, channelPropName, lightSelectPlugin)


def _fixBlenderRectLight(areaLight: bpy.types.Light):
    # Change Blender AREA lights shape to 'RECTANGLE' as this is the shape expected by the 
    # the backing LightRectangle plugin export code. 
    if areaLight.shape == 'SQUARE':
        areaLight.shape = 'RECTANGLE'

    if areaLight.vray.LightRectangle.is_disc:
        areaLight.size_y = areaLight.size


def _fixBlenderSpotLight(spotLight: bpy.types.Light):
    # Lights have two different propgroups for legacy and node lights, get the correct one
    propGroup = getLightPropGroup(spotLight, 'LightSpot')
    
    if propGroup.coneAngle != spotLight.spot_size:
        propGroup.coneAngle = spotLight.spot_size
        spotLight.spot_blend = max(-propGroup.penumbraAngle / propGroup.coneAngle, 0)
    else:
        # spot_blind is always positive while penumbraAngle may be both positive (outside) and negative (inside).
        # There is no way to make Blender's gizmo show outside penumbra. In the positive penumbra case, 
        # 'spot_blend' will stay at 0 but we don't want to transfer that value back to the penumbraAngle
        oldSpotBlend = max(-propGroup.penumbraAngle / propGroup.coneAngle, 0)
        
        if (abs(spotLight.spot_blend - oldSpotBlend) > ANGLE_EPSILON) and (spotLight.spot_blend != 0):
            propGroup.penumbraAngle = -(propGroup.coneAngle * spotLight.spot_blend)


def _setLightRectLightAttrs(areaLight: bpy.types.AreaLight, pluginDesc):

    # V-Ray expects half-size for both width and height
    sizeX = areaLight.size / 2.0
    sizeY = areaLight.size_y / 2.0

    isSquare = areaLight.shape in ('SQUARE', 'DISK') or areaLight.vray.LightRectangle.is_disc
    
    pluginDesc.setAttribute("u_size", sizeX)
    pluginDesc.setAttribute("v_size", sizeX if isSquare else sizeY )

    rectTex = pluginDesc.getAttribute("rect_tex")
    if type(rectTex) is AttrPlugin and not rectTex.isEmpty():
        pluginDesc.setAttribute("use_rect_tex", True)


def _customExportLightNode(nodeCtx: NodeContext, pluginDesc: PluginDesc):
    _customExportDomeLightSettings(nodeCtx, pluginDesc)
    return export_utils.exportPlugin(nodeCtx.exporterCtx, pluginDesc)
         

def _customExportDomeLightSettings(nodeCtx: NodeContext, pluginDesc: PluginDesc):
    """ Add Dome light settings which need special handling to the exported properties """
    if nodeCtx.rootObj.vray.light_type == 'DOME' and nodeCtx.node.bl_idname == 'VRayNodeUVWMapping':
        if nodeCtx.node.mapping_node_type != 'ENVIRONMENT' or not getInputSocketByVRayAttr(nodeCtx.nodes[0], 'dome_lock_texture').value:
            pluginDesc.setAttribute('uvw_matrix', Matrix())
            return

        # Check whether the UVW mapping node is connected to a Uvwgen or Mapping input of a node.
        # There is no need to check for multiple chained UVWMapping nodes as the node mapping inputs 
        # do not allow connections from native VRay plugins.
        uvwNode = nodeCtx.node
        texNode = nodeCtx.nodes[-2]
        if not any([i for i in texNode.inputs if i.is_linked and \
                                                ((i.links[0].to_socket.name == 'Uvwgen') or (i.links[0].to_socket.name == 'Mapping')) and \
                                                (i.links[0].from_socket == uvwNode.outputs['Mapping']) ]):
            # The socket is not connected to the corrrect socket type on the texture node
            return

        # If a texture socket on the LightDome node is connected to the same nodetree branch 
        # as the UVWMapping node, set the inverted rotation of the world matrix of the light object 
        # as transfomation in the UWVMapping node in order to lock the texture placement to the 
        # DomeLight orientation.
        domeNode = nodeCtx.nodes[0]
        texNode = nodeCtx.nodes[1]
        for sockName in ['color_colortex', 'intensity_tex', 'shadowColor_colortex']:
            sock = getInputSocketByAttr(domeNode, sockName)
            if any ([l for l in sock.links if l.from_node == texNode]):
                _, rotQuat, _ = nodeCtx.sceneObj.matrix_world.decompose()
                tm = rotQuat.inverted().to_matrix().to_4x4()
                pluginDesc.setAttribute('uvw_matrix', tm)
                return
            

def getPluginName(obj: bpy.types.Object):
    assert type(obj) is bpy.types.Object
    return Names.object(obj)


def _getLightsOfTextures(textureNames: list[str]):
        """ Return the objects of type 'LIGHT' in whose node trees the texture names are used.
         
          NOTE: This is hackish, but for the moment we don't know how to make Blender generate 
          depsgraph updates for the node tree.
        """
        result = set()

        for light in [l for l in bpy.context.scene.objects if l.type == 'LIGHT' and hasattr(l.data, 'vray') and l.data.node_tree]:
            for n in light.data.node_tree.nodes:
                if hasattr(n, 'texture') and (n.texture.name in textureNames):
                    result.add(light)
                
        return result


class LightExporter(ExporterBase):
    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        self.exported = set()
        
        # Track plugins associated with parent objects of lights
        self.objTracker = ctx.objTrackers['LIGHT']

        # Track plugins associated with a node tree
        self.nodeTracker = ctx.nodeTrackers['LIGHT']

        self.updatedMeshLights = {}
        

    def export(self):
        if self.preview:
            self._exportPreview()
        else:
            self._exportScene()


    def _exportPreview(self):
        for obj in [o for o in self.dg.objects if o.type == 'LIGHT']:
            with self.objectContext.push(obj):
                self._exportLight(obj)


    def _exportScene(self):
        updates = [ u.id.original for u in self.dg.updates ]

        sceneLightObjs = [o for o in self.sceneObjects if o.type == 'LIGHT']
        
        updatedTextures       = [t.name for t in updates if isinstance(t, bpy.types.Texture)]
        updatedTextureLights  = _getLightsOfTextures(updatedTextures)
        updatedMeshLights     = {p.lightObj for p in self.updatedMeshLightsInfo}

        # Updates registered in UpdateTracker
        trackerUpdates = UpdateTracker.getUpdatesOfType(UpdateTarget.LIGHT, UpdateFlags.ALL)
        updatedTrackerLightSIDs = {m[0] for m in trackerUpdates}
        
        updatedLights = {o for o in sceneLightObjs if self.fullExport or    (o in updates) or \
                                                                            (o.data in updates) or \
                                                                            (getObjTrackId(o.data) in updatedTrackerLightSIDs)}
        
        updatedLights = updatedLights.union(updatedTextureLights).union(updatedMeshLights)

        def lightsForExport():
            if self.commonSettings.useMotionBlur and self.isAnimation:
                return self.motionBlurBuilder.getObjectsForExport(updatedLights)
            return updatedLights

        # All plugins for lights which should not be exported have been removed from VRay and the tracker
        # by the prune procedure. From the rest, export only the updated visible nodes. 
        for obj in  [o for o in lightsForExport() if getObjTrackId(o) in self.visibleObjects]:
            with self.objectContext.push(obj):
                self._exportLight(obj)

        self._fixLightVisibility(sceneLightObjs)
        self._exportLightMix()


    def _exportLightSelect(self, channelName: str, selectMode: int):
        """ Export a single RenderChannelLightSelect plugin.

            Parameters:
            @channelName - The name of the channel to show in VFB's UI
            @selectMode -  Select mode (one of settings_defs.LightSelectMode items)
        """    
        pluginType = "RenderChannelLightSelect"
        pluginName = Names.pluginObject(pluginType, channelName)
                
        pluginDesc = PluginDesc(pluginName, pluginType)

        pluginDesc.setAttribute("name", channelName)
        pluginDesc.setAttribute("color_mapping", True)
        pluginDesc.setAttribute("light_select_mode", selectMode)

        return export_utils.exportPlugin(self, pluginDesc)

    
    # Creates light plugin, exports it and puts it in ObjTracker
    def _exportLight(self, obj: bpy.types.Object):
        light = obj.data
        vrayLight = light.vray

        if not vrayLight.is_vray_class:
            return
        
        pluginType = getLightPluginType(light)
        if pluginType == "":
            debug.printError(f"Can't find vray Light type for blender object with id:{obj.name}")
            return AttrPlugin()
        
        # Match the light representation of V-Ray light to a blender one
        if vrayLight.light_type != "BLENDER":
            # Changing the type here will trigger a scene update. To avoid going into an 
            # infinite update loop, set the type only once
            blenderType = LightVrayTypeToBlender[vrayLight.light_type]
            if light.type != blenderType:
                light.type = blenderType

        
        lightPluginName = getPluginName(obj)

        # The lights may be defined either through the property pages, or as node trees.
        # When a V-Ray node tree is active, all values set through the property pages are disregarded.
        # Any node trees for lights in the preview scenes are not VRay trees, so do not try 
        # to export them. Export the light property group instead. 
        if (not self.preview) and (lightNtree := light.node_tree) and (lightNode := getLightOutputNode(lightNtree)):
            nodeCtx = NodeContext(self, light, self.ctx.scene, self.renderer)
            nodeCtx.nodeTracker   = self.nodeTracker
            nodeCtx.ntree         = lightNtree
            nodeCtx.sceneObj      = obj
            nodeCtx.customHandler = _customExportLightNode

            with nodeCtx, nodeCtx.push(lightNode):
                pluginDesc = PluginDesc(lightPluginName, pluginType)
                pluginDesc.node = lightNode
                lightTrackId = getObjTrackId(light)

                if (lightPlugin := nodeCtx.getCachedNodePlugin(lightNode)) is None:
                    with TrackObj(self.nodeTracker, lightTrackId):
                        exportNodeTree(nodeCtx, pluginDesc)
                        # So far, we have exported the whole nodetree except for the proper LightXXX node. Export it now
                        lightPlugin = self._exportLightPlugin(obj, pluginDesc, lightNode)
                        nodeCtx.cacheNodePlugin(lightNode, lightPlugin)

        else:
            pluginDesc = PluginDesc(lightPluginName, pluginType)
            pluginDesc.vrayPropGroup = getattr(vrayLight, pluginType)
            self._exportLightPlugin(obj, pluginDesc, lightNode = None)


    def _exportLightPlugin(self, obj: bpy.types.Object, pluginDesc: PluginDesc, lightNode: bpy.types.Node):
        """ Export an 'output' light plugin. """  
        light = obj.data

        # Set attributes that do not depend on the usage of nodetree for the light
        match pluginDesc.type:
            case "LightRectangle":
                _setLightRectLightAttrs(light, pluginDesc)
                pluginDesc.setAttribute("objectID", obj.pass_index)
            case "LightSphere" | "LightDome":
                pluginDesc.setAttribute("objectID", obj.pass_index)
            case "SunLight":
                # When created, SunLight objects use empty objects as targets,
                # tracking them with "Track to" constraints.
                # If such a "target object" exists, its transform should be used as "target_transform".
                for constraint in obj.constraints:
                    # Only process constraints of correct type and such with valid targets
                    if (constraint.type == 'TRACK_TO') and (constraint.target is not None):
                        pluginDesc.setAttribute("target_transform", constraint.target.matrix_world)
                        break

        # Applying the 'transform' attribute. It can be overwritten by plugin description or node.
        if (transMat := pluginDesc.getAttribute("transform")) \
            and (type(transMat) is Matrix) and (not transMat.is_identity):
            
            # Upscaling the matrix to 4x4. This happens when VRayNodeMatrix is attached.
            if len(transMat.col) == len(transMat.row) == 3 :
                transMat = transMat.to_4x4()
                transMat.col[3][3] = 1

            assert len(transMat.col) == len(transMat.row) == 4, "Transform matrix with size other that 4x4, 3x3"

            pluginDesc.setAttribute("transform", obj.matrix_world @ transMat)
        else:
            pluginDesc.setAttribute("transform", obj.matrix_world)

        pluginDesc.setAttribute("scene_name", [light.name, getSceneNameOfObject(obj, self.dg.scene)])

        # Export a LightSelect render channel for this light, if necessary.
        self._exportIndividualLightInLightMix(obj, pluginDesc)

        # Depending on whether the light has a nodetree, the plugin properties are stored in different locations.  
        propHolder = lightNode if lightNode else light.vray
        pluginDesc.vrayPropGroup = getattr(propHolder, pluginDesc.type)

        if self.commonSettings.useMotionBlur:
            overrideMb = obj.vray.VRayObjectProperties.override_motion_blur_samples
            samples = obj.vray.VRayObjectProperties.motion_blur_samples
            pluginDesc.setAttribute("nsamples", samples if overrideMb else self.commonSettings.mbSamples)

        plugin = export_utils.exportPlugin(self, pluginDesc)
        
        if type(plugin) is AttrPlugin:
            self._trackPlugin(obj, lightNode, plugin.name)
        elif type(plugin) is list:
            # LightMesh will export several instances of the light plugin if more than 1 object is connected to the
            # same LightMesh. This is why its export procedure will return a list of exported plugins 
            for pl in plugin:
                self._trackPlugin(obj, lightNode, pl.name)
        
        return plugin


    def _fixLightVisibility(self, sceneLights: list[bpy.types.Object]):
        """ Sets the visibility of all light in the scene regardless of the update status. We don't receive light-specific 
            update notifications when the visibility changes, so we must do this for all the lights. 
        """
        if not self.interactive:
            return
        
        for obj in sceneLights:
            pluginName = getPluginName(obj)  
            pluginType = getLightPluginType(obj.data)

            # Get the current 'enabled' flag from the correct property source depending
            # on whether the light has a V-Ray nodetree attached.
            if (lightNtree := obj.data.node_tree) and (lightNode := getLightOutputNode(lightNtree)):
                propGroup = getattr(lightNode, pluginType)
                isEnabled = (lightNode is not None) and propGroup.enabled
            else:
                propGroup = getattr(obj.data.vray, pluginType)
                isEnabled = propGroup.enabled

            isVisibleLight = getObjTrackId(obj) in self.visibleObjects
            
            plugin_utils.updateValue(self.renderer, pluginName, "enabled",  isEnabled and isVisibleLight)


    def _getLightMix(self):
        if self.viewport or not (lightMixNode := self.activeLightMixNode):
            return None
        
        return lightMixNode.RenderChannelLightMix
        
        
    def _trackPlugin(self, obj: bpy.types.Object, lightNode: bpy.types.Node, pluginName):
        assert isinstance(obj, bpy.types.Object)
        
        objTrackId = getObjTrackId(obj)
        lightTrackId = getObjTrackId(obj.data)
        
        self.objTracker.trackPlugin(objTrackId, pluginName)

        # If the light has node tree, track the current node as well
        if lightNode:
            self.nodeTracker.trackNodePlugin(lightTrackId, getNodeTrackId(lightNode), pluginName)
        
        
    def prunePlugins(self):
        """ Delete all plugins associated with removed, orphaned or updated lights """
        
        # Find all lights that are to be shown in the scene
        activeLights = [l for l in bpy.data.lights if l.vray and (not isObjectOrphaned(l))]
        activeObjects = [o for o in self.sceneObjects if o.type == 'LIGHT' and (not isObjectOrphaned(o))]
        
        # Remove from VRay the lights with node trees whose topology has been updated. 
        # They will be fully re-exported during the current update cycle 
        topologyUpdates = self._getTopologyUpdates() 
        updatedLightIds = [getObjTrackId(l) for l in activeLights if self.fullExport or (getObjTrackId(l) in topologyUpdates)]

        self._pruneObjectPlugins(updatedLightIds)        
        self._pruneNodeTreePlugins(updatedLightIds)

        activeLightIds = [getObjTrackId(l) for l in activeLights]
        activeObjectIds = [getObjTrackId(o) for o in activeObjects]

        removedObjectIds = self.objTracker.diff(activeObjectIds)
        self._pruneObjectPlugins(removedObjectIds)

        # Remove from VRay the light trees for objects that have been removed from the scene or orphaned
        removedLightNodeIds = self.nodeTracker.diffObjs(activeLightIds)
        self._pruneNodeTreePlugins(removedLightNodeIds)

        # Remove all LightMesh plugins with disconnected gizmos
        self._pruneDisconnected()


    def _getTopologyUpdates(self):
        """ Return the node tree track IDs of the Light data objects whose tree topology has changed. """
        topologyUpdates =  UpdateTracker.getUpdatesOfType(UpdateTarget.LIGHT, UpdateFlags.TOPOLOGY)
        return [t[0] for t in topologyUpdates]


    def _pruneObjectPlugins(self, removeIds: list[str]):
        """ Remove plugins for lights without node trees """
        if not self.interactive:
            return
        
        for objId in removeIds:
            for pluginName in self.objTracker.getOwnedPlugins(objId):
                vray.pluginRemove(self.renderer, pluginName)
                trackerLog(f"REMOVE OBJ PLUGIN: {pluginName}")
            self.objTracker.forget(objId)


    def _pruneNodeTreePlugins(self, removeIds: list[str]):
        """ Remove plugins for lights with node trees """
        if not self.interactive:
            return
        
        def forgetNodes(lightId, nodeIds):
             if not nodeIds:
                 return
             
             for nodeId in nodeIds:
                for pluginName in self.nodeTracker.getNodePlugins(lightId, nodeId):
                    vray.pluginRemove(self.renderer, pluginName)
                    trackerLog(f"REMOVE NODE PLUGIN: {pluginName}")
                self.nodeTracker.forgetNode(lightId, nodeId)

        for trackId in removeIds:
            forgetNodes(trackId, self.nodeTracker.getOwnedNodes(trackId))


    def _pruneDisconnected(self):
        """ Delete plugins for LightMeshes for which the gizmo has been disconnected. """
        disconnectedMeshLights = [p for p in self.cachedMeshLightsInfo.difference(self.activeMeshLightsInfo)]

        for l in disconnectedMeshLights:
            lightPluginName = getLightMeshPluginName(l.lightName, l.gizmoObjTrackId)
            vray.pluginRemove(self.renderer, lightPluginName)

    
    def _exportLightMix(self):
        if not (lightMix := self._getLightMix()):
            return

        # Environment and Self-Illumination LightSelect plugins are needed for the Light Mix
        self._exportLightSelect("Environment", LightSelectMode.Environment)
        self._exportLightSelect("Self_Illumination", LightSelectMode.SelfIllumination)

        if lightMix.separate_emissive_material:
            # Create LightSelect channels for each emissive material
            for pluginName, attrName, nodeName in self.emissiveMaterials:
                lightSelectEmissive = self._exportLightSelect(nodeName, LightSelectMode.Full)
                self.linkPluginToRenderChannel(pluginName, attrName, lightSelectEmissive)

        if lightMix.mode == 'grouped':
            self._exportGroupedLightMix()
        

    def _exportGroupedLightMix(self):
        """ For 'grouped' light select mode, export one light select channel for each collecrion
            containing lights.
        """
        if not (self.production or self.iprVFB):
            return
        
        for coll in bpy.data.collections:
            if not (lightObjects := ([o for o in coll.objects if o.type == 'LIGHT'])):
                continue

            lsPlugin = self._exportLightSelect(coll.name, LightSelectMode.Full)
            # No need to track light selects as they are only exported for production renders

            for objLight in lightObjects:
                linkLightToRenderChannel(self, objLight, 'channels_full', lsPlugin)


    def _exportIndividualLightInLightMix(self, objLight: bpy.types.Object, lightPlugin: PluginDesc):
        """ Export the light as a separate LightSelect render channel.

            This is done when LightMix is in a mode other than 'manual'. An nndividual LightSelect 
            render channel is exported for each light and assinged to the 'channels_full' property of the light.
        """
        if not (self.production or self.iprVFB):
            return
        
        if not (lightMix := self._getLightMix()):
            return
        
        isFreeLight = (lightMix.mode == 'grouped') and (objLight in self.lightCollections[""])

        if (lightMix.mode != 'individual') and (not isFreeLight):
            return
        
        if objLight.data.vray.light_type == 'MESH':
            for gizmoObjTrackId in [li.gizmoObjTrackId for li in self.activeMeshLightsInfo if li.lightName == lightPlugin.name]:
                lightMeshPluginName = getLightMeshPluginName(lightPlugin.name, gizmoObjTrackId)
                lsPlugin = self._exportLightSelect(objLight.name, LightSelectMode.Full)    
                self.linkPluginToRenderChannel(lightMeshPluginName, 'channels_full', lsPlugin)
        else:
            lsPlugin = self._exportLightSelect(objLight.name, LightSelectMode.Full)
            self.linkPluginToRenderChannel(lightPlugin.name, 'channels_full', lsPlugin)


        