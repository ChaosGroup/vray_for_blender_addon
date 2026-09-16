# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Leaf helpers shared by all import paths (server-side .vrscene import, cosmos .vrmat
    import, material import). No bpy dependencies - importable from both nodes/ and
    vray_tools/ without cycles.
"""

import os


def refName(ref):
    """ Strip the output selector from a plugin reference ("name::out" -> "name"). """
    return ref.partition("::")[0] if isinstance(ref, str) else ref


def sceneName(attrs: dict, fallback: str) -> str:
    """ The original host-app name of a plugin (scene_name), used to name the imported
        datablock instead of the mangled V-Ray plugin name. scene_name is a STRING_LIST
        for geometry/material/light plugins and a STRING for RenderView. """
    sn = attrs.get('scene_name')
    if isinstance(sn, (list, tuple)) and len(sn) and sn[0]:
        return sn[0]
    if isinstance(sn, str) and sn:
        return sn
    return fallback


# Material plugin types accepted as importable roots (in addition to wrappers and BRDFs).
MATERIAL_ROOT_TYPES = {
    'MtlSingleBRDF',
    'MtlVRmat',
    'MtlDoubleSided',
    'MtlGLSL',
    'MtlLayeredBRDF',
    'MtlDiffuse',
    'MtlBump',
    'Mtl2Sided',
}


# Material wrapper plugins mapped to the attribute holding the wrapped material.
WRAPPER_BASE_ATTRS = {
    'MtlRoundEdges': 'base_mtl',
    'MtlMaterialID': 'base_mtl',
    'MtlRenderStats': 'base_mtl',
    'MtlOverride': 'base_mtl',
    'MtlWrapper': 'base_material',
    'MtlUVWSelect': 'base_mtl',
    # No node of their own (both are excluded in plugins/__init__.py); peeled like MtlUVWSelect.
    'MtlSelectRE': 'base_mtl',
    'MtlWrapperMaya': 'base_material',
}


def isScaledDistanceAttr(attrDesc) -> bool:
    """ True for distance-typed attrs that participate in import unit conversion:
        quantityType 'distance' without options.skipUnitScale (the marker for values
        V-Ray self-normalizes by scene units, e.g. bump_amount). """
    if not attrDesc:
        return False
    return attrDesc.get('ui', {}).get('quantityType') == 'distance' \
        and not attrDesc.get('options', {}).get('skipUnitScale')


def getImportDir(vrsceneDict) -> str:
    """ Directory of the source scene file, from the synthetic 'Import Settings' record.
        Handles both storage conventions: top-level 'Dirpath' (vrmat parser) and
        Attributes['dirpath'] (vrscene parser; the server import provides both). """
    if importSettings := getPluginByName(vrsceneDict, "Import Settings"):
        return importSettings.get('Dirpath') \
            or importSettings.get('Attributes', {}).get('dirpath', "")
    return ""


def findAssetUnderDir(path: str, baseDir: str):
    """ Locate an asset a .vrscene references by an absolute path from another machine.

        Tries progressively shorter tails of the path under baseDir (longest first), so a
        texture in a subfolder like '<scene>/tex/foo.jpg' is found even when only the drive/
        user prefix differs. Returns an existing path or None. """
    if not path or not baseDir:
        return None
    parts = [p for p in path.replace('\\', '/').split('/') if p and p not in ('.', '..')]
    for i in range(len(parts)):
        cand = os.path.join(baseDir, *parts[i:])
        if os.path.isfile(cand):
            return cand
    return None


def resolveAssetPath(path: str, sceneBaseDir: str, locationsMap: dict = None) -> str:
    """ Resolve a file path referenced by the source scene: an exact locationsMap entry
        first (cosmos assets), then a relative reference against the scene directory, then
        the path as stored, and last the progressively-shorter-tail search under the scene
        directory. Returns the input path when nothing resolves. """
    if locationsMap and path in locationsMap:
        return locationsMap[path]

    if not path:
        return path

    # Separators are unified for the candidates only. A path that resolves to nothing is
    # returned exactly as the scene stored it.
    unified = path.replace('\\', '/')

    # A relative reference is relative to the .vrscene, not to Blender's working directory,
    # and may point outside the scene folder ('../../assets/maps/t.png').
    if sceneBaseDir and not os.path.isabs(unified):
        candidate = os.path.normpath(os.path.join(sceneBaseDir, unified))
        if os.path.isfile(candidate):
            return candidate

    if os.path.isfile(path):
        return path

    if sceneBaseDir and (found := findAssetUnderDir(unified, sceneBaseDir)):
        return found

    return path


class IndexedVrsceneDict(list):
    """ A vrscene dict (list of plugin descriptions) carrying name and type indexes that
        make getPluginByName/getPluginByType O(1).

        Only append() keeps both indexes current. Mutating the list any other way leaves
        the indexes stale.
    """
    def __init__(self, plugins):
        super().__init__(plugins)
        self.pluginIndex = {p['Name']: p for p in plugins}
        self.typeIndex: dict[str, list] = {}
        for p in plugins:
            self.typeIndex.setdefault(p['ID'], []).append(p)

    def append(self, pluginDesc):
        super().append(pluginDesc)
        self.pluginIndex[pluginDesc['Name']] = pluginDesc
        self.typeIndex.setdefault(pluginDesc['ID'], []).append(pluginDesc)


def getPluginByName(vrsceneDict, pluginName):
    index = getattr(vrsceneDict, 'pluginIndex', None)
    if index is not None:
        if (pluginDesc := index.get(pluginName)) is not None:
            return pluginDesc

    for pluginDesc in vrsceneDict:
        if pluginDesc['Name'] == pluginName:
            if index is not None:
                # Route the mutation that added this plugin through append().
                from vray_blender import debug
                debug.printWarning(f"IndexedVrsceneDict: stale name index for '{pluginName}'")
            return pluginDesc
    return None


def getPluginByType(vrsceneDict, pluginType):
    if (typeIndex := getattr(vrsceneDict, 'typeIndex', None)) is not None:
        if pluginDescs := typeIndex.get(pluginType):
            return pluginDescs[0]

    for pluginDesc in vrsceneDict:
        if pluginDesc['ID'] == pluginType:
            return pluginDesc
    return None
