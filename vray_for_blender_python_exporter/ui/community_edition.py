# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.ui.icons import getIcon
from vray_blender.bin import VRayBlenderLib as vray


def getCELimitedFeatureMsg():
    """ Return a standard message to display when a feature
        is disabled in the Community Edition of the product.
    """
    return "This feature is not supported in V-Ray for Blender - Community Edition"

def getLimitedFeatureDescription(defaultDescription: str):
    """ Return a standard description of a limited feature """
    if vray.isCommunityEdition():
        return getCELimitedFeatureMsg() + ".\n\n" + defaultDescription
    return defaultDescription

def drawCELimitedFeatureWarning(layout: bpy.types.UILayout):
    layout.label(text="This feature requires the fully featured version of V-Ray.")
    layout.label(text="Please upgrade your plan to access this functionality.")
    op = layout.template_popup_confirm("vray.url_open", text="Upgrade", icon='URL')
    addCEPricingURLInfo(op)

def addCEPricingURLInfo(operator: bpy.types.Operator):
    """ Add the URL and description for the V-Ray for Blender pricing page to an vray.url_open operator. """
    operator.url = 'https://www.chaos.com/vray/blender#pricing'
    operator.description = 'Go to the V-Ray for Blender page.'


def drawCELimitedFeatureIcon(layout: bpy.types.UILayout):
    """ Draw a tooltip icon for a limited feature in the Community Edition. """
    layout.operator('vray.ce_limited_feature_tooltip', text='', icon_value=getIcon('INFO_ABOUT'), emboss=False)