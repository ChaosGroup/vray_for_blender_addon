from vray_blender.lib import export_utils
from vray_blender.lib.names import Names
from vray_blender.lib.defs import ExporterContext, ExporterBase, PluginDesc, SceneStats
from vray_blender.plugins import PLUGINS
from vray_blender.exporting.tools import IGNORED_PLUGINS


class SettingsExporter(ExporterBase):
    """ Export all settings plugins
    """
    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        self.stats = SceneStats()


    def export(self):
        # * Some plugins require that SettingsOutput to be exported first, so its export is done 
        #   earlier in the export sequence.
        # * SettingsCurrentFrame should only be exported for interactive renders, so its export
        #   is triggered manually.
        skipSettings = {'SettingsOutput', 'SettingsCurrentFrame'}

        toExport = [pl for pl in PLUGINS['SETTINGS'] if pl not in IGNORED_PLUGINS.union(skipSettings)]

        for pluginType in toExport: 
            self.exportPlugin(pluginType)

        return self.stats


    def exportPlugin(self, pluginType: str):
        scene = self.dg.scene
        vrayScene = scene.vray

        if pluginType.startswith('Filter') and vrayScene.SettingsImageSampler.filter_type != pluginType:
            return
        
        propGroup = getattr(vrayScene, pluginType)
        if not propGroup:
            return
        
        # Set plugin name the camelCase version of plugin type
        pluginName = Names.singletonPlugin(pluginType)
       
        plDesc = PluginDesc(pluginName, pluginType)
        plDesc.vrayPropGroup = propGroup
        export_utils.exportPlugin(self, plDesc)
        
