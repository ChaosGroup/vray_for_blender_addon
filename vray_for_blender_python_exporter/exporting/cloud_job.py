
import bpy
import subprocess
import os
import json

from vray_blender.lib import lib_utils
from vray_blender import debug


def getLoopCameras(scene):
    cameras = []

    for ob in scene.objects:
        if ob.type == 'CAMERA':
            if ob.data.vray.use_camera_loop:
                cameras.append(ob)

    return cameras

def getFrameRange(scene):
    VRayExporter = scene.vray.Exporter
    
    if VRayExporter.animation_mode == 'FRAME':
        return ''

    frame_range = '{}-{}'
    frame_range =  frame_range.format(scene.frame_start, scene.frame_end)

    return frame_range


class VCloudJob:
    def __init__(self, scene, sceneFile):

        VRayScene = scene.vray
        VRayExporter = VRayScene.Exporter

        self.project = VRayExporter.vray_cloud_project_name
        self.name = VRayExporter.vray_cloud_job_name
        self.sceneFile = sceneFile
        
        if VRayScene.SettingsImageSampler.type == '3':
            self.renderMode = "progressive"
        else:
            self.renderMode = "bucket"

        self.width = int(scene.render.resolution_x * scene.render.resolution_percentage * 0.01)
        self.height = int(scene.render.resolution_y * scene.render.resolution_percentage * 0.01)

        self.animation = not VRayExporter.animation_mode == 'FRAME'
        if self.animation:
            self.frameRange = getFrameRange(scene)
            self.frameStep = scene.frame_step


    def submitCmd(self):
        VRayPreferences = bpy.context.preferences.addons['vray_blender'].preferences

        cmd = [VRayPreferences.vray_cloud_binary, "--json", "job", "submit"]

        cmd.append("--project")
        cmd.append(self.project)

        cmd.append("--name")
        cmd.append(lib_utils.formatName(self.name))

        cmd.append("--sceneFile")
        cmd.append(self.sceneFile)

        cmd.append("--renderMode")
        cmd.append(self.renderMode)
        
        cmd.append("--width")
        cmd.append(str(self.width))

        cmd.append("--height")
        cmd.append(str(self.height))

        if self.animation:
            cmd.append("--animation")

            cmd.append("--frameRange")
            cmd.append(self.frameRange)

            cmd.append("--frameStep")
            cmd.append(str(self.frameStep))

        cmd.append("--ignoreWarnings")


        return cmd

            
    def createProjectCmd(self):
        VRayPreferences = bpy.context.preferences.addons['vray_blender'].preferences

        return [VRayPreferences.vray_cloud_binary, "project", "create", "--name", self.project]

    def _parseSubmissionJobOutput(self, submitJobResult):
        for line in submitJobResult.split('\n'):
            try:
                res = json.loads(line)
                if "jobURL" in res:
                    bpy.ops.wm.url_open(url=res["jobURL"])
                    break
                elif "errors" in res:
                    for err in res["errors"]:
                        if "message" in err:
                            debug.printError(f"Chaos Cloud: {err['message']}")
            except json.JSONDecodeError:
                pass


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

            cmd = self.submitCmd()
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                debug.report("ERROR", "Chaos Cloud failed to submit job, check the console")

            self._parseSubmissionJobOutput(result.stdout)
        else:
            debug.report("ERROR", "Chaos Cloud binary is not detected on the system, done nothing")