# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Functionality related to the BRDF Toon Override (V-Ray Outlines) node.
Adds the per-material depth/angular line-width curve overrides, mirroring the
global VolumeVRayToon depth/angular scaling.

The plugin itself is exported by MtlExporter._exportBRDFToonOverrideForOutlines
(it is connected to the Material Output's 'Outlines' socket), which also emits
these curves via curves_node.exportLineWidthCurves."""

import bpy

from vray_blender.lib import plugin_utils
from vray_blender.nodes.utils import getNodeOfPropGroup
from vray_blender.nodes.curves_node import addCurvesUpdateCallback, copyCurvesData, createCurvesNode, getCurvesNode, hasCurvesNode, removeCurvesNode

plugin_utils.loadPluginOnModule(globals(), __name__)


_CURVE_ATTRIBUTES = {'depth_curve': 'depth', 'angular_curve': 'angular'}
_CURVE_TYPES = ('depth', 'angular')


def _initCurveDefaults(curvesNode: bpy.types.Node):
    """Default line-width curve: full width at the near end, tapering to zero."""
    curve = curvesNode.mapping.curves[3]
    curve.points[0].location[1] = 1.0
    curve.points[1].location[1] = 0.0


def nodeInit(node: bpy.types.Node):
    node.assignStaticId() # The static id is needed for the name of the curves node

    for curveType in _CURVE_TYPES:
        _initCurveDefaults(createCurvesNode(node, f'_{curveType}'))


def nodeFree(node: bpy.types.Node):
    for curveType in _CURVE_TYPES:
        if hasCurvesNode(node, f'_{curveType}'):
            removeCurvesNode(node, f'_{curveType}')


def nodeCopy(copyNode: bpy.types.Node, origNode: bpy.types.Node):
    copyNode.assignStaticId() # The static id is needed for the name of the curves node

    for curveType in _CURVE_TYPES:
        copyCurvesData(getCurvesNode(origNode, f'_{curveType}'), createCurvesNode(copyNode, f'_{curveType}'))


def _ensureCurvesNodes(node: bpy.types.Node):
    """Create the depth/angular curve nodes if they don't exist yet
    (e.g. for scenes saved before these overrides were added)."""
    for curveType in _CURVE_TYPES:
        if not hasCurvesNode(node, f'_{curveType}'):
            _initCurveDefaults(createCurvesNode(node, f'_{curveType}'))


def registerNodeCurves(node: bpy.types.Node):
    """Register update callbacks for a single BRDFToonOverride node."""
    _ensureCurvesNodes(node)
    for curveType in _CURVE_TYPES:
        addCurvesUpdateCallback(node, getCurvesNode(node, f'_{curveType}'))


def drawLineWidthCurveTemplate(context, layout, propGroup, widgetAttr):
    node = getNodeOfPropGroup(propGroup)

    curveType = _CURVE_ATTRIBUTES.get(widgetAttr['name'])
    curvesNode = getCurvesNode(node, f'_{curveType}')
    layout.template_curve_mapping(curvesNode, "mapping", type='NONE')
