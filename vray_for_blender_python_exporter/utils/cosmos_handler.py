from enum import Enum
import bpy, os, time
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.lib.lib_utils import getLightPropGroup
from vray_blender import debug

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

"""
When relinking is started first all materials, proxies and (IES/Dome)lights in the scene are checked. If any
of them have missing assets they are added to a list and sent back to the Zmq server. Blender UI is locked
and we make a request to cosmos to check the integrity of the assets and also return back the calculated asset
download size. The list of assets is stored in the server and it will wait for a download missing request. When
the request is sent and a download is necessary Blender will also be locked and wait until the download is
completed. BlenderCosmosImporter::downloadMissingAssets(...) will send back the newly relinked asset paths which
are then directly changed in the materials and objects.
"""

class VRAY_OT_dummy(bpy.types.Operator):
    bl_idname = "vray.dummy_operator"
    bl_label = "Does Nothing"

    def execute(self, context):
        return {'FINISHED'}

class VRAY_OT_show_cosmos_info_popup(bpy.types.Operator):
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

        def checkNodeTreeBitmaps(object):
            for node in object.node_tree.nodes:
                if (texture := getattr(node, 'texture', None)):
                    if unresolvedPath := self._getPathIfAssetMissing(texture.image.filepath):
                        unresolvedPackageIds.append(object.vray.cosmos_package_id)
                        unresolvedRevisionIds.append(object.vray.cosmos_revision_id)
                        unresolvedPaths.append(unresolvedPath)
                        self.unresolvedCallbacks.append(lambda x, img=texture.image: setattr(img, 'filepath', x))

        for material in bpy.data.materials:
            if not hasattr(material, 'vray') or not material.node_tree or not material.use_nodes:
                continue
            if not material.vray.cosmos_package_id:
                continue
            checkNodeTreeBitmaps(material)

        for mesh in bpy.data.meshes:
            if not hasattr(mesh, 'vray') or not mesh.vray.cosmos_package_id:
                continue
            if (vrayProxy := mesh.vray.GeomMeshFile) and mesh.vray.cosmos_package_id:
                if unresolvedPath := self._getPathIfAssetMissing(vrayProxy.file):
                    unresolvedPackageIds.append(mesh.vray.cosmos_package_id)
                    unresolvedRevisionIds.append(mesh.vray.cosmos_revision_id)
                    unresolvedPaths.append(unresolvedPath)
                    self.unresolvedCallbacks.append(lambda x, proxy=vrayProxy: setattr(proxy, 'file', x))

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
                checkNodeTreeBitmaps(light)

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
