from vray_blender import debug
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.node_exporters.uvw_node_export import exportDefaultUVWGenChannel
from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.lib import path_utils, plugin_utils, sys_utils
from vray_blender.lib.blender_utils import getShadowAttr, setShadowAttr
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib.draw_utils import UIPainter
from vray_blender.lib.names import Names
from vray_blender.lib.sys_utils import getAppSdkLibPath
from vray_blender.nodes.tools import isInputSocketLinked, isVrayNodeTree
from vray_blender.nodes.utils import getVrayPropGroup, getNodeOfPropGroup, getMaterialFromNode
from vray_blender.plugins import getPluginModule
from vray_blender.vray_tools.vray_proxy import PreviewAction

import bpy, json, math, os, struct, queue
from io import BufferedReader
from pathlib import PurePath

plugin_utils.loadPluginOnModule(globals(), __name__)

# Flag indicating if the user has a scanned materials license. Set in scannedLicenseCallback(...).
# Note that there is a check in the ZMQ server for scanned material licensing as well.
hasLicense = False

def nodeInit(node: bpy.types.Node):
    vray.checkScannedLicense()

def registerScannedNodes():
    """ Called from the Load Post event handler"""

    for tree in bpy.data.materials:
        if isVrayNodeTree(tree.node_tree, "Material"):
            for node in tree.node_tree.nodes:
                if hasattr(node, "BRDFScanned"):
                    vray.checkScannedLicense()
                    return

class SerializablePreset:
    def __init__(self, data):
        (
            self.result,
            self.ccior,
            self.plain,
            self.bumpmul,
            self.bumpstart,
            self.depthmul,
            self.ccbump,
            self.ccmul,
            self.ccmetalrefl,
            self.orggls,
            self.orgglvar
        ) = data

def _dumpPreset(scannedFile: str, binFile: str):
    from subprocess import PIPE, run

    vrayToolsApp = path_utils.getBinTool(sys_utils.getPlatformName("vraytools"))

    cmd = [vrayToolsApp]
    cmd.extend(['-vrayLib', getAppSdkLibPath()])
    cmd.extend(['-action', PreviewAction.ScannedPreset])
    cmd.extend(['-input', scannedFile])
    cmd.extend(['-output', binFile])

    result = run(cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True)

    debug.printInfo(f"Running scanned preset tool: {' '.join(cmd)}")

    if result.returncode != 0:
        debug.printError(result.stdout)
        debug.printError(result.stderr)
        return f"Error generating bin file: {result.returncode}"

    if not os.path.exists(binFile):
       return "Error generating bin file: tool returned success, but the file was not generated"

    return None

def _readScannedPreset(filename: str):
    with open(filename, "rb") as f:
        reader = BufferedReader(f)

        lenBytes = reader.read(4)
        (infoLen,) = struct.unpack("i", lenBytes)

        scannedInfo = reader.read(infoLen).decode("utf-8", errors="replace")
        presetData = reader.read(4 + 4 + 4 * 9)
        unpacked = struct.unpack("<ififfffffff", presetData)
        preset = SerializablePreset(unpacked)

        return scannedInfo, preset
    return None, None


def _vrayGlossinessToScanned(glossiness: float) -> float:
    """ Convert a V-Ray [0-1] glossiness value to scanned material glossiness """
    if glossiness > 0.9999:
        return 10000.0

    N = 1000
    du = 1.0 / N
    s = 1e-6
    p = 2 * math.pow(1.0 - glossiness, 3.5)

    u = 0.5 * du
    while u < 1.0:
        s += math.sqrt(1.0 - math.pow(u, p))
        u += du

    return float(2 * N / s)

def _scannedGlossinessToVRay(glossiness: float) -> float:
    """ Convert a scanned material glossiness value to more user friendly [0-1] V-Ray range """
    if glossiness > 9999.0:
        return 1.0

    rng = [0.0, 1.0]
    x = 0.0

    for _ in range(24):
        x = (rng[0] + rng[1]) / 2.0
        scannedGlossiness = _vrayGlossinessToScanned(x)
        if scannedGlossiness > glossiness:
            rng[1] = x
        else:
            rng[0] = x

    return x

def nodeDrawSide(context, layout: bpy.types.UILayout, node):
    # There isn't a multiline text box in blender so we draw labels row by row.
    propGroup = getVrayPropGroup(node)
    layout.prop(propGroup, 'file')
    layout.separator()
    col = layout.column(align=True)
    box = col.box()

    brdfScannedModule = getPluginModule('BRDFScanned')
    if not brdfScannedModule.hasLicense:
        r = box.row(align=True)
        r.alignment = 'RIGHT'
        r.label(text="[No License]", icon='ERROR')
    node = getNodeOfPropGroup(propGroup)
    rows = node.BRDFScanned.file_info.strip().split("\n")
    for row in rows:
        r = box.row(align=True)
        r.alignment = 'RIGHT'
        r.label(text=row)

    layout.separator()

    col = layout.column()
    col.enabled=brdfScannedModule.hasLicense
    painter = UIPainter(context, brdfScannedModule, propGroup)
    painter.renderPluginUI(col)

def onFileUpdate(brdfScanned, context = None, attrName = ''):
    scannedPath = path_utils.formatResourcePath(brdfScanned.file, False)
    if not os.path.exists(scannedPath):
        brdfScanned.file_info = '[Invalid Scanned Material Path]'
        return
    binScannedFile = str(PurePath(path_utils.getV4BTempDir(), PurePath(scannedPath).stem).with_suffix('.vrbin'))
    if err := _dumpPreset(scannedPath, binScannedFile):
        debug.printError(err)
        return
    info, preset = _readScannedPreset(binScannedFile)
    os.remove(binScannedFile)
    if preset.result == True:
        # plain = 3 is a compatiblity option that was added so that assets could force triplanar mapping
        # without changing the scanned material itself in V-Ray Core.
        if preset.plain == 3:
            brdfScanned.plain = "1"
            brdfScanned.enable_triplanar = True
        else:
            brdfScanned.plain = str(preset.plain)
        brdfScanned.ccior = preset.ccior
        brdfScanned.bumpmul = preset.bumpmul
        brdfScanned.bumpstart = preset.bumpstart
        brdfScanned.depthmul = preset.depthmul
        brdfScanned.ccbump = preset.ccbump
        brdfScanned.ccmul = preset.ccmul
        brdfScanned.enable_clear_coat = (preset.ccior != 1.0)
        brdfScanned.ccglossy = _scannedGlossinessToVRay(preset.orggls)
        brdfScanned.ccglossyvar = preset.orgglvar
    # Set this at the end, it's used to prevent unnecessary parameter encoding from just loading a preset.
    brdfScanned.file_info = info

def _fillScannedPluginDesc(pluginDesc: PluginDesc, node: bpy.types.Node, propGroup):
    pluginDesc.vrayPropGroup = propGroup

    pluginDesc.setAttribute("invgamma", 1.0/propGroup.invgamma)
    triplanarFlags = 0
    if propGroup.triplanar:
        triplanarFlags |= 1
        if propGroup.triplanar_random_offset:
            triplanarFlags |= 2
        if propGroup.triplanar_random_rotation:
            triplanarFlags |= 4
    pluginDesc.setAttribute("triplanar", triplanarFlags)

    usedMaps = 0
    if propGroup.use_paint:
        usedMaps |= 1
    if propGroup.use_filter:
        usedMaps |= 2
    if propGroup.enable_clear_coat:
        ccmultSocket = getInputSocketByAttr(node, "ccmul")
        if ccmultSocket and isInputSocketLinked(ccmultSocket):
            usedMaps |= 4

        pluginDesc.setAttribute("ccglossy", _vrayGlossinessToScanned(propGroup.ccglossy))
    else:
        pluginDesc.setAttribute("ccmul", 0.0)
        pluginDesc.setAttribute("ccmetalrefl", 0.0)
        pluginDesc.setAttribute("ccior", 1.0)
        pluginDesc.setAttribute("ccbump", 0.0)
    pluginDesc.setAttribute("usedmaps", usedMaps)

def onParameterUpdate(brdfScanned, context, attrName):
    if not getPluginModule("BRDFScanned").hasLicense:
        return
    # If the file is not set that means we are still loading a preset.
    if not brdfScanned.file_info:
        return
    node = getNodeOfPropGroup(brdfScanned)
    mat = getMaterialFromNode(node)
    pluginDesc = PluginDesc("dummy", "BRDFScanned")
    _fillScannedPluginDesc(pluginDesc, node, brdfScanned)
    for name in dir(brdfScanned):
        if not name.startswith('__'):
            attr = getattr(brdfScanned, name)
            if isinstance(attr, float) or isinstance(attr, int):
                pluginDesc.setAttribute(name, attr, False)

    pluginDesc.setAttribute("plain", int(brdfScanned.plain))
    pluginDesc.setAttribute("UnfRefl", int(brdfScanned.UnfRefl))
    vray.encodeScannedParameters(mat.session_uid, node.name, json.dumps(pluginDesc.attrs))

def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node
    propGroup = getVrayPropGroup(node)

    pluginName = Names.treeNode(nodeCtx)
    # Changing the file name doesn't work in IPR so the plugin should be re-created, but
    # it's disabled for now since it can cause errors because the 'parent' MtlSingleBRDF
    # might not be always re-exported thus losing it's brdf reference.
    # filePrevValue = getShadowAttr(propGroup, 'file')
    # if filePrevValue != propGroup.file and nodeCtx.exporterCtx.interactive:
        # vray.pluginRemove(nodeCtx.renderer, pluginName)
        # setShadowAttr(propGroup, 'file', propGroup.file)
    pluginDesc = PluginDesc(pluginName, "BRDFScanned")
    _fillScannedPluginDesc(pluginDesc, node, propGroup)

    mappingSock = getInputSocketByAttr(node, "uvwgen")
    if mappingSock and isInputSocketLinked(mappingSock):
        uvwPlugin = commonNodesExport.exportLinkedSocket(nodeCtx, mappingSock)
        pluginDesc.setAttribute("uvwgen", uvwPlugin)
    else:
        uvwPlugin = exportDefaultUVWGenChannel(nodeCtx)
        pluginDesc.setAttribute("uvwgen", uvwPlugin)

    skippedAttrs = [ "invgamma", "triplanar", "ccglossy", "uvwgen", "param_block" ]
    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc, skippedSockets=skippedAttrs)

    plugin = commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)

    if paramBlockString := propGroup.param_block:
        paramBlock = list(map(int, paramBlockString.split(',')))
        plugin_utils.updateValue(nodeCtx.renderer, pluginName, "param_block", paramBlock)
    return plugin

def scannedLicenseCallback(license):
    getPluginModule("BRDFScanned").hasLicense = license

# Similar to the Cosmos import queue we have to forward all parameter modifications(param_block)
# in this case coming from random threads to the main thread. Requests coming from the server
# are collected in this queue and are handeled in scannedParamBlockFunction(...) on a timer.
scannedQueue = queue.Queue()

# Called when a new request to update a param_block of a scanned material comes.
def scannedParamBlockCallback(materialId, nodeName, paramBlock):
    global scannedQueue
    scannedQueue.put((materialId, nodeName, paramBlock))

# Runs on a 1sec timer and forwards param_block updates to the main Blender thread.
def scannedParamBlockFunction():
    global scannedQueue

    try:
        while not scannedQueue.empty():
            (materialId, nodeName, paramBlock) = scannedQueue.get()
            for material in bpy.data.materials:
                if material.session_uid == materialId:
                    for node in material.node_tree.nodes:
                        if node.name == nodeName:
                            node.BRDFScanned.param_block = ','.join(map(str, paramBlock))
    except Exception as e:
        debug.printExceptionInfo(e, "BRDFScanned.scannedParamBlockFunction")

    return 1.0

def registerScannedImportTimerFunction():
    if not bpy.app.timers.is_registered(scannedParamBlockFunction):
        bpy.app.timers.register(scannedParamBlockFunction)

def register():
    registerScannedImportTimerFunction()

def unregister():
    if bpy.app.timers.is_registered(scannedParamBlockFunction):
        bpy.app.timers.unregister(scannedParamBlockFunction)