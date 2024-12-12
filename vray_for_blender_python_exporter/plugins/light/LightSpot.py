from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import PluginDesc, ExporterContext
from vray_blender.plugins.light.light_tools import onUpdateColor
from vray_blender.nodes.utils import getUpdateCallbackPropertyContext
from vray_blender.exporting.light_export import ANGLE_EPSILON

plugin_utils.loadPluginOnModule(globals(), __name__)

def onUpdateAttribute(src, context, attrName):

    if (lamp := context.active_object) and ((lamp.type != 'LIGHT') or (lamp.data.type != 'SPOT')):
        return
    
    if attrName in ('color', 'temperature'):
        onUpdateColor(src, 'LightSpot', attrName)
        return
    
    propContext = getUpdateCallbackPropertyContext(src, 'LightSpot')
    
    # This function will be called when the user changes the vaues of the light properties directly
    # in the property pages or through a script, and when the change occurs due to a manipulation of
    # the light gizmo or through the native Blender light properties. In the latter case, an infinite 
    # recursion may occur with the fixBlenderLights() function in light_export.py if care is not taken 
    # to only set the properties when their values have changed. 
    match attrName:
        case 'coneAngle':
            newConeAngle = propContext.get('coneAngle')
            if abs(newConeAngle -lamp.data.spot_size) > ANGLE_EPSILON:
                lamp.data.spot_size = newConeAngle
                lamp.data.spot_blend = max(-propContext.get('penumbraAngle') / newConeAngle, 0)

        case 'penumbraAngle':
            newPenumbraAngle = -propContext.get('penumbraAngle')
            coneAngle = propContext.get('coneAngle')
            newBlend = max(newPenumbraAngle / coneAngle, 0)
            if abs(newBlend - lamp.data.spot_blend) > ANGLE_EPSILON:
                lamp.data.spot_blend = newBlend

        case 'show_cone':
            newShow = propContext.get('show_cone')
            if newShow != lamp.data.show_cone:
                lamp.data.show_cone = newShow


