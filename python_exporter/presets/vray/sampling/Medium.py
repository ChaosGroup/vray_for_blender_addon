import bpy
vs = bpy.context.scene.vray

vs.SettingsImageSampler.samples_limit = 1764
vs.SettingsImageSampler.dmc_minSubdivs = 1
vs.SettingsImageSampler.dmc_maxSubdivs = 20
vs.SettingsImageSampler.dmc_threshold = 0.04
vs.SettingsImageSampler.progressive_minSubdivs = 1
vs.SettingsImageSampler.progressive_maxSubdivs = 20
vs.SettingsImageSampler.progressive_threshold = 0.04
vs.SettingsRTEngine.noise_threshold = 0.04
