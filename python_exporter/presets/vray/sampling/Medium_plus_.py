import bpy
vs = bpy.context.scene.vray

vs.SettingsImageSampler.samples_limit = 7396
vs.SettingsImageSampler.dmc_minSubdivs = 1
vs.SettingsImageSampler.dmc_maxSubdivs = 42
vs.SettingsImageSampler.dmc_threshold = 0.02
vs.SettingsImageSampler.progressive_minSubdivs = 1
vs.SettingsImageSampler.progressive_maxSubdivs = 42
vs.SettingsImageSampler.progressive_threshold = 0.02
vs.SettingsRTEngine.noise_threshold = 0.02
