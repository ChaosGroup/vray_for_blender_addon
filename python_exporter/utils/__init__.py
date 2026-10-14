# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


__all__ = []


def getRegModules():
    from vray_blender.utils import cosmos_handler, cosmos_variants, utils_bake, fur_preview, splat_preview, update_checker
    return (
        cosmos_handler,
        # Registers panels parented to VRAY_PT_VRayProxy and VRAY_PT_npanel. This package is
        # registered after ui/, which is where those parents come from.
        cosmos_variants,
        utils_bake,
        fur_preview,
        splat_preview,
        update_checker
    )


def register():
    for regModule in getRegModules():
        regModule.register()


def unregister():
    for regModule in reversed(getRegModules()):
        regModule.unregister()

