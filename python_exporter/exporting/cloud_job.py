# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy
import subprocess
import os
import pathlib
import shutil
import threading

from vray_blender.lib import blender_utils, path_utils
from vray_blender import debug


class VCloudJob:
    def __init__(self, scene, sceneFile):

        VRayScene = scene.vray
        VRayExporter = VRayScene.Exporter

        self.project = VRayExporter.vray_cloud_project_name
        self.name = VRayExporter.vray_cloud_job_name
        self.sceneFile = sceneFile


    def _getSubmitCmd(self):
        VRayPreferences = bpy.context.preferences.addons['vray_blender'].preferences

        cmd = [VRayPreferences.vray_cloud_binary, "--json", "job", "submit"]

        cmd.append("--gui")
        cmd.append("--project")
        cmd.append(self.project)

        cmd.append("--name")
        cmd.append(path_utils.PathExpander(bpy.context).expand(self.name))

        cmd.append("--sceneFile")
        cmd.append(self.sceneFile)

        return cmd


    def createProjectCmd(self):
        VRayPreferences = blender_utils.getVRayPreferences()

        return [VRayPreferences.vray_cloud_binary, "project", "create", "--name", self.project]


    def submitToCloud(self):
        """
        Submits this job to Chaos Cloud. Tries to create a project before submitting.
        """
        VRayPreferences = bpy.context.preferences.addons['vray_blender'].preferences
        if VRayPreferences.detect_vray_cloud:
            cmd = self.createProjectCmd()
            createProjectResult = subprocess.call(cmd, env=os.environ)

            if createProjectResult != 0:
                debug.report("ERROR","Chaos Cloud failed to create project")
                return

            cmd = self._getSubmitCmd()
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=os.environ
            )

            def runCloudSubmit(process):
                try:
                    stdout, stderr = process.communicate()
                    if process.returncode != 0:
                        bpy.app.timers.register(lambda: debug.report("ERROR", "Chaos Cloud failed to submit job, check the console" + stderr))
                finally:
                    # Owns cleanup of the temp scene dir on both success and failure paths.
                    shutil.rmtree(pathlib.Path(self.sceneFile).parent, ignore_errors=True)

            threading.Thread(target=runCloudSubmit, args=(process,), daemon=True).start()
        else:
            debug.report("ERROR", "Chaos Cloud binary is not detected on the system, done nothing")