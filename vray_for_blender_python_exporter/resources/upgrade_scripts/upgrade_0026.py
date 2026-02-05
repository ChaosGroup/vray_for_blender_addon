import bpy
from vray_blender.plugins.settings.SettingsImageSampler import _maxSubdivsGPUToSamplesLimit, _maxSubdivsCPUToSamplesLimit
from vray_blender.lib.sys_utils import isGPUEngine

def run():
    for scene in bpy.data.scenes:
        vrayScene = scene.vray
        settingsImageSampler = vrayScene.SettingsImageSampler

        if settingsImageSampler.type != '1': # Adaptive(bucket)
            # Go with the deafult value of samples_limit
            continue

        # CPU and GPU use different conversion because the GPU max subdivs used to be twice the CPU one.
        if isGPUEngine(scene):
            settingsImageSampler['samples_limit'] = _maxSubdivsGPUToSamplesLimit(settingsImageSampler.dmc_maxSubdivs)
        else:
            settingsImageSampler['samples_limit'] = _maxSubdivsCPUToSamplesLimit(settingsImageSampler.dmc_maxSubdivs)

def check():
    return True