# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from array import array
import copy
from mathutils import Matrix

from vray_blender.exporting import tools
from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.lib.defs import AttrPlugin, ExporterContext, ExporterBase, PluginDesc
from vray_blender.lib.export_utils import exportPlugin
from vray_blender.lib.names import Names


from vray_blender.bin import VRayBlenderLib as vray

class SmokeData:
    def __init__(self, name):
        self.name = name
        self.cacheDir = ""
        self.transform: Matrix  = None
        self.domainRes: array   = None


class SmokeExporter(ExporterBase):
    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        self.exported = set()
        self.objTracker = ctx.objTrackers['OBJ']


    def exportVolume(self, obj: bpy.types.Object, exportGeometry: bool, isVisible, domainRes=[32,32,32]):
        assert obj.is_evaluated, f"Evaluated object expected: {obj.name}"

        data = SmokeData(Names.object(obj))
        data.cacheDir = bpy.path.abspath(obj.data.filepath)
        data.transform = tools.mat4x4ToTuple(obj.matrix_world)
        data.domainRes = domainRes

        return self._exportSmoke(obj, data, exportGeometry, isVisible)


    def exportFluidModifier(self, obj: bpy.types.Object, exportGeometry: bool, isVisible: bool):
        fluidModif = [m for m in obj.modifiers if m.type == "FLUID"][0]
        if fluidModif.domain_settings is None:
            return AttrPlugin()

        if fluidModif.fluid_type != "DOMAIN" or fluidModif.domain_settings.cache_data_format != "OPENVDB":
            return AttrPlugin()

        data = SmokeData(Names.object(obj))

        curFrame = bpy.context.scene.frame_current
        currentCacheFileName = "fluid_data_" + '0'*(4-len(str(curFrame))) + str(curFrame) + ".vdb"
        data.cacheDir = bpy.path.abspath(fluidModif.domain_settings.cache_directory + "\\data\\" + currentCacheFileName)
        transf = copy.deepcopy(obj.matrix_world)

        # TODO: currently the smoke has an offset and this transformation serves to correct it
        # better solution should be found in the future
        dim = obj.dimensions/2
        for i in range(0,3):
            transf[i][3] -= dim[i]
            transf[i][i] *= 0.8


        data.transform = tools.mat4x4ToTuple(transf)
        data.domainRes = fluidModif.domain_settings.domain_resolution

        return self._exportSmoke(obj, data, exportGeometry, isVisible)


    def _exportSmoke(self, obj: bpy.types.ID, data: SmokeData, exportGeometry: bool, isVisible: bool):
        assert obj.is_evaluated, f"Evaluated object expected: {obj.name}"
        # NOTE: !!! These names should be the same as the ones used by the C++ exporter implementation
        pluginName = f'{data.name}@PhxShaderSim'

        if exportGeometry:
            vray.exportSmoke(self.renderer, data)

        # Export only the visibility state. The plugin must have been created already.
        vray.pluginUpdateInt(self.renderer, pluginName, 'enabled', isVisible)

        objTrackId = getObjTrackId(obj)

        self.objTracker.trackPlugin(objTrackId, pluginName)
        self.objTracker.trackPlugin(objTrackId, f'{pluginName}@PhxShaderCache')
        self.objTracker.trackPlugin(objTrackId, '__PhxShaderGlobalVolume__')

        return AttrPlugin(pluginName)