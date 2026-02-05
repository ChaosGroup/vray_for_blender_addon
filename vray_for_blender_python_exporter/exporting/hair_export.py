import bpy
import math
import mathutils
import numpy as np

from vray_blender.exporting import tools
from vray_blender.lib.defs import AttrPlugin, ExporterBase, ExporterContext
from vray_blender.lib.names import Names
from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.lib.defs import DataArray

class HairData:
    TYPE_CURVES     = "CURVES"
    TYPE_PARTICLES  = "PARTICLES"

    def __init__(self, name, type):
        self.name           = name
        self.type           = type
        self.widthsInPixels = False
        self.useHairBSpline = False
        self.strandSegments  = np.empty(shape=0, dtype=np.int32)
        self.pointRadii      = np.empty(shape=0, dtype=np.float32)
        self.vertColors      = np.empty(shape=(0,3), dtype=np.float32)

        self.uvs = DataArray()
        self.points = DataArray()

        # Particle hair
        self.psys = 0
        self.firstToExport = 0
        self.totalParticles = 0
        self.shape = 0.0
        self.rootRadius = 0.0
        self.tipRadius = 0.0
        self.maxSteps = 0


class HairExporter(ExporterBase):
    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        self.objTracker = ctx.objTrackers['OBJ']


    def exportFromCurves(self, evaluatedObjCurves: bpy.types.Object, exportGeometry: bool):
        curves: bpy.types.Curves = evaluatedObjCurves.data
        uniqueName = Names.objectData(evaluatedObjCurves)

        if not exportGeometry:
            return AttrPlugin(uniqueName)

        if (totalPoints := len(curves.points)) == 0:
            # Curves without points (the curves object is empty), nothing to export
            return AttrPlugin()

        points = DataArray.fromAttribute(curves, "position")

        # The offsets are always 1 more than the curves in order to provide info about the
        # segments in the last curve
        numCurves = len(curves.curve_offset_data) - 1

        # Offset of the strand in the points vector
        # TODO: See if we can optimize this as well.
        strandOffsets = tools.foreachGetAttr(curves.curve_offset_data, "value", shape=(numCurves + 1,), dtype=np.int32)
        strandSegments = np.ediff1d(strandOffsets)

        # Radius of the strand at each point
        # TODO: See if we can optimize this as well.
        pointRadiuses = tools.foreachGetAttr(curves.points, "radius", shape=(totalPoints,), dtype=np.float32)

        # UVs of strand roots ( the anchor points to the parent surface )
        # These are so far the only UVs we can obtain from Blender
        uvs = DataArray.fromAttribute(curves, "surface_uv_coordinate")
        uvs.count *= 2

        data = HairData(uniqueName, HairData.TYPE_CURVES)
        data.fadeWidth      = True
        data.widthsInPixels = False
        data.useHairBSpline = True
        data.points         = points
        data.strandSegments = strandSegments
        data.pointRadii     = pointRadiuses
        data.uvs            = uvs

        vray.pluginCreate(self.renderer, uniqueName, 'GeomMayaHair')
        vray.exportHair(self.renderer, data)

        self.objTracker.trackPlugin(getObjTrackId(evaluatedObjCurves), data.name)

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
                # Note: In newer Blender versions this only works if the user creates a Face Corner+Byte color
                # attribute. mcol_on_emitter doesn't seem to work with any other attribute type.
                activeLayer = next(iter(l for l in objMesh.vertex_colors if l.active_render), None)
                if activeLayer is not None:
                    activeLayerIndex = objMesh.vertex_colors.find(activeLayer.name)

        exportUVs = uvIndex != -1 and uvLayers[uvIndex].data
        exportColors = activeLayerIndex != -1

        uvs = np.empty(2*(totalParticles - firstExported), dtype=np.float32) if exportUVs else np.empty(0)
        colors = np.empty(3*(totalParticles - firstExported), dtype=np.float32) if exportColors else np.empty(0)

        i = 0
        for pindex in range(firstExported, totalParticles):
            particle = psys.particles[(pindex - parents) % parents]

            if exportUVs:
                uv = psys.uv_on_emitter(pmod, particle=particle, particle_no=pindex, uv_no=uvIndex)
                uvs[i*2+0] = uv[0]
                uvs[i*2+1] = uv[1]
            if exportColors:
                color = psys.mcol_on_emitter(pmod, particle=particle, particle_no=pindex, vcol_no=activeLayerIndex)
                colors[i*3+0] = color[0]
                colors[i*3+1] = color[1]
                colors[i*3+2] = color[2]
            i+=1
        data.uvs = uvs
        data.vertColors = colors

        data.psys = psys.as_pointer()
        data.firstToExport = firstExported
        data.totalParticles = totalParticles
        data.shape = pset.shape
        data.rootRadius = (pset.root_radius * pset.radius_scale) / 2
        data.tipRadius = (pset.tip_radius * pset.radius_scale) / 2
        data.maxSteps = pointsPerStrand

        vray.pluginCreate(self.renderer, uniqueName, 'GeomMayaHair')
        vray.exportHair(self.renderer, data)
        self.objTracker.trackPlugin(getObjTrackId(evaluatedObj), data.name)

        self.persistedState.objDataTracker.trackParticlePluginOfData(Names.objectData(evaluatedObj), psys.name, uniqueName)
        return AttrPlugin(data.name)

    def getParticleHairName(self, obj, psys: bpy.types.ParticleSystem):
        return f"{Names.objectData(obj)}|{psys.name}"


    @staticmethod
    def findActiveUV(uvLayers):
        return next((uv for uv in uvLayers if uv.active_render), None)
