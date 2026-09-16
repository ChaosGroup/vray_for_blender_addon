# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from chaos_scatter.backend import ScatterComputeBackend


class NullBackend(ScatterComputeBackend):
    """ Used when no compute provider is available. Scatter objects stay fully editable and
        any previously baked points keep rendering; only the preview recompute is disabled.
    """

    def __init__(self, reason=""):
        self._reason = reason or "V-Ray binaries not found - scatter preview is disabled"

    def isAvailable(self) -> bool:
        return False

    def unavailableReason(self) -> str:
        return self._reason

    def submit(self, request, onFinished) -> int:
        raise RuntimeError("NullBackend cannot compute scatter previews")

    def readPreset(self, filePath: str, unitRescale: float):
        raise RuntimeError(f"Cannot read a Chaos Scatter preset: {self._reason}")
