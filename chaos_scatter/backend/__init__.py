# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Scatter preview compute backends.

    The addon talks to a ScatterComputeBackend, never to ZMQ or V-Ray directly. The V-Ray
    backend (vray_backend.py) is used when the V-Ray package is installed; tests may inject
    their own via setBackendOverride().
"""


class ScatterResult:
    """ Plain result data. transforms is (N, 12) float32, topo is (N,) int32 (model index). """
    STATUS_OK = 0
    STATUS_ERROR = 1
    STATUS_CANCELLED = 2

    def __init__(self, status=STATUS_OK, errorText="", transforms=None, topo=None, countLimitHit=False):
        self.status = status
        self.errorText = errorText
        self.transforms = transforms
        self.topo = topo
        self.countLimitHit = countLimitHit


class ScatterComputeBackend:
    """ Backend protocol. onFinished(requestId, ScatterResult) may be invoked on ANY thread. """

    def isAvailable(self) -> bool:
        raise NotImplementedError

    def isSessionReady(self) -> bool:
        """ True when a compute session is live RIGHT NOW, without starting the server or waiting
            on it. Callers that must not block (scene load) test this before submitting; submit()
            itself is free to start the server and wait for it.
        """
        return self.isAvailable()

    def unavailableReason(self) -> str:
        return ""

    def submit(self, request: dict, onFinished) -> int:
        """ Returns a monotonically increasing requestId. """
        raise NotImplementedError

    def readPreset(self, filePath: str, unitRescale: float):
        """ Read a Chaos Scatter preset config (.mbc) and return (plugins, modelAssetIds).

            plugins is [(pluginName, pluginType, attrs), ...] - the filled GeomScatter first, then
            one placeholder Node per model the preset references, in link order; modelAssetIds is
            parallel to those Nodes. Blocks until the read completes; raises on failure.
        """
        raise NotImplementedError

    def drainCompleted(self):
        """ Main thread: marshal results that finished on another thread to their owners.

            Normally driven by a timer. A caller that cannot wait for one (a render freezes
            bpy.app.timers) pumps this itself.
        """
        pass

    def cancel(self, requestId: int):
        pass

    def shutdown(self):
        """ Release any live compute session/callbacks (addon disable). Safe to call always. """
        pass


class SessionNotReady(RuntimeError):
    """ The compute session could not be opened yet - the ZMQ server is still starting. """


_backendOverride = None
_backend = None


def setBackendOverride(backend):
    """ Test hook: force a specific backend instance (None restores auto-selection). """
    global _backendOverride, _backend
    _backendOverride = backend
    _backend = None


def getBackend() -> ScatterComputeBackend:
    global _backend
    if _backendOverride is not None:
        return _backendOverride
    if _backend is None:
        from chaos_scatter.backend.vray_backend import VRayScatterBackend
        from chaos_scatter.backend.null_backend import NullBackend
        vrayBackend = VRayScatterBackend()
        _backend = vrayBackend if vrayBackend.isAvailable() else NullBackend(vrayBackend.unavailableReason())
    return _backend


def resetBackend():
    """ Drop the cached backend so availability is re-evaluated (e.g. after a server crash). """
    global _backend
    _backend = None


def shutdownBackend():
    """ Tear down and forget the cached backend (addon disable). Never instantiates one. """
    global _backend
    if _backend is not None:
        _backend.shutdown()
        _backend = None
