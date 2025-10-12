# This file contains definitions for the V-Ray menu in the main Blender menu bar

import bpy
import sys

from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
from vray_blender.engine.renderer_vantage import VRayRendererVantageLiveLink, VantageInitStatus
from vray_blender.operators import VRAY_OT_render, VRAY_OT_render_interactive, VRAY_OT_cloud_submit
from vray_blender.ui.classes import pollEngine, disableLayoutInEditMode
from vray_blender.ui.icons import getIcon, getUIIcon
from vray_blender.lib.sys_utils import activeRendererExists
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes.importing import convertMaterial
from vray_blender.engine.render_engine import VRayRenderEngine
from vray_blender.operators import VRAY_OT_message_box_base

from vray_blender.bin import VRayBlenderLib as vray

class VRAY_MT_help(bpy.types.Menu):
    bl_label = 'Help'
    bl_idname = 'VRAY_MT_help'

    @classmethod
    def poll(cls, context):
        return pollEngine(context)

    def draw(self, context):
        self.layout.operator(VRAY_OT_show_about_dialog.bl_idname, icon_value=getUIIcon(VRAY_OT_show_about_dialog))

        opDocSite = self.layout.operator('wm.url_open', text='Online Documentation', icon_value=getIcon("VRAY_LOGO"))
        opDocSite.url = 'https://documentation.chaos.com/space/VBLD'

        opHelpSite = self.layout.operator('wm.url_open', text='Online Help Center', icon='URL')
        opHelpSite.url = 'https://support.chaos.com'

        opHelpSite = self.layout.operator('wm.url_open', text='V-Ray Ideas Portal', icon='URL')
        opHelpSite.url = 'http://chaos.com/ideas/vray-blender'


class VRAY_MT_camera(bpy.types.Menu):
    bl_label = 'Camera'
    bl_idname = 'VRAY_MT_camera'

    @classmethod
    def poll(cls, context):
        return pollEngine(context)

    def draw(self, context):
        from vray_blender.ui import menus

        # Forbid creation of cameras in Edit mode (it can produce errors).
        disableLayoutInEditMode(self.layout, context)

        physCamera = menus.VRAY_OT_add_physical_camera
        self.layout.operator(physCamera.bl_idname, text="V-Ray Physical Camera", icon_value=getUIIcon(physCamera))


class VRAY_MT_lights(bpy.types.Menu):
    bl_label = 'Lights'
    bl_idname = 'VRAY_MT_lights'

    @classmethod
    def poll(cls, context):
        return pollEngine(context)

    def draw(self, context):
        from vray_blender.ui import menus
        menus.addVRayLightsToMenu(self, context)


class VRAY_MT_geometry(bpy.types.Menu):
    bl_label = 'Geometry'
    bl_idname = 'VRAY_MT_geometry'

    @classmethod
    def poll(cls, context):
        return pollEngine(context)

    def draw(self, context):
        from vray_blender.ui import menus
        vrayProxyOp = menus.VRAY_OT_add_object_proxy
        vraySceneOp = menus.VRAY_OT_add_object_vrayscene
        vrayFurOp = menus.VRAY_OT_add_object_fur

        enableVrscene = not VRayRendererIprViewport.isActive()
        vraySceneLayout = self.layout.column()
        vraySceneLayout.active = enableVrscene
        vraySceneLayout.enabled = enableVrscene

        vraySceneLayout.operator(vraySceneOp.bl_idname, text="V-Ray Scene", icon_value=getUIIcon(vraySceneOp))
        self.layout.operator(vrayProxyOp.bl_idname, text="V-Ray Proxy", icon_value=getUIIcon(vrayProxyOp))
        self.layout.operator(vrayFurOp.bl_idname, text="V-Ray Fur", icon_value=getUIIcon(vrayFurOp))

class VRAY_MT_cosmos(bpy.types.Menu):
    bl_label = 'Chaos Cosmos'
    bl_idname = 'VRAY_MT_cosmos'

    @classmethod
    def poll(cls, context):
        return pollEngine(context)

    def draw(self, context):
        self.layout.operator(VRAY_OT_open_cosmos_browser.bl_idname, icon_value=getUIIcon(VRAY_OT_open_cosmos_browser))
        self.layout.operator(VRAY_OT_relink_cosmos_assets.bl_idname, icon_value=getUIIcon(VRAY_OT_relink_cosmos_assets))


class VRAY_MT_tools(bpy.types.Menu):
    bl_label = 'Tools'
    bl_idname = 'VRAY_MT_tools'

    @classmethod
    def poll(cls, context):
        return pollEngine(context)

    def draw(self, context):
        self.layout.operator(VRAY_OT_convert_materials.bl_idname, icon_value=getUIIcon(VRAY_OT_convert_materials))


class VRAY_MT_main(bpy.types.Menu):
    """ V-Ray menu item in main Blender menu bar """
    bl_label = "V-Ray"
    bl_idname = 'VRAY_MT_main'

    @classmethod
    def poll(cls, context):
        return pollEngine(context)

    def draw(self, context):
        layout = self.layout

        renderOps = layout.column()
        renderOps.enabled = vray.isInitialized()
        renderOps.operator(VRAY_OT_open_vfb.bl_idname, icon_value=getUIIcon(VRAY_OT_open_vfb))
        renderOps.operator(VRAY_OT_render.bl_idname, icon_value=getUIIcon(VRAY_OT_render))
        renderOps.operator(VRAY_OT_render_interactive.bl_idname, icon_value=getUIIcon(VRAY_OT_render_interactive))
        layout.operator(VRAY_OT_cloud_submit.bl_idname, icon_value=getUIIcon(VRAY_OT_cloud_submit))

        layout.separator()
        layout.operator(VRAY_OT_open_collaboration.bl_idname, icon_value=getUIIcon(VRAY_OT_open_collaboration))
        layout.separator()
        layout.menu(VRAY_MT_cosmos.bl_idname)
        if vray.withDR2 and sys.platform == "win32":
            layout.separator()
            layout.operator(VRAY_OT_vantage_live_link.bl_idname)
        layout.separator()
        layout.menu(VRAY_MT_camera.bl_idname)
        layout.menu(VRAY_MT_lights.bl_idname)
        layout.menu(VRAY_MT_geometry.bl_idname)
        layout.separator()
        layout.menu(VRAY_MT_tools.bl_idname)
        layout.separator()
        layout.menu(VRAY_MT_help.bl_idname)
        layout.operator(VRAY_OT_show_account_status.bl_idname)


class VRAY_OT_open_collaboration(VRayOperatorBase):
    bl_idname       = "vray.open_collaboration"
    bl_label        = "Chaos Collaboration"
    bl_description  = "Open Chaos Collaboration"

    def execute(self, context):
        from vray_blender import bl_info, build_number

        hostInfo = vray.HostInfo()
        hostInfo.vrayVersion    = ".".join(bl_info['version'])
        hostInfo.buildVersion   = build_number.BUILD
        # Note that this is 4.2(lowest supported version hard coded in bl_info)
        hostInfo.blenderVersion = ".".join((str(i) for i in bl_info['blender']))

        vray.openCollaboration(hostInfo)
        return {'FINISHED'}

class VRAY_OT_open_cosmos_browser(VRayOperatorBase):
    bl_idname =      "vray.open_cosmos_browser"
    bl_label =       "Cosmos Browser"
    bl_description = "Open Cosmos Browser"

    def execute(self, context):
        vray.openCosmos()
        return {'FINISHED'}

from vray_blender.utils.cosmos_handler import cosmosHandler, VRAY_OT_show_cosmos_info_popup, VRAY_OT_dummy

class VRAY_OT_relink_cosmos_assets(VRayOperatorBase):
    bl_idname       = "vray.relink_cosmos_assets"
    bl_label        = "Download Cosmos Assets"
    bl_description  = "Download and Relink Cosmos Assets"

    @classmethod
    def poll(cls, context):
        return pollEngine(context) and not activeRendererExists()

    def execute(self, context: bpy.types.Context):
        cosmosHandler.downloadMissingAssets(self, context)
        return { 'FINISHED' }

    def invoke(self, context, event):
        return cosmosHandler.checkMissingAssets(self, context, event)


class VRAY_OT_convert_materials(VRAY_OT_message_box_base):
    bl_idname      = "vray.convert_materials"
    bl_label       = "Convert Materials"
    bl_description = "Convert Cycles Materials to V-Ray"
    bl_options     = { "UNDO" }

    @classmethod
    def poll(cls, context):
        return pollEngine(context)

    def execute(self, context):
        converted = 0
        for material in bpy.data.materials:
            if (not material.vray.is_vray_class) and material.use_nodes: 
                convertMaterial(material, self)
                converted += 1
        self.report({'INFO'}, f"Converted {converted} material(s)")

        return { 'FINISHED' }

    def invoke(self, context, event):
        self._centerDialog(context, event)
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        self.layout.label(text="If there are V-Ray nodes in any Cycles material tree")
        self.layout.label(text="they will be deleted before the conversion.")

class VRAY_OT_vantage_live_link(VRayOperatorBase):
    bl_idname       = "vray.vantage_live_link"
    bl_label        = "Vantage Live Link"
    bl_description  = "Vantage Live Link"

    def invoke(self, context, event):
        if (status := VRayRendererVantageLiveLink.checkAndReportVantageState()) != VantageInitStatus.Success:
            return bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message="Vantage Live Link failed: " + status.value)
        return self.execute(context)

    def execute(self, context: bpy.types.Context):
        VRayRenderEngine.stopInteractiveRenderer()
        VRayRenderEngine.startVantageLiveLink()
        return { 'FINISHED' }

class VRAY_OT_open_vfb(VRayOperatorBase):
    bl_idname =      "vray.open_vfb"
    bl_label =       "V-Ray VFB"
    bl_description = "Open VFB"

    def execute(self, context):
        vfbAlwaysOnTop = context.scene.vray.Exporter.display_vfb_on_top
        vray.openVFB()
        vray.setVfbOnTop(vfbAlwaysOnTop)

        return {'FINISHED'}

    @staticmethod
    def openVfb(renderer, alwaysOnTop):
        vray.openVFBWithRenderer(renderer)
        vray.setVfbOnTopWithRenderer(renderer, alwaysOnTop)



class VRAY_OT_show_about_dialog(VRayOperatorBase):
    bl_idname       = "vray.show_about_dialog"
    bl_label        = "About V-Ray for Blender"
    bl_description  = "About V-Ray for Blender"

    def execute(self, context):
        from vray_blender.version import getBuildVersionString
        from vray_blender import bl_info
        import json

        versionInfo = f"{bl_info['name']}<br>{getBuildVersionString()}"

        dlg = {
            'type': 'AboutDialog',
            'data': versionInfo
        }
        vray.showUserDialog(json.dumps(dlg))

        return {'FINISHED'}


class VRAY_OT_show_account_status(VRayOperatorBase):
    bl_idname       = "vray.show_account_status"
    bl_label        = "Chaos Account"
    bl_description  = "Show Chaos Account management dialog"

    def execute(self, context):
        from vray_blender.version import getBuildVersionString
        from vray_blender import bl_info
        import json

        dlg = {
            'type': 'AccountInfoDialog',
            'data': None
        }
        vray.showUserDialog(json.dumps(dlg))

        return {'FINISHED'}


def _drawMainMenu(self, context):
    layout = self.layout
    layout.menu(VRAY_MT_main.bl_idname)


def _drawExportAsVrsceneMenuItem(self, context):
    layout = self.layout.column()
    layout.operator_context = 'INVOKE_DEFAULT'
    layout.operator('vray.export_vrscene', text='V-Ray (.vrscene)')


def _getRegClasses():
    return (
        VRAY_OT_open_collaboration,
        VRAY_OT_open_cosmos_browser,
        VRAY_OT_relink_cosmos_assets,
        VRAY_OT_convert_materials,
        VRAY_OT_vantage_live_link,
        VRAY_OT_open_vfb,
        VRAY_OT_show_about_dialog,
        VRAY_OT_show_account_status,
        VRAY_OT_show_cosmos_info_popup,
        VRAY_OT_dummy,

        VRAY_MT_help,
        VRAY_MT_camera,
        VRAY_MT_lights,
        VRAY_MT_geometry,
        VRAY_MT_cosmos,
        VRAY_MT_tools,
        VRAY_MT_main
    )


def register():
    for regClass in _getRegClasses():
        bpy.utils.register_class(regClass)

    bpy.types.TOPBAR_MT_editor_menus.append(_drawMainMenu)
    bpy.types.TOPBAR_MT_file_export.append(_drawExportAsVrsceneMenuItem)


def unregister():
    for regClass in _getRegClasses():
        bpy.utils.unregister_class(regClass)

    bpy.types.TOPBAR_MT_file_export.remove(_drawExportAsVrsceneMenuItem)
    bpy.types.TOPBAR_MT_editor_menus.remove(_drawMainMenu)