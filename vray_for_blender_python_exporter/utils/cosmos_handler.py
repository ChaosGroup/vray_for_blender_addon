# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from enum import Enum
import bpy, os, time, queue

from vray_blender import debug
from vray_blender.exporting.tools import isObjectVrayProxy, isObjectVRayDecal
from vray_blender.lib.lib_utils import getLightPropGroup
from vray_blender.lib.blender_utils import selectObject
from vray_blender.lib.image_utils import untrackImage
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes.operators.import_file import  importDecal, importHDRI, importMaterials, importProxyFromMeshFile
from vray_blender.nodes.utils import getNodeByType, treeHasNodes

from vray_blender.bin import VRayBlenderLib as vray

# Cosmos relinking states, keep in sync with the enum in zmq_messages.hpp.
class CosmosRelinkStatus(Enum):
    CheckingIntegrity  = -1 # Python only state to block while checking integrity in c++.
    AllAssetsValid = 0 # All assets are valid and properly linked. In theory we should never end up getting it.
    NotLoggedIn = 1 # The user is not logged in so relinking is not at all possible.
    RelinkOnly = 2 # Only relinking i.e. chaning paths on some assets.
    DownloadAndRelink = 3 # Download the missing assets and then relink them.
    Aborted = 4 # Only used if something goes wrong with the zmq server.

# Cosmos download states, keep in sync with the enum in zmq_messages.hpp.
class CosmosDownloadStatus(Enum):
    Downloading = -1  # Python only state to block while downloading assets.
    Cancelled = 0 # The download was cancelled in the python process.
    Timeout = 1 # The download of an asset timed out and was cancelled.
    Done = 2 # Download and relinking was successful.
    Aborted = 3 # Only used if something goes wrong with the zmq server.

# Enum used to select which browser page to open. Keep in sync with CosmosBrowserPage in cosmos_importer.cpp.
class CosmosBrowserPage(Enum):
    HomePage = 0
    AIGenerator = 1

"""
When relinking is started first all materials, proxies and (IES/Dome)lights in the scene are checked. If any
of them have missing assets they are added to a list and sent back to the Zmq server. Blender UI is locked
and we make a request to cosmos to check the integrity of the assets and also return back the calculated asset
download size. The list of assets is stored in the server and it will wait for a download missing request. When
the request is sent and a download is necessary Blender will also be locked and wait until the download is
completed. BlenderCosmosImporter::downloadMissingAssets(...) will send back the newly relinked asset paths which
are then directly changed in the materials and objects.
"""

class VRAY_OT_dummy(VRayOperatorBase):
    bl_idname = "vray.dummy_operator"
    bl_label = "Does Nothing"

    def execute(self, context):
        return {'FINISHED'}

class VRAY_OT_show_cosmos_info_popup(VRayOperatorBase):
    bl_idname       = "vray.cosmos_info_popup"
    bl_label        = "Chaos Cosmos"
    bl_description  = "Chaos Cosmos Info Message"

    message: bpy.props.StringProperty()
    messageRow2: bpy.props.StringProperty()

    def execute(self, context):
        return { 'FINISHED' }

    def draw(self, context):
        layout = self.layout
        layout.label(text=self.message, icon='ERROR')

        # Splitting the message with \n does not work for labels since this operator behaves
        # a bit different than invoke_confirm(...)
        if self.messageRow2:
            layout.label(text=self.messageRow2)
        layout.template_popup_confirm("vray.dummy_operator", cancel_text="")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)

class CosmosHandler:
    def __init__(self):
        self.relinked = False
        self.unresolvedCallbacks = []
        self.missingAssetsStatus: CosmosRelinkStatus = CosmosRelinkStatus.AllAssetsValid
        self.downloadStatus: CosmosDownloadStatus = CosmosDownloadStatus.Done
        self.downloadSizeMB = 0
        self.relinkedAssets = []

    def _setCosmosRelinkState(self, status, downloadSize):
        self.missingAssetsStatus = status
        self.downloadSizeMB = downloadSize

    def _setCosmosDownloadedAssets(self, downloadStatus, relinkedAssets):
        self.downloadStatus = downloadStatus
        self.relinkedAssets = relinkedAssets

    def _getPathIfAssetMissing(self, path):
        resolvedPath = bpy.path.abspath(path)
        return resolvedPath if not os.path.exists(resolvedPath) else None

    def downloadMissingAssets(self, operator: bpy.types.Operator, context: bpy.types.Context):
        if not self.relinked:
            return { 'FINISHED' }
        self.downloadStatus = CosmosDownloadStatus.Downloading

        vray.downloadMissingAssets()
        # Just block blender until download is completed. There's a qt dialog where progress
        # is shown along with a way to cancel the download.
        while self.downloadStatus==CosmosDownloadStatus.Downloading:
            time.sleep(0.1)

        if self.downloadStatus == CosmosDownloadStatus.Cancelled:
            return { 'CANCELLED' }

        if self.downloadStatus == CosmosDownloadStatus.Aborted:
            return bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message="Download has failed. No assets have been relinked.")

        if self.downloadStatus == CosmosDownloadStatus.Timeout:
            return bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message="Asset relinking has timed out.")

        for i, assetPath in enumerate(self.relinkedAssets):
            # If download was aborted paths for unavailable assets will remain empty
            if assetPath:
                self.unresolvedCallbacks[i](assetPath)

        debug.printInfo(f'{len(self.unresolvedCallbacks)} assets have been successfully relinked')
        return { 'FINISHED' }

    def abortDownload(self):
        self.downloadStatus = CosmosDownloadStatus.Aborted
        self.missingAssetsStatus = CosmosRelinkStatus.Aborted

    def checkMissingAssets(self, operator: bpy.types.Operator, context: bpy.types.Context, event: bpy.types.Event):
        self.unresolvedCallbacks = []
        self.missingAssetsStatus = CosmosRelinkStatus.CheckingIntegrity

        unresolvedPackageIds, unresolvedRevisionIds, unresolvedPaths = [], [], []

        def checkNodeTreeAssets(dataObject, ntree: bpy.types.NodeTree):
            # Register unresolved callbacks for the nodes in a  node tree. The object passed in
            # should have valid cosmos_package_id and cosmos_revision_id properties.
            for node in ntree.nodes:
                if (texture := getattr(node, 'texture', None)):
                    if unresolvedPath := self._getPathIfAssetMissing(texture.image.filepath):
                        unresolvedPackageIds.append(dataObject.vray.cosmos_package_id)
                        unresolvedRevisionIds.append(dataObject.vray.cosmos_revision_id)
                        unresolvedPaths.append(unresolvedPath)
                        self.unresolvedCallbacks.append(lambda x, img=texture.image: (setattr(img, 'filepath', x), untrackImage(img)))
                elif (brdfScanned := getattr(node, 'BRDFScanned', None)):
                    if unresolvedPath := self._getPathIfAssetMissing(brdfScanned.file):
                        unresolvedPackageIds.append(dataObject.vray.cosmos_package_id)
                        unresolvedRevisionIds.append(dataObject.vray.cosmos_revision_id)
                        unresolvedPaths.append(unresolvedPath)
                        self.unresolvedCallbacks.append(lambda x, brdf=brdfScanned: setattr(brdf, 'file', x))

        for material in bpy.data.materials:
            if not hasattr(material, 'vray') or not material.node_tree or not material.use_nodes:
                continue
            if not material.vray.cosmos_package_id:
                continue
            checkNodeTreeAssets(material, material.node_tree)

        for obj in bpy.data.objects:
            if obj.type != 'MESH':
                continue
            mesh = obj.data
            if not hasattr(mesh, 'vray') or not mesh.vray.cosmos_package_id:
                continue
            if (isObjectVrayProxy(obj) and (vrayProxy := mesh.vray.GeomMeshFile)):
                if unresolvedPath := self._getPathIfAssetMissing(vrayProxy.file):
                    unresolvedPackageIds.append(mesh.vray.cosmos_package_id)
                    unresolvedRevisionIds.append(mesh.vray.cosmos_revision_id)
                    unresolvedPaths.append(unresolvedPath)
                    self.unresolvedCallbacks.append(lambda x, proxyObj=obj, proxy=vrayProxy: (selectObject(proxyObj), setattr(proxy, 'file', x)))
            elif isObjectVRayDecal(obj) and treeHasNodes(obj.vray.ntree) and (getNodeByType(obj.vray.ntree, 'VRayNodeDecalOutput') is not None):
                checkNodeTreeAssets(obj.data, obj.vray.ntree)

        for light in bpy.data.lights:
            if not hasattr(light, 'vray') or not light.use_nodes or not light.node_tree or not light.vray.cosmos_package_id:
                continue
            if light.vray.light_type=='IES':
                propGroup = getLightPropGroup(light, 'LightIES')
                if unresolvedPath := self._getPathIfAssetMissing(propGroup.ies_file):
                    unresolvedPackageIds.append(light.vray.cosmos_package_id)
                    unresolvedRevisionIds.append(light.vray.cosmos_revision_id)
                    unresolvedPaths.append(unresolvedPath)
                    self.unresolvedCallbacks.append(lambda x, ies=propGroup: setattr(ies, 'ies_file', x))
            if light.vray.light_type=='DOME':
                checkNodeTreeAssets(light, light.node_tree)

        if len(unresolvedPackageIds) == 0:
            return bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message='There are no Cosmos assets that require relinking.',
                messageRow2='Only assets imported after hotfix 7.00.24 can be relinked.')

        vray.calculateDownloadSize(unresolvedPackageIds, unresolvedRevisionIds, unresolvedPaths)

        timeout = 0
        MAX_DOWNLOAD_SIZE_TIMEOUT = 30 # Hard timeout in case something goes wrong with the zmq server.
        # Block and wait until the total asset size is calculated... Should not be slower than a 2-3s
        while self.missingAssetsStatus == CosmosRelinkStatus.CheckingIntegrity:
            time.sleep(0.1)
            timeout += 0.1

            if timeout > MAX_DOWNLOAD_SIZE_TIMEOUT:
                return bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message='Cosmos asset integrity check has timed out')

        self.relinked = False
        # The user is not logged in or the cosmos service is not running.
        if self.missingAssetsStatus == CosmosRelinkStatus.NotLoggedIn:
            return bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message="Please open Cosmos and log in first before relinking Cosmos assets.")

        if self.missingAssetsStatus == CosmosRelinkStatus.Aborted:
            return bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message="Checking for asset integrity has failed. No assets have been relinked.")

        if self.missingAssetsStatus == CosmosRelinkStatus.AllAssetsValid:
            return bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message="There are no Cosmos assets that require relinking. \
                Only assets imported after hotfix 7.00.24 can be relinked.")

        self.relinked = True
        if self.missingAssetsStatus == CosmosRelinkStatus.RelinkOnly:
            return context.window_manager.invoke_confirm(
                operator, event, title="", icon='WARNING',
                message=f"Would you like to relink your Cosmos assets?"
            )
        else:
            return context.window_manager.invoke_confirm(
                operator, event, title="", icon='WARNING',
                message=f'The scene has {self.downloadSizeMB}MB of missing cosmos assets.\nWould you like to download them and recover the asset file references?'
            )

cosmosHandler = CosmosHandler()

# Modifying bpy data structures from another thread could crash Blender.
# This means that vrmat and vrmesh file importing can be done only from the main thread.
# For that reason 'assetImportTimerFunction' is registered as timer and waits until asset import data is
# added to the 'assetImportQueue' from 'assetImportCallback' which is safe for execution from another thread
# This is the recommended by the  blender community way for dealing with this problem:
# https://docs.blender.org/api/current/bpy.app.timers.html#use-a-timer-to-react-to-events-in-another-thread
assetImportQueue = queue.Queue()

# Scale applied when importing Cosmos Assets
COSMOS_SCALE_UNIT = 0.01 # centimeters

def assetImportTimerFunction():
    global assetImportQueue

    try:
        while not assetImportQueue.empty():
            settings = assetImportQueue.get()
            match(settings.assetType):
                case "Material":
                    if not os.path.exists(settings.matFile):
                        debug.printError(f"VRmat file {settings.matFile} does not exist")
                        continue
                    importMaterials(settings.matFile, settings.packageId, settings.revisionId, locationsMap=settings.locationsMap)

                case "VRMesh":
                    ob, err = importProxyFromMeshFile(bpy.context,
                                            settings.matFile, settings.objFile, lightPath=settings.lightFile,
                                            packageId=settings.packageId, revisionId=settings.revisionId,
                                            locationsMap=settings.locationsMap,
                                            scaleUnit=COSMOS_SCALE_UNIT)
                    if err:
                        debug.printError(err)

                    # Animated cosmos models need the attribute below
                    # to function properly when the scene containing them is exported and rendered through Vantage.
                    if ob and settings.isAnimated:
                        userAttrs = ob.vray.UserAttributes
                        userAttrs.user_attributes.add()

                        animAttr = userAttrs.user_attributes[-1]
                        animAttr.name = "lavina_fast_morph_mesh"
                        animAttr.value_type = "0"
                        animAttr.value_int = 2

                case "HDRI":
                    importHDRI(settings.matFile, settings.lightFile, settings.packageId, settings.revisionId, locationsMap=settings.locationsMap)

                case "Extras":
                    importDecal(settings)

            bpy.ops.ed.undo_push(message="Import Cosmos " + settings.assetType)
    except Exception as e:
        debug.printExceptionInfo(e, "cosmos_handler.assetImportTimerFunction")
        debug.reportError("Import of Cosmos asset failed")

    return 1.0 # Timeout before the next invocation

def assetImportCallback(assetSettings):
    global assetImportQueue
    assetImportQueue.put(assetSettings)

def registerAssetImportTimerFunction():
    if not bpy.app.timers.is_registered(assetImportTimerFunction):
        bpy.app.timers.register(assetImportTimerFunction)

def register():
    registerAssetImportTimerFunction()


def unregister():
    if bpy.app.timers.is_registered(assetImportTimerFunction):
        bpy.app.timers.unregister(assetImportTimerFunction)