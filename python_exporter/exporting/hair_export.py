# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import itertools
import math
import numpy as np

from vray_blender.exporting import tools
from vray_blender.lib.defs import AttrPlugin, ExporterBase, ExporterContext
from vray_blender.lib.names import Names
from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.lib import plugin_utils
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
        self.strandOffsets   = DataArray()
        self.pointRadii      = np.empty(shape=0, dtype=np.float32)
        self.vertColors      = np.empty(shape=0, dtype=np.float32)

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
    # Strands per np.fromiter batch. Small keeps the sampled list in cache; measurably
    # faster than one big batch, and bounds the live Python objects.
    EMITTER_CHUNK = 4096

    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        self.objTracker = ctx.objTrackers['OBJ']
        self.modTracker = ctx.objTrackers['MODIFIER']


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

        # Zero-copy pointer directly into Blender's curve_offsets int array.
        # C++ will compute per-strand point counts as diffs from these offsets.
        strandOffsets = DataArray(curves.curve_offset_data[0].as_pointer(), numCurves + 1)

        # Zero-copy pointer directly into Blender's radius attribute storage.
        pointRadiuses = DataArray.fromAttribute(curves, "radius")

        # UVs of strand roots ( the anchor points to the parent surface )
        # These are so far the only UVs we can obtain from Blender
        uvs = DataArray.fromAttribute(curves, "surface_uv_coordinate")
        uvs.count *= 2

        data = HairData(uniqueName, HairData.TYPE_CURVES)
        data.fadeWidth      = True
        data.widthsInPixels = False
        data.useHairBSpline = True
        data.points         = points
        data.strandOffsets  = strandOffsets
        data.pointRadii     = pointRadiuses
        data.uvs            = uvs

        plugin_utils.createPlugin(self, uniqueName, 'GeomMayaHair')
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
        if objMesh:
            self.objectsWithTempMeshes.append(evaluatedObj)

        uvIndex = -1
        activeLayerIndex = -1
        uvLayers = None
        if objMesh:
            uvLayers = objMesh.uv_layers
            if activeUV := HairExporter.findActiveUV(uvLayers):
                uvIndex = uvLayers.find(activeUV.name)

            if len(objMesh.color_attributes) > 0:
                # Note: In newer Blender versions this only works if the user creates a Face Corner+Byte color
                # attribute. mcol_on_emitter doesn't seem to work with any other attribute type.
                activeLayer = next((l for l in objMesh.vertex_colors if l.active_render), None)
                if activeLayer is not None:
                    activeLayerIndex = objMesh.vertex_colors.find(activeLayer.name)

        exportUVs = uvIndex != -1 and uvLayers[uvIndex].data
        exportColors = activeLayerIndex != -1

        strands = totalParticles - firstExported
        uvs = np.empty(2 * strands, dtype=np.float32) if exportUVs else np.empty(0, dtype=np.float32)
        colors = np.empty(3 * strands, dtype=np.float32) if exportColors else np.empty(0, dtype=np.float32)

        if exportUVs or exportColors:
            HairExporter.fillEmitterAttrs(psys, pmod, pset, parents, children, firstExported,
                                          totalParticles,
                                          uvIndex if exportUVs else -1,
                                          activeLayerIndex if exportColors else -1,
                                          uvs, colors)
        data.uvs = uvs
        data.vertColors = colors

        data.psys = psys.as_pointer()
        data.firstToExport = firstExported
        data.totalParticles = totalParticles
        data.shape = pset.shape
        objScale = evaluatedObj.matrix_world.median_scale
        data.rootRadius = (pset.root_radius * pset.radius_scale * objScale) / 2
        data.tipRadius = (pset.tip_radius * pset.radius_scale * objScale) / 2
        data.maxSteps = pointsPerStrand

        plugin_utils.createPlugin(self, uniqueName, 'GeomMayaHair')
        vray.exportHair(self.renderer, data)
        # Track both the object and the particle system settings. That way it can
        # be deleted if the object is removed or if the modifier is removed.
        self.objTracker.trackPlugin(getObjTrackId(evaluatedObj), data.name)
        self.objTracker.trackPlugin(getObjTrackId(pset), data.name)
        self.modTracker.trackPlugin(getObjTrackId(pset), data.name)

        self.persistedState.objDataTracker.trackParticlePluginOfData(Names.objectData(evaluatedObj), psys.name, uniqueName)
        return AttrPlugin(data.name)

    @staticmethod
    def fillEmitterAttrs(psys: bpy.types.ParticleSystem, pmod, pset, parents: int, children: int,
                         firstExported: int, totalParticles: int, uvIndex: int, colorIndex: int,
                         uvs, colors):
        """ Sample the emitter UV/color at each exported strand's root.

            uvIndex/colorIndex are -1 when that attribute is not exported.
        """
        exportUVs = uvIndex != -1
        exportColors = colorIndex != -1
        uvOnEmitter = psys.uv_on_emitter
        mcolOnEmitter = psys.mcol_on_emitter

        if children and parents and pset.child_type == 'SIMPLE':
            # 'Simple' children inherit the parent's emitter value, and are laid out
            # parent-major, so sample once per parent and tile.
            parentUVs = np.empty((parents, 2), dtype=np.float32) if exportUVs else None
            parentColors = np.empty((parents, 3), dtype=np.float32) if exportColors else None

            for i, particle in enumerate(psys.particles):
                if exportUVs:
                    parentUVs[i] = uvOnEmitter(pmod, particle, particle_no=i, uv_no=uvIndex)
                if exportColors:
                    parentColors[i] = mcolOnEmitter(pmod, particle, particle_no=i, vcol_no=colorIndex)

            firstChild = firstExported - parents
            reps = -(-(firstChild + (totalParticles - firstExported)) // parents)
            if exportUVs:
                uvs[:] = np.tile(parentUVs, (reps, 1))[firstChild:firstChild + len(uvs) // 2].reshape(-1)
            if exportColors:
                colors[:] = np.tile(parentColors, (reps, 1))[firstChild:firstChild + len(colors) // 3].reshape(-1)
            return

        if children and parents:
            # 'Interpolated' children each need their own call, but the 'particle' argument
            # is never dereferenced for a child index, so one parent serves for all of them.
            # Batching through np.fromiter beats storing into the array element by element.
            particle = psys.particles[0]
            chain = itertools.chain.from_iterable
            strands = totalParticles - firstExported

            for start in range(0, strands, HairExporter.EMITTER_CHUNK):
                count = min(HairExporter.EMITTER_CHUNK, strands - start)
                base = firstExported + start
                if exportUVs:
                    sampled = [uvOnEmitter(pmod, particle, particle_no=base + k, uv_no=uvIndex)
                               for k in range(count)]
                    uvs[2 * start: 2 * (start + count)] = \
                        np.fromiter(chain(sampled), np.float32, 2 * count)
                if exportColors:
                    sampled = [mcolOnEmitter(pmod, particle, particle_no=base + k, vcol_no=colorIndex)
                               for k in range(count)]
                    colors[3 * start: 3 * (start + count)] = \
                        np.fromiter(chain(sampled), np.float32, 3 * count)
            return

        # No children: one strand per parent particle, and here 'particle' is really read.
        iu = 0
        ic = 0
        for pindex, particle in enumerate(psys.particles):
            if exportUVs:
                uv = uvOnEmitter(pmod, particle, particle_no=pindex, uv_no=uvIndex)
                uvs[iu] = uv[0]
                uvs[iu + 1] = uv[1]
                iu += 2
            if exportColors:
                color = mcolOnEmitter(pmod, particle, particle_no=pindex, vcol_no=colorIndex)
                colors[ic] = color[0]
                colors[ic + 1] = color[1]
                colors[ic + 2] = color[2]
                ic += 3


    def getParticleHairName(self, obj, psys: bpy.types.ParticleSystem):
        return f"{Names.objectData(obj)}|{psys.name}"


    @staticmethod
    def findActiveUV(uvLayers):
        return next((uv for uv in uvLayers if uv.active_render), None)
