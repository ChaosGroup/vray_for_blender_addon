
import os

from xml.etree import ElementTree
from vray_blender import debug


def _convertToXML(filePath):
    import shutil
    import subprocess

    vrmatconvert = os.path.join(os.path.dirname(__file__), "..", "bin", "vrmatconvert.exe")

    bakFile = f"{filePath}.bak"

    shutil.copyfile(filePath, bakFile)

    subprocess.call([vrmatconvert, bakFile, filePath])

def _parseTree(filePath):
    tree = None
    try:
        tree = ElementTree.parse(filePath)
    except:
        pass
    return tree

def _parserConvertTree(filePath):
    if not os.path.exists(filePath):
        return None

    tree = _parseTree(filePath)
    if tree is None:
        _convertToXML(filePath)
        tree = _parseTree(filePath)

    return tree

def getMaterialNamesFromVRMatFile(filePath):
    tree = _parserConvertTree(filePath)
    if tree is None:
        debug.printError("Failed to parse VRmat file!")
        return []

    vrmat = tree.getroot()

    materialPluginNames = []

    for asset in vrmat.iter('Asset'):
        assetType = asset.attrib['type']
        if assetType not in {'material'}:
            continue

        url = asset.attrib['url']
        if url.startswith("/"):
            url = url[1:]

        materialPluginNames.append(url)

    return materialPluginNames

def parseVrmat(filePath):
    from numpy import allclose, array
    from mathutils import Matrix

    def _getColorValue(rawValue):
        return (
            float(rawValue.find('r').text),
            float(rawValue.find('g').text),
            float(rawValue.find('b').text),
        )

    def _getVectorValue(rawValue):
        return (
            float(rawValue.find('x').text),
            float(rawValue.find('y').text),
            float(rawValue.find('z').text),
        )

    def _getTransformValue(rawValue):
        v3 = rawValue[3]

        return (  _getMatrixValue(rawValue),
                  (float(v3[0].text), float(v3[1].text), float(v3[2].text)))


    def _getMatrixValue(rawValue):
        v0 = rawValue[0]
        v1 = rawValue[1]
        v2 = rawValue[2]

        return ( (float(v0[0].text), float(v0[1].text), float(v0[2].text)),
                 (float(v1[0].text), float(v1[1].text), float(v1[2].text)),
                 (float(v2[0].text), float(v2[1].text), float(v2[2].text)))

    def _isNumeric(text):
        value = text.replace('.', '', 1)
        return value.isdigit() or (text[0] == '-' and value[1:].isdigit())

    from pathlib import Path

    sceneDesc = []

    tree = _parserConvertTree(filePath)
    if tree is None:
        debug.printError(f"Failed to parse VRmat file: {filePath}")
        return {}

    vrmat = tree.getroot()

    for asset in vrmat.iter('Asset'):
        assetType = asset.attrib['type']

        vrayPluginName = asset.attrib['url']

        for vrayplugin in asset.iter('vrayplugin'):
            vrayPluginID = vrayplugin.attrib['name']

            vrayPluginAttributes = {}

            for parameter in vrayplugin.iter('parameter'):
                attrName  = parameter.attrib['name']
                attrType  = parameter.attrib['type']
                attrValue = None

                # print("Found attribute: %s [%s]" % (attrName, attrType))

                if (rawValue := parameter.find('value')) is None:
                    continue

                match attrType:
                    case 'integer':
                        attrValue = int(rawValue.text)

                    case 'float':
                        attrValue = float(rawValue.text)

                    case 'bool':
                        attrValue = (rawValue.text != '0')

                    case 'color' | 'acolor':
                        if rawValue.find('r') is not None:
                            attrValue = _getColorValue(rawValue)

                    case 'vector':
                        # Most likely a pivot offset
                        if rawValue.find('x') is not None:
                            attrValue = _getVectorValue(rawValue)

                    case 'float texture':
                        if rawValue.text:
                            if _isNumeric(rawValue.text):
                                attrValue = float(rawValue.text)
                            else:
                                attrValue = rawValue.text

                    case 'acolor texture':
                        if rawValue.text:
                            if rawValue.find('r') is None:
                                attrValue = rawValue.text
                            else:
                                attrValue = _getColorValue(rawValue)

                    case 'int texture':
                        if rawValue.text:
                            if _isNumeric(rawValue.text):
                                attrValue = int(rawValue.text)
                            else:
                                attrValue = rawValue.text

                    case 'plugin' | 'string':
                        attrValue = rawValue.text

                    case 'list':
                        attrValue = [v.text for v in rawValue.find('list').iter('entry')]

                    case 'transform':
                        attrValue = _getTransformValue(rawValue)

                    case 'matrix':
                        # From the plugins that have properties of type 'Matrix' we only suppport UVWGenEnvironment
                        # However, the V-Ray UVW Mapping => Environment node does not expose the 'uvw_matrix'
                        # socket. If the matrix is not identity, log an error.
                        matrixValue = array(_getMatrixValue(rawValue))
                        if not allclose(Matrix.Identity(3), matrixValue):
                            assert vrayPluginID == 'UVWGenEnvironment', f"Parameter of type 'Matrix' in an unsupported plugin: {vrayPluginID}"
                            assetName = Path(filePath).stem
                            debug.printError(f"The Cosmos asset being imported ({assetName}) has a UVWGenEnvironment node with its 'uvw_matrix' property" + \
                                            " not set to identity. This property is currently unsipported. Please contact the V-Ray for Blender developers.")

                        continue

                    case _:
                        debug.printWarning(f"Unsupported data type: {attrName}, {attrType}")

                if attrValue is not None:
                    vrayPluginAttributes[attrName] = attrValue

            sceneDesc.append({
                "ID"         : vrayPluginID,
                "Name"       : vrayPluginName,
                "Attributes" : vrayPluginAttributes,
            })

    sceneDesc.append({
        "ID" : 'ImportSettings',
        "Name" : "Import Settings",
        "Attributes" : {
            'filepath' : filePath,
            'dirpath'  : os.path.dirname(filePath),
        },
    })

    return sceneDesc


if __name__ == '__main__':
    import argparse
    from pprint import pprint

    parser = argparse.ArgumentParser()
    parser.add_argument('filepath', nargs='?')
    args = parser.parse_args()

    vrsceneDict = parseVrmat(args.filepath)

    for pluginDesc in vrsceneDict:
        print("Name:", pluginDesc['Name'])
        print("ID:  ", pluginDesc['ID'])
        print("Attributes:")
        pprint(pluginDesc['Attributes'], indent=4)
