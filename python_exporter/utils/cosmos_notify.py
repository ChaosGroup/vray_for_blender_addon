# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Messages shown in the Chaos Cosmos Browser.
#
# The Blender-side part of a Cosmos import runs here, in the addon, long after the server
# has handed the asset over. Errors found along the way used to reach the V-Ray console
# only, which is not where the user is looking - they are still in the browser they just
# dragged the asset out of. The messages are sent to the server, which collects them
# together with its own and posts them through the Cosmos notifications API.
#
# Errors, warnings and successes are the ones the browser shows. Info and debug never
# appear there: both arrive as a debug notification, which is only written to the Cosmos
# service log (cb.log). They are the same message on that side - the level picks the
# V-Ray console severity and nothing else.

from vray_blender import debug as vrayDebug
from vray_blender.bin import VRayBlenderLib as vray


class CosmosLevel:
    """ Log levels understood by the Cosmos notifications API.

        NOTE: not the same enum as debug.LogLevel. The two agree up to Warning and differ
        after it - Success has no counterpart there, and shifts Info and Debug by one - so
        the overlap makes a mix-up look right for errors and mislabel everything else.
        Only pass these to the functions in this module.
    """
    Critical = 0
    Error    = 1
    Warning  = 2
    Success  = 3
    Info     = 4
    Debug    = 5


# The addon's Python is used straight from the checkout while the native library is
# whatever was last built, so a newer addon may be running against a library that does
# not export the function yet. Same check as the one importCosmosAssets() makes.
_HAS_NATIVE = hasattr(vray, "cosmosNotify")


def post(level: int, msg: str):
    """ Send a message to the Cosmos notifier without logging it.

    Args:
        level (CosmosLevel): The severity of the message. Decides whether it is shown
            in the browser or only written to the Cosmos log.
        msg (str): The message to send.
    """
    if _HAS_NATIVE:
        vray.cosmosNotify(level, msg)


def error(msg: str):
    """ Log an error and show it in the Cosmos Browser. """
    vrayDebug.printError(msg)
    post(CosmosLevel.Error, msg)


def warning(msg: str):
    """ Log a warning and show it in the Cosmos Browser. """
    vrayDebug.printWarning(msg)
    post(CosmosLevel.Warning, msg)


def success(msg: str):
    """ Log a message and show it in the Cosmos Browser as a success.

        Logged at info level - the V-Ray console has no notion of a success message.
    """
    vrayDebug.printInfo(msg)
    post(CosmosLevel.Success, msg)


def info(msg: str):
    """ Log a message and add it to the Cosmos log.

        Not shown in the browser - see the note at the top of this module. Use it for
        the few steps worth having in a user's Cosmos log, not for per-asset trace.
    """
    vrayDebug.printInfo(msg)
    post(CosmosLevel.Info, msg)


def debug(msg: str):
    """ Log a debug message and add it to the Cosmos log.

        Same destination as info() - only the V-Ray console severity differs.
    """
    vrayDebug.printDebug(msg)
    post(CosmosLevel.Debug, msg)


def reportError(msg: str, exc: Exception = None):
    """ Show an error in Blender's UI and in the Cosmos Browser, and log it.

    Args:
        msg (str): The message to show.
        exc (Exception, optional): Exception whose info to add to the log.
    """
    vrayDebug.reportError(msg, exc=exc)
    post(CosmosLevel.Error, msg)
