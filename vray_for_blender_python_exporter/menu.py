# This file contains definitions for the V-Ray menu in the main Blender menu bar

import bpy

from vray_blender.operators import VRAY_OT_render, VRAY_OT_render_interactive, VRAY_OT_cloud_submit
from vray_blender.ui.classes import VRayEngines
from vray_blender.ui.icons import getIcon, getUIIcon

from vray_blender.bin import VRayBlenderLib as vray


class VRAY_MT_help(bpy.types.Menu):
    bl_label = 'Help'
    bl_idname = 'VRAY_MT_help'

    @classmethod
    def poll(cls, context):
        return context.scene.render.engine in VRayEngines 
    
    def draw(self, context):
        self.layout.operator(VRAY_OT_show_about_dialog.bl_idname, icon_value=getUIIcon(VRAY_OT_show_about_dialog))
        
        opDocSite = self.layout.operator('wm.url_open', text='Online Documentation', icon_value=getIcon("VRAY_LOGO"))
        opDocSite.url = 'https://docs.chaos.com/display/VBLD'

        opHelpSite = self.layout.operator('wm.url_open', text='Online Help Center', icon='URL')
        opHelpSite.url = 'https://support.chaos.com'


class VRAY_MT_camera(bpy.types.Menu):
    bl_label = 'Camera'
    bl_idname = 'VRAY_MT_camera'

    @classmethod
    def poll(cls, context):
        return context.scene.render.engine in VRayEngines 
    
    def draw(self, context):
        from vray_blender.ui import menus
        physCamera = menus.VRAY_OT_add_physical_camera
        self.layout.operator(physCamera.bl_idname, text="V-Ray Physical Camera", icon_value=getUIIcon(physCamera))


class VRAY_MT_lights(bpy.types.Menu):
    bl_label = 'Lights'
    bl_idname = 'VRAY_MT_lights'

    @classmethod
    def poll(cls, context):
        return context.scene.render.engine in VRayEngines 
    
    def draw(self, context):
        from vray_blender.ui import menus
        menus.addVRayLightsToMenu(self, context)
        

class VRAY_MT_geometry(bpy.types.Menu):
    bl_label = 'Geometry'
    bl_idname = 'VRAY_MT_geometry'

    @classmethod
    def poll(cls, context):
        return context.scene.render.engine in VRayEngines 
    
    def draw(self, context):
        from vray_blender.ui import menus
        vrayProxyOp = menus.VRAY_OT_add_object_proxy

        # To be enabled when V-Ray Scene import is re-implemented
        # vraySceneOp = menus.VRAY_OT_add_object_vrayscene
        # self.layout.operator(vraySceneOp.bl_idname, text="V-Ray Scene", icon_value=getUIIcon(vraySceneOp))
        
        self.layout.operator(vrayProxyOp.bl_idname, text="V-Ray Proxy", icon_value=getUIIcon(vrayProxyOp))

class VRAY_MT_main(bpy.types.Menu):
    """ V-Ray menu item in main Blender menu bar """
    bl_label = "V-Ray"
    bl_idname = 'VRAY_MT_main'

    @classmethod
    def poll(cls, context):
        return context.scene.render.engine in VRayEngines 
    
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
        layout.operator(VRAY_OT_open_cosmos_browser.bl_idname, icon_value=getUIIcon(VRAY_OT_open_cosmos_browser))
        layout.separator()
        layout.menu(VRAY_MT_camera.bl_idname)
        layout.menu(VRAY_MT_lights.bl_idname)
        layout.menu(VRAY_MT_geometry.bl_idname)
        layout.separator()
        layout.menu(VRAY_MT_help.bl_idname)
        layout.operator(VRAY_OT_show_account_status.bl_idname)


class VRAY_OT_open_collaboration(bpy.types.Operator):
    bl_idname       = "vray.open_collaboration"
    bl_label        = "Chaos Collaboration"
    bl_description  = "Open Chaos Collaboration"

    def execute(self, context):
        from vray_blender import bl_info, build_number

        hostInfo = vray.HostInfo()
        hostInfo.vrayVersion    = ".".join(bl_info['version'])
        hostInfo.buildVersion   = build_number.BUILD
        hostInfo.blenderVersion = ".".join((str(i) for i in bl_info['blender']))

        vray.openCollaboration(hostInfo)
        return {'FINISHED'}


class VRAY_OT_open_cosmos_browser(bpy.types.Operator):
    bl_idname =      "vray.open_cosmos_browser"
    bl_label =       "Cosmos Browser"
    bl_description = "Open Cosmos Browser"

    def execute(self, context):
        vray.openCosmos()
        return {'FINISHED'}
    

class VRAY_OT_open_vfb(bpy.types.Operator):
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



class VRAY_OT_show_about_dialog(bpy.types.Operator):
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
    

class VRAY_OT_show_account_status(bpy.types.Operator):
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
        VRAY_OT_open_vfb,
        VRAY_OT_show_about_dialog,
        VRAY_OT_show_account_status,

        VRAY_MT_help,
        VRAY_MT_camera,
        VRAY_MT_lights,
        VRAY_MT_geometry,
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