import bpy
import math
import mathutils
import numpy as np

from vray_blender.exporting import tools
from vray_blender import debug
from vray_blender.lib.defs import AttrPlugin, ExporterBase, ExporterContext, PluginDesc
from vray_blender.lib.export_utils import exportPlugin, exportObjProperties
from vray_blender.lib.names import Names
from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.lib.defs import DataArray
from vray_blender.nodes import utils as NodesUtils

from vray_blender.exporting.node_export import NodeContext, exportNodeTree
from vray_blender.exporting.plugin_tracker import TrackObj


def syncFurInfo(exporterCtx: ExporterContext):
    """ Collect info about the visible and updated objects associated with Fur exports. """
    from vray_blender.plugins.geometry.GeomHair import collectGeomHairInfo
    
    # Collect the info for the current update 
    activePairs, updatedPairs = collectGeomHairInfo(exporterCtx)
    exporterCtx.activeFurInfo = activePairs
    exporterCtx.updatedFurInfo = updatedPairs


class HairData:
    TYPE_CURVES     = "CURVES"
    TYPE_PARTICLES  = "PARTICLES"

    def __init__(self, name, type):
        self.name           = name
        self.type           = type
        self.segments       = 0
        self.matWorld       = mathutils.Matrix()    # Identity
        self.width          = 0.0
        self.fadeWidth      = False
        self.widthsInPixels = False
        self.useHairBSpline = False
        self.strandSegments  = np.empty(shape=0, dtype=np.int32)
        self.pointRadii      = np.empty(shape=0, dtype=np.float32)
        self.vertColors      = np.empty(shape=(0,3), dtype=np.float32)

        # The members below are represented as DataArray-s when the HairData is generated from curves.
        self.uvs: DataArray | np.ndarray = None
        self.points: DataArray | np.ndarray = None


class HairExporter(ExporterBase):
    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        self.objTracker = ctx.objTrackers['OBJ']
        self.nodeTracker = ctx.nodeTrackers['OBJ']
        self.furObjectsForExport = []


    def exportFromCurves(self, evaluatedObjCurves: bpy.types.Object, exportGeometry: bool):
        curves: bpy.types.Curves = evaluatedObjCurves.data
        uniqueName = Names.objectData(evaluatedObjCurves)
        objName = Names.object(evaluatedObjCurves)

        if not exportGeometry:
            return AttrPlugin(self.persistedState.objToGeomPluginName.get(objName, ""))

        if (totalPoints := len(curves.points)) == 0:
            # Curves without points (the curves object is empty), nothing to export
            return AttrPlugin()

        points = DataArray.fromAttribute(curves, "position")

        # The offsets are always 1 more than the curves in order to provide info about the
        # segments in the last curve
        numCurves = len(curves.curve_offset_data) - 1

        # Offset of the strand in the points vector
        strandOffsets = tools.foreachGetAttr(curves.curve_offset_data, "value", shape=(numCurves + 1,), dtype=np.int32)
        strandSegments = np.ediff1d(strandOffsets)

        # Radius of the strand at each point
        pointRadiuses = tools.foreachGetAttr(curves.points, "radius", shape=(totalPoints,), dtype=np.float32)

        # UVs of strand roots ( the anchor points to the parent surface )
        # These are so far the only UVs we can obtain from Blender
        uvs = DataArray.fromAttribute(curves, "surface_uv_coordinate")

        sameRadius = np.all(pointRadiuses == pointRadiuses[0])
        sameLength = np.all(strandSegments == strandSegments[0])

        data = HairData(uniqueName, HairData.TYPE_CURVES)
        data.segments       = strandSegments[0] - 1 if sameLength else 0
        data.matWorld       = tools.mat4x4ToTuple(mathutils.Matrix())
        data.width          = pointRadiuses[0] if sameRadius else 0.0
        data.fadeWidth      = True
        data.widthsInPixels = False
        data.useHairBSpline = True
        data.points         = points
        data.strandSegments = np.empty(shape=0, dtype=np.int32) if sameLength else strandSegments
        data.pointRadii     = np.empty(shape=0, dtype=np.float32) if sameRadius else pointRadiuses
        data.uvs            = uvs

        vray.pluginCreate(self.renderer, uniqueName, 'GeomMayaHair')
        vray.exportHair(self.renderer, data)

        self.objTracker.trackPlugin(getObjTrackId(evaluatedObjCurves), data.name)

        self.persistedState.objToGeomPluginName[objName] = data.name

        return AttrPlugin(data.name)

    def exportFromParticles(self, evaluatedObj: bpy.types.Object, pmod: bpy.types.ParticleSystemModifier):
        assert evaluatedObj.is_evaluated, f"Particle hair exporter: Object should have been evaluated: {evaluatedObj.name}"

        viewportRender = self.interactive
        psys = pmod.particle_system
        pset = psys.settings
        uniqueName = self.getParticleHairName(evaluatedObj.original, psys)

        parents = len(psys.particles)
        children = len(psys.child_particles)

        totalParticles = parents + children

        firstExported = 0

        # Parents are not exported if there are children
        if children != 0:
            # Number of virtual parents reduces the number of exported children
            virtualParents = math.trunc(0.3 * psys.settings.virtual_parents
                                             * psys.settings.child_percent * parents)
            firstExported = parents + virtualParents

        segments = pset.display_step if viewportRender else pset.render_step
        segments = (1 << segments)
        pointsPerStrand = segments + 1

        vrayFur = pset.vray.VRayFur

        data = HairData(uniqueName, HairData.TYPE_PARTICLES)
        data.segments       = 0
        data.matWorld       = tools.mat4x4ToTuple(evaluatedObj.matrix_world)
        data.width          = vrayFur.width
        data.fadeWidth      = vrayFur.make_thinner
        data.widthsInPixels = vrayFur.widths_in_pixels
        data.useHairBSpline = pset.use_hair_bspline

        objMesh = evaluatedObj.to_mesh(preserve_all_data_layers=True, depsgraph=self.dg)

        uvIndex = -1
        activeLayerIndex = -1
        if objMesh:
            uvLayers = objMesh.uv_layers
            if activeUV := HairExporter.findActiveUV(uvLayers):
                uvIndex = uvLayers.find(activeUV.name)
            else:
                uvIndex = -1

            if len(objMesh.color_attributes) > 0:
                # TODO: Handle multiple color layers, if present
                activeLayer = next(iter(l for l in objMesh.vertex_colors if l.active_render), None)
                if activeLayer is not None:
                    activeLayerIndex = objMesh.vertex_colors.find(activeLayer.name)

        exportUVs = uvIndex != -1 and uvLayers[uvIndex].data
        exportColors = activeLayerIndex != -1

        rootRadius = (pset.root_radius * pset.radius_scale) / 2
        tipRadius = (pset.tip_radius * pset.radius_scale) / 2

        pointsList = []
        numHairVerts = []
        uvs = []
        colors = []
        radii = []
        for pindex in range(firstExported, totalParticles):
            particle = psys.particles[(pindex - parents) % parents]
            pts = []
            for step in range(pointsPerStrand):
                pt = psys.co_hair(object=evaluatedObj, particle_no=pindex, step=step)
                if pt.length_squared == 0:
                    break
                pts.append(list(pt))

            if len(pts) < 2:
                continue

            numHairVerts.append(len(pts))
            pointsList.extend(pts)

            shape = pmod.particle_system.settings.shape  # Shape parameter controls the radius curve
            if pmod.particle_system.settings.shape == 0: # No changes in the radius
                radii.append(np.linspace(rootRadius, tipRadius, pointsPerStrand, dtype=np.float32))
            else:
                # Implementation of the function of Cycles, responsible for interpolating the hair strand radius
                # from root to tip, based on the shape parameter.
                # Link to the implementation in Blender's source code:
                # https://projects.blender.org/blender/blender/src/commit/5ba668135aaf7cb79081482cb7839a64bbb47457/intern/cycles/blender/curves.cpp#L32
                shapeExp = 0
                if shape < 0:
                    shapeExp = (1 + shape)
                elif shape > 0 and shape != 1:
                    shapeExp = (1 / (1 - shape))
                elif shape == 1:
                    # Avoiding division by zero (1/(1-1)).
                    # 10000 is equal to 1/(1-0.9999)
                    shapeExp = 10000

                t = np.linspace(1, 0, pointsPerStrand, dtype=np.float32)
                radii.append((t ** shapeExp) * (rootRadius - tipRadius) + tipRadius)

            if exportUVs:
                uv = psys.uv_on_emitter(pmod, particle=particle, particle_no=pindex, uv_no=uvIndex)
                uvs.append(list(uv))
            if exportColors:
                color = psys.mcol_on_emitter(pmod, particle=particle, particle_no=pindex, vcol_no=activeLayerIndex)
                colors.append(list(color))
        
        if len(pointsList) <= 2:
            return None
        
        data.points = np.array(pointsList, dtype=np.float32)
        data.uvs = np.array(uvs, dtype=np.float32)
        data.vertColors = np.array(colors, dtype=np.float32)
        data.pointRadii = np.array(radii, dtype=np.float32)
        data.strandSegments = np.array(numHairVerts, dtype=np.int32)

        vray.pluginCreate(self.renderer, uniqueName, 'GeomMayaHair')
        vray.exportHair(self.renderer, data)
        self.objTracker.trackPlugin(getObjTrackId(evaluatedObj), data.name)

        self.persistedState.objToGeomPluginName[Names.object(evaluatedObj)] = uniqueName

        return AttrPlugin(data.name)

    def getParticleHairName(self, obj, psys: bpy.types.ParticleSystem):
        return f"{Names.objectData(obj)}|{psys.name}"


    @staticmethod
    def findActiveUV(uvLayers):
        return next((uv for uv in uvLayers if uv.active_render), None)


    def purgeFurInfo(self):
        instTracker = self.objTrackers['INSTANCER']
        disconnectedFurs = [p for p in self.persistedState.activeFurInfo.difference(self.activeFurInfo)]

        for f in disconnectedFurs:
            for pluginName in self.objTracker.getOwnedPlugins(f.parentObjTrackId):
                if f.gizmoObjName in pluginName:
                    vray.pluginRemove(self.renderer, pluginName)
                    self.objTracker.forgetPlugin(f.parentObjTrackId, pluginName)

            # Remove instancer plugins if any
            for pluginName in instTracker.getOwnedPlugins(f.parentObjTrackId):
                if f.gizmoObjName in pluginName:
                    vray.pluginRemove(self.renderer, pluginName)
                    instTracker.forgetPlugin(f.parentObjTrackId, pluginName)



    def exportFurObjects(self):
        for obj, exportGeometry in self.furObjectsForExport:
            self._exportFurObject(obj, exportGeometry)

    def addFurObjectForExport(self, obj: bpy.types.Object, exportGeometry: bool):
        self.furObjectsForExport.append((obj, exportGeometry))

    def _exportFurObject(self, obj: bpy.types.Object, exportGeometry: bool):
        assert obj.vray.isVRayFur, f"Fur object expected: {obj.name}"
        
        exportedFurs = []
        pluginDesc = PluginDesc(f"VrayFur@{Names.object(obj)}", "GeomHair")
        
        # Hacky way to pass to GeomHair.customExport() that the geometry plugin should or should not be exported.
        pluginDesc.setAttribute('export_geometry', exportGeometry)
        
        with self.objectContext.push(obj):
            if NodesUtils.treeHasNodes(obj.vray.ntree) and \
                (furOutputNode := NodesUtils.getOutputNode(obj.vray.ntree)):
                
                pluginDesc.vrayPropGroup = getattr(furOutputNode, furOutputNode.vray_plugin)
                pluginDesc.node = furOutputNode

                nodeCtx = NodeContext(self, obj, self.ctx.scene, self.renderer)
                nodeCtx.rootObj     = obj
                nodeCtx.nodeTracker = self.nodeTracker
                nodeCtx.ntree       = obj.vray.ntree

                with nodeCtx, nodeCtx.push(furOutputNode):
                    objTrackId = getObjTrackId(obj)

                    if nodeCtx.getCachedNodePlugin(furOutputNode) is None:
                        nodeCtx.cacheNodePlugin(furOutputNode)
                        with TrackObj(self.nodeTracker, objTrackId):
                            exportNodeTree(nodeCtx, pluginDesc)
                            exportedFurs = exportPlugin(self, pluginDesc)
                            nodePluginNames = [np.name for np in exportedFurs]

                            if objOutputNode := NodesUtils.getOutputNode(obj.vray.ntree, 'OBJECT'):
                                exportObjProperties(obj, nodeCtx, self.objTracker, objOutputNode, nodePluginNames)
            else:
                pluginDesc.vrayPropGroup = obj.data.vray.GeomHair
                exportedFurs = exportPlugin(self, pluginDesc)

        return exportedFurs
        
        
