import bpy
vs = bpy.context.scene.vray

vs.SettingsImageSampler.samples_limit = 16900
vs.SettingsImageSampler.dmc_minSubdivs = 2
vs.SettingsImageSampler.dmc_maxSubdivs = 64
vs.SettingsImageSampler.dmc_threshold = 0.01
vs.SettingsImageSampler.progressive_minSubdivs = 2
vs.SettingsImageSampler.progressive_maxSubdivs = 64
vs.SettingsImageSampler.progressive_threshold = 0.01
vs.SettingsRTEngine.noise_threshold = 0.01
