import bpy
import json
import os
import sys
from vray_blender import version
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.lib import blender_utils, sys_utils
from vray_blender.plugins.system.compute_devices import ComputeDevices, ComputeDeviceSelector

def getVRayCloudPath():
    """
    If Chaos Cloud is installed on the system returns the path to it, else - None.
    """

    # check whether vcloud JSON file exists
    vcloudJsonDir = '%APPDATA%/Chaos/Cloud/client/' if bpy.app.build_platform == b'Windows' else '$HOME/.ChaosGroup/vcloud/client/'
    vcloudJsonDir = os.path.expandvars(vcloudJsonDir)
    vcloudJsonFilename = vcloudJsonDir + 'vcloud.json'
    if not os.path.exists(vcloudJsonFilename):
        return None

    # check whether the vcloud executable file exists via the "executable" field in the JSON file
    vcloudFullPath = ''
    with open(vcloudJsonFilename, 'r') as jsonFileId:
        jsonData = json.load(jsonFileId)
        vcloudFullPath = os.path.expandvars(jsonData['executable'])

    return vcloudFullPath if os.path.exists(vcloudFullPath) else None

def _getTelemetry(keyName):
    if keyName in os.environ:
        envVal = os.environ[keyName]
        if envVal in ["0", "1"]:
            return envVal != "0"

    if sys.platform == "win32":
        import winreg
        # Telemetry settings are stored in three places:
        # 1. 'keyName' envvar
        # 2. HKEY_CURRENT_USER\Environment\'keyName' for the current user
        # 3. HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment for the system
        # User setting takes precedence and is the one we can set from Blender w/o admin privileges
        userPath = "Environment"
        systemPath = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
        userVal = sys_utils.getWinRegistry(userPath, keyName)
        if userVal != None and userVal != "-1":
            return userVal != "0"
        else:
            systemVal = sys_utils.getWinRegistry(systemPath, keyName, winreg.HKEY_LOCAL_MACHINE)
            return systemVal == "1"
    else:
        telemetryConfigPath = os.path.expandvars('$HOME/.Chaos/telemetry/config.ini')
        telemetryConfig = sys_utils.parseIni(telemetryConfigPath)
        if keyName in telemetryConfig:
            return telemetryConfig[keyName] == "1"

    return False

def _getAnonymizedTelemetry(self):
    return _getTelemetry("VRAY_SEND_ANONYMIZED_FEEDBACK")

def _getPersonalizedTelemetry(self):
    return _getTelemetry("VRAY_SEND_PERSONALIZED_FEEDBACK")

def _setTelemetry(self, keyName, value):
    def _getNewTeleemtryValue(value):
        return "1" if value == True else "0"

    # Update envvar if it was present
    newValue = _getNewTeleemtryValue(value)
    if keyName in os.environ:
        os.environ[keyName] = newValue

    if sys.platform == "win32":
        pathToKey = "Environment"
        # Update registry value here too, so it works even if ZMQ server isn't running
        sys_utils.setWinRegistry(pathToKey, keyName, newValue)
    else:
        anonymizedTelemetry = newValue if keyName == "VRAY_SEND_ANONYMIZED_FEEDBACK" else _getNewTeleemtryValue(self.anonymized_telemetry)
        personalizedTelemetry = newValue if keyName == "VRAY_SEND_PERSONALIZED_FEEDBACK" else _getNewTeleemtryValue(self.personalized_telemetry)
        telemetryConfigPath = os.path.expandvars('$HOME/.Chaos/telemetry/config.ini')
        telemetryConfig = f"""; Configuration variable for controlling the Chaos anonymized telemetry.
VRAY_SEND_ANONYMIZED_FEEDBACK={anonymizedTelemetry}

; Configuration variable for controlling the Chaos personalized telemetry.
VRAY_SEND_PERSONALIZED_FEEDBACK={personalizedTelemetry}
"""
        with open(telemetryConfigPath, "w", encoding="utf-8") as file:
            file.write(telemetryConfig)

def _setAnonymizedTelemetry(self, value):
    _setTelemetry(self, "VRAY_SEND_ANONYMIZED_FEEDBACK", value)
    if not value and self.personalized_telemetry:
        self.personalized_telemetry = value

    # Send a message to ZMQ server to change the registry value, otherwise it won't pick up the change
    vray.setTelemetryState(value, self.personalized_telemetry)

def _setPersonalizedTelemetry(self, value):
    _setTelemetry(self, "VRAY_SEND_PERSONALIZED_FEEDBACK", value)

    # Send a message to ZMQ server to change the registry value, otherwise it won't pick up the change
    vray.setTelemetryState(self.anonymized_telemetry, value)


class VRayRenderNode(bpy.types.PropertyGroup):
    nodeName: bpy.props.StringProperty(
        name = "Node name",
        description = "The name of the render node",
        update=lambda self, context: blender_utils.markPreferencesDirty(context),
        default="Render Node"
    )

    address: bpy.props.StringProperty(
        name = "IP/Hostname",
        description = "Render node IP or hostname",
        update=lambda self, context: blender_utils.markPreferencesDirty(context),
        default="IP/Hostname"
    )

    port: bpy.props.IntProperty(
        name = "Port",
        description = "Distributed rendering port",
        min = 0,
        max = 65535,
        default = 20209
    )

    use: bpy.props.BoolProperty(
        name = "Use Node",
        description = "Use render node",
        default = True
    )


class VRayExporterPreferences(bpy.types.AddonPreferences):
    bl_idname = "vray_blender"

    vray_cloud_binary = getVRayCloudPath()
    detect_vray_cloud = vray_cloud_binary is not None

    anonymized_telemetry: bpy.props.BoolProperty(
        default = False,
        name="Enable anonymized Telemetry",
        description="Includes the most used Product functionality and/or parameter values. The data is not personally identifiable and is not tied to the user's individual account",
        get=_getAnonymizedTelemetry,
        set=_setAnonymizedTelemetry
    )

    personalized_telemetry: bpy.props.BoolProperty(
        default = False,
        name="Enable personalized Telemetry",
        description="Same as anonymized telemetry, but contains personally identifiable information tied to an individual user's license to help Chaos tailor and optimize the Product for better personal use",
        get=_getPersonalizedTelemetry,
        set=_setPersonalizedTelemetry
    )

    vantage_host: bpy.props.StringProperty(
        name = "Address",
        default = "localhost",
        description = "V-Ray Vantage Live Link host"
    )

    vantage_port: bpy.props.IntProperty(
        name = "Port",
        default = 20703,
        description = "V-Ray Vantage Live Link port"
    )

    use_remote_dispatcher: bpy.props.BoolProperty(
        name = "Use Remote Dispatcher",
        description = "Use a remote dispatcher server for distributed rendering.",
        default = False
    )

    dispatcher: bpy.props.PointerProperty(
        name="Dispatcher",
        type = VRayRenderNode,
        description = "V-Ray remote dispatcher node"
    )

    nodes: bpy.props.CollectionProperty(
        name = "Render Nodes",
        type =  VRayRenderNode,
        description = "V-Ray render nodes"
    )

    nodes_selected: bpy.props.IntProperty(
        name = "Render Node Index",
        default = -1,
        min = -1,
        max = 100
    )

    render_only_on_nodes: bpy.props.BoolProperty(
        name        = "Don't Use Local Machine",
        description = "Use distributed rendering excluding the local machine",
        default     = False
    )

    preferences_menu: bpy.props.EnumProperty(
        default = 'PREFERENCES_MENU_GENERAL',
        items = (
            ('PREFERENCES_MENU_GENERAL', "General", "General preferences settings"),
            ('PREFERENCES_MENU_LOGGING', "Logging", "Logging settings"),
            ('PREFERENCES_MENU_GPU_DEVICES', "Device selection", "Select which GPU devices will be used for rendering"),
            ('PREFERENCES_MENU_DR',  "Distributed rendering",  "Change Distributed rendering preferences"),
        ),
        description = "Preference menu selection",
        options = { 'HIDDEN' }
    )

    compute_devices: bpy.props.PointerProperty(
        name = "Compute Devices",
        description = "Select compute devices for rendering",
        type = ComputeDevices
    )

    def _updateLogLevel(self, context):
        from vray_blender import debug
        debug.setLogLevel(int(self.verbose_level), bool(self.enable_qt_logs))


    verbose_level: bpy.props.EnumProperty(
        name = "Log Level",
        description = "Specifies the verbosity level of information printed to the standard output",
        items = (
            ('0', "No information", "No information printed"),
            ('1', "Errors only",    "Only errors"),
            ('2', "Warnings",       "Errors and warnings"),
            ('3', "Info",           "Errors, warnings and informational messages"),
            ('4', "All",            "All output"),
        ),
        default = '2',
        update = _updateLogLevel
    )

    enable_qt_logs: bpy.props.BoolProperty(
        name = "Log Qt output",
        description = "Used to control logging from Qt",
        update = _updateLogLevel,
        default = False
    )

    loaded_from_scene: bpy.props.BoolProperty(
        name="Preferences loaded from the scene",
        default=False
    )

    ask_for_upgrade_confirm: bpy.props.BoolProperty(
        name="Ask for confirmation when upgrading scenes",
        description="When enabled will ask before upgrading scenes, if not upgrades will happen automatically",
        default=True
    )

    def _drawAboutPanel(self, layout):
        from vray_blender.menu import VRAY_OT_show_account_status
        box = layout.box()
        header, subLayout = box.panel(idname="about", default_closed=False)
        header.label(text="About")
        if subLayout:
            split = subLayout.split(factor=0.05, align=True)
            split.column()
            panel = split.column(align=True)
            panel.separator()

            subLayout = panel.column()
            subLayout.use_property_split = True
            subLayout.use_property_decorate = False

            rowVersion = subLayout.row()
            rowVersion.label(text=f"Version: {version.getBuildVersionString()}")

            rowAccount = subLayout.row()
            rowAccount.label(text="Chaos Account settings")
            rowAccount.operator(VRAY_OT_show_account_status.bl_idname, text="Open")

            rowCloudBinary = subLayout.row()
            if self.detect_vray_cloud:
                rowCloudBinary.label(text="Chaos Cloud {0}".format(self.vray_cloud_binary))
            else:
                rowCloudBinary.label(text="Chaos Cloud is not detected on your system!")

    def _drawTelemetryPanel(self, layout):
        box = layout.box()
        header, subLayout = box.panel(idname="Usage statistics sharing", default_closed=True)
        header.label(text="Usage statistics")
        if subLayout:
            split = subLayout.split(factor=0.05, align=True)
            split.column()

            panel = split.column(align=True)
            panel.separator()

            telemetryTextLabelCol = panel.column()
            telemetryTextLabelCol.label(text="The Improvement Program of Chaos Software (Chaos)")
            telemetryTextLabelCol.label(text="helps improve the Product by tracking general usage statistics")
            telemetryTextLabelCol.label(text="and to automatically collect crash information.")

            panel.separator(factor=2)
            panel.label(text="You can choose to enable (and change your choice at any time):")

            telemetryCol = panel.column()
            telemetryCol.separator(type="LINE")
            telemetryCol.prop(self, "anonymized_telemetry", text="Anonymized Telemetry")
            telemetryCol.label(text="Includes the most used Product functionality and/or parameter values.")
            telemetryCol.label(text="The data is not personally identifiable and is not tied")
            telemetryCol.label(text="to the user's individual account")
            telemetryCol.separator(type="LINE")

            personalTelemetryCol = telemetryCol.column()
            personalTelemetryCol.enabled = self.anonymized_telemetry
            personalTelemetryCol.prop(self, "personalized_telemetry", text="Personalized Telemetry")
            personalTelemetryCol.label(text="Same as anonymized telemetry, but contains personally identifiable")
            personalTelemetryCol.label(text="information tied to an individual user's license to help Chaos tailor")
            personalTelemetryCol.label(text="and optimize the Product for better personal use")

            telemetryCol.separator(type="LINE")
            telemetryCol.operator("wm.url_open", text="Learn more", icon='HELP',).url = "https://documentation.chaos.com/space/VBLD/117637472/Chaos+Telemetry"

    def _drawAdditionalSettingsPanel(self, layout):
        box = layout.box()
        header, subLayout = box.panel(idname="about", default_closed=True)
        header.label(text="Additional settings")
        if subLayout:
            split = subLayout.split(factor=0.05, align=True)
            split.column()
            panel = split.column(align=True)
            panel.separator()

            subLayout = panel.column()
            subLayout.use_property_split = True
            subLayout.use_property_decorate = False

            subLayout.prop(self, 'ask_for_upgrade_confirm')

    def _drawGeneralPanel(self, layout, context):
        self._drawAboutPanel(layout)
        self._drawTelemetryPanel(layout)
        self._drawAdditionalSettingsPanel(layout)

    def _drawLoggingPanel(self, layout, context):
        box = layout.box()
        header, subLayout = box.panel(idname="logging", default_closed=False)
        header.label(text="Logging")
        if subLayout:
            split = subLayout.split(factor=0.05, align=True)
            split.column()
            panel = split.column(align=True)
            panel.separator()

            subLayout = panel.column()
            subLayout.use_property_split = True
            subLayout.use_property_decorate = False

            subLayout.prop(self, "verbose_level")
            subLayout.prop(self, "enable_qt_logs")

    def _drawComputeDevicesPanel(self, layout, context):
        computeDevices = self.compute_devices

        header, subLayout = layout.box().panel(idname="Compute Devices", default_closed=False)
        header.label(text="V-Ray GPU Compute Devices")
        if subLayout:
            subLayout = subLayout.column()
            subLayout.use_property_split = True
            subLayout.use_property_decorate = False
            if sys.platform == "darwin":
                if devicesList := getattr(computeDevices, "devicesMetal"):
                    for device in devicesList:
                        deviceRow = subLayout.row()
                        deviceRow.prop(device, "deviceEnabled", text=device.deviceName)
            else:
                subLayout.prop(computeDevices, "gpuDeviceType")
                subLayout.separator()

                deviceType = computeDevices.gpuDeviceType
                if devicesList := getattr(computeDevices, "devicesCUDA" if deviceType == "0" else "devicesOptix"):
                    for device in devicesList:
                        deviceRow = subLayout.row()
                        deviceRow.prop(device, "deviceEnabled", text=device.deviceName)
                else:
                    noDevicesRow = subLayout.row()
                    noDevicesRow.label(text="No compute devices available")

    def _drawDistributedRenderingPanel(self, layout, context):
        header, subLayout = layout.box().panel(idname='dr_prefs', default_closed=False)
        header.label(text="V-Ray Distributed Rendering")
        if subLayout:
            split = subLayout.split(factor=0.05, align=True)
            split.column()
            panel = split.column(align=True)
            panel.separator()

            subLayout = panel.column()
            subLayout.use_property_split = True
            subLayout.use_property_decorate = False

            subLayout.prop(self, 'render_only_on_nodes')

            subLayout.prop(self, 'use_remote_dispatcher', text='Use Remote Dispatcher')
            col = subLayout.column()
            col.enabled = self.use_remote_dispatcher
            col.prop(self.dispatcher, "address", text='Remote Dispatcher')
            col.prop(self.dispatcher, "port", text=' ')

            row = panel.row()
            row.template_list('VRAY_UL_DR', '', self, 'nodes', self, 'nodes_selected', rows=3)
            col = row.column(align=True)
            col.operator('vray.render_nodes_add', text="", icon='ADD')
            col.operator('vray.render_nodes_remove', text="", icon='REMOVE')

            col = col.row().column(align=True)
            col.operator('vray.dr_nodes_load', text="", icon='FILE_FOLDER')
            col.operator('vray.dr_nodes_save', text="", icon='DISK_DRIVE')

        if False:
            # Draw the vantage settings on all operating systems, MacOS V-Ray Blender and
            # Vantage running on Windows is a valid use-case.
            header, subLayout = layout.box().panel(idname="live_link", default_closed=False)
            header.label(text='Vantage Live Link')
            if subLayout:
                subLayout = subLayout.column()
                subLayout.use_property_split = True
                subLayout.use_property_decorate = False

                subLayout.prop(self, 'vantage_host', text='Vantage Host')
                subLayout.prop(self, 'vantage_port', text=' ')

    def draw(self, context):
        layout = self.layout

        row = layout.row(align=True)
        row.prop(self, 'preferences_menu', expand=True)

        if self.preferences_menu == 'PREFERENCES_MENU_GENERAL':
            self._drawGeneralPanel(layout, context)
        elif self.preferences_menu == 'PREFERENCES_MENU_LOGGING':
            self._drawLoggingPanel(layout, context)
        elif self.preferences_menu == 'PREFERENCES_MENU_GPU_DEVICES':
            self._drawComputeDevicesPanel(layout, context)
        elif self.preferences_menu == 'PREFERENCES_MENU_DR':
            self._drawDistributedRenderingPanel(layout, context)


def getRegClasses():
    return (
        VRayRenderNode,
        VRayExporterPreferences,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
