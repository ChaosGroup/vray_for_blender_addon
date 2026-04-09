# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


__all__ = []


def getRegModules():
    from vray_blender.utils import cosmos_handler, utils_bake, fur_preview, update_checker
    return (
        cosmos_handler,
        utils_bake,
        fur_preview,
        update_checker
    )


def register():
    for regModule in getRegModules():
        regModule.register()


def unregister():
    for regModule in reversed(getRegModules()):
        regModule.unregister()

