import bpy
import math
import mathutils
import numpy as np

from vray_blender.exporting import tools
from vray_blender import debug
from vray_blender.lib.defs import AttrPlugin, ExporterBase, ExporterContext
from vray_blender.lib.names import Names
from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.bin import VRayBlenderLib as vray


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
        self.points          = np.empty(shape=(0,3), dtype=np.float32)
        self.strandSegments  = np.empty(shape=0, dtype=np.int32)
        self.pointRadii      = np.empty(shape=0, dtype=np.float32)
        self.uvs             = np.empty(shape=(0,2), dtype=np.float32)
        self.vertColors      = np.empty(shape=(0,3), dtype=np.float32)


def gatherPoints(obj, start, end, pointsPerStrand, fnCoHair, data):
    data.points = np.fromiter(( elem
                        for pindex in range(start, end)
                            for step in range(pointsPerStrand)
                            for elem in fnCoHair(object=obj, particle_no=pindex, step=step)),
                        dtype=np.float32).reshape((end-start) * pointsPerStrand, 3)
    
def gatherRadii(pointsPerStrand: int, totalParticles: int, pset: bpy.types.ParticleSettings, data: HairData):
    """ Returns array with radiuses of hair points """

    # Get the root and tip radius scaling
    rootRadius = (pset.root_radius * pset.radius_scale) / 2
    tipRadius = (pset.tip_radius * pset.radius_scale) / 2

    shape = pset.shape  # Shape parameter controls the radius curve
    if shape == 0: # No changes in the radius
        data.pointRadii = np.tile(np.linspace(rootRadius, tipRadius, pointsPerStrand, dtype=np.float32), totalParticles)
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
        data.pointRadii = np.tile((t ** shapeExp) * (rootRadius - tipRadius) + tipRadius,  totalParticles)


def gatherUVs(mod, start, end, psys, uvIndex, fnUvOnEmitter, parents, data):
    data.uvs = np.fromiter((elem
                            for i in range(start, end)
                            for elem in fnUvOnEmitter(mod, particle=psys.particles[(i - parents) % parents], particle_no=i, uv_no=uvIndex)),
                            dtype=np.float32).reshape(end - start, 2)


def gatherVertColors(mod, start, end, psys, vcolIndex, fnColOnEmitter, parents, data):
    data.vertColors = np.fromiter((elem
                            for i in range(start, end)
                            for elem in fnColOnEmitter(mod, psys.particles[(i - parents) % parents], 
                                                       particle_no=i, vcol_no=vcolIndex)),
                            dtype=np.float32).reshape(end-start, 3)


class HairExporter(ExporterBase):
    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        self.exported = set()
        self.objTracker = ctx.objTrackers['OBJ']


    def exportFromCurves(self, evaluatedObjCurves: bpy.types.Curves, exportGeometry: bool):
        curves = evaluatedObjCurves.data
        uniqueName = Names.objectData(evaluatedObjCurves)

        if (not exportGeometry) or (uniqueName in self.exported):
            # Already exported, return the existing plugin
            return AttrPlugin(uniqueName)
        
        totalPoints = len(curves.points)
        strands = len(curves.curves)
       
        points = tools.foreachGetAttr(curves.points, "position", shape=(totalPoints,3), dtype=np.float32) 
        # Alternative to the above
        # curves.postition_data.foreach_get("vector", points)

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
        uvs = tools.foreachGetAttr(curves.attributes["surface_uv_coordinate"].data, "vector", shape=(strands,2), dtype=np.float32)

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
        
        self.exported.add(data.name)
        return AttrPlugin(data.name)


    def exportFromParticles(self, evaluatedObj: bpy.types.Object, pmod: bpy.types.ParticleSystemModifier):
        assert evaluatedObj.is_evaluated, f"Particle hair exporter: Object should have been evaluated: {evaluatedObj.name}"
        
        viewportRender = self.interactive
        psys = pmod.particle_system
        pset = psys.settings
        uniqueName = self.getParticleHairName(evaluatedObj.original, psys)

        if (uniqueName in self.exported) or (not tools.isModifierVisible(self, pmod)):
            return AttrPlugin(uniqueName)
        
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
        data.segments       = segments
        data.matWorld       = tools.mat4x4ToTuple(evaluatedObj.matrix_world)
        data.width          = vrayFur.width
        data.fadeWidth      = vrayFur.make_thinner
        data.widthsInPixels = vrayFur.widths_in_pixels
        data.useHairBSpline = pset.use_hair_bspline
        
        gatherRadii(pointsPerStrand, totalParticles, pset, data)

        fnCoHair = psys.co_hair

        self.ts.timeThis('collect_data_hair_verts', lambda: gatherPoints(evaluatedObj, firstExported, totalParticles, pointsPerStrand, fnCoHair, data))
        
        objMesh = evaluatedObj.to_mesh(preserve_all_data_layers=True, depsgraph=self.dg)
        self.exportUVs(objMesh, pmod, firstExported, totalParticles, psys, parents, data)
    
        # TODO: Colors are currently not rendered by VRay although they are being expotred
        self.exportColors(objMesh, pmod, firstExported, totalParticles, psys, parents, data)

        vray.pluginCreate(self.renderer, uniqueName, 'GeomMayaHair')
        vray.exportHair(self.renderer, data)
        self.objTracker.trackPlugin(getObjTrackId(evaluatedObj), data.name)

        self.exported.add(data.name)
        return AttrPlugin(data.name)


    def exportUVs(self, objMesh, mod, firstExported, totalParticles, psys, parents, data):
        uvLayers = objMesh.uv_layers

        if activeUV := HairExporter.findActiveUV(uvLayers):
            uvIndex = uvLayers.find(activeUV.name)
        else:
            uvIndex = -1
        #else:
        #    uvIndex = uvLayers.find(pset.uv_map_name)

        if uvIndex == -1 or not uvLayers[uvIndex].data:
            # TODO: Handle failure
            debug.printWarning(f"Hair export: No UV Layers found in object '{objMesh.name}'")
            return None

        fnUvOnEmitter = psys.uv_on_emitter
        
        self.ts.timeThis("collect_data_hair_uvs", lambda: gatherUVs(mod, firstExported, totalParticles, psys, uvIndex, fnUvOnEmitter, parents, data))
        

    def exportColors(self, mesh: bpy.types.Mesh, mod, firstExported, totalParticles, psys, parents, data: HairData):
        if len(mesh.color_attributes) > 0:
            # TODO: Handle multiple color layers, if present
            activeLayer = next(iter(l for l in mesh.vertex_colors if l.active_render), None)
            if activeLayer is None:
                return
            
            activeLayerIndex = mesh.vertex_colors.find(activeLayer.name)
            if activeLayerIndex == -1:
                return

            fColOnEmitter = psys.mcol_on_emitter
            self.ts.timeThis("collect_data_hair_colors", lambda: gatherVertColors(mod, firstExported, totalParticles, psys, activeLayerIndex, fColOnEmitter, parents, data))

        
    def getParticleHairName(self, obj, psys: bpy.types.ParticleSystem):
        return f"{Names.objectData(obj)}|{psys.name}"

    
    @staticmethod
    def findActiveUV(uvLayers):
        return next((uv for uv in uvLayers if uv.active_render), None)
        
        
        





