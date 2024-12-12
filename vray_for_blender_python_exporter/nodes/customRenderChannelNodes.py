# Render channel plugins in V-Ray may be reused to achieve different effects. 
# The specific effect is selected by setting the 'alias' property. Each plugin
# has a default 'alias' value. The following list defines channels for non-default
# effects (variants). 
customRenderChannelNodesDesc = (
    # BEAUTY
    {
        "params":{
            "alias":  124,
            "name": "Background"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "BEAUTY"
    },
    {
        "params":{
            "alias":  107,
            "name": "Lighting"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "BEAUTY"
    },
    {
        "params":{
            "alias":  108,
            "name": "GI",
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "BEAUTY"
    },
    {
        "params":{
            "alias":  102,
            "name": "Reflection"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "BEAUTY"
    },
    {
        "params":{
            "alias":  103,
            "name": "Refraction"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "BEAUTY"
    },
    {
        "params": {
            "alias":  106,
            "name": "Specular"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "BEAUTY"
    },
    {
        "params": {
            "alias":  133,
            "name": "SSS"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "BEAUTY"
    },
    {
        "params":{
            "alias":  104,
            "name": "Self-Illumination"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "BEAUTY"
    },
    {
        "params":{
            "alias":  109,
            "name": "Caustics",
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "BEAUTY"
    },
    {
        "params":{
            "alias":  100,
            "name": "Atmospheric Effects"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "BEAUTY"
    },



    # ADVANCED
    {
        "params":{
            "alias":  101,
            "name": "Diffuse"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  112,
            "name": "Shadow"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{ 
            "alias":  129,
            "name": "Total Light"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  118,
            "name": "Reflection Filter"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  120,
            "name": "Refraction Filter"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  140,
            "name": "Reflection IOR",
        },
        "base_plugin_type": "RenderChannelGlossiness",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  135,
            "name": "Reflection Glossiness"
        },
        "base_plugin_type": "RenderChannelGlossiness",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  136,
            "name": "Reflection Highlight Glossiness"
        },
        "base_plugin_type": "RenderChannelGlossiness",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  137,
            "name": "Refraction Glossiness",
        },
        "base_plugin_type": "RenderChannelGlossiness",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  165,
            "name": "Metalness"
        },
        "base_plugin_type": "RenderChannelGlossiness",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  191,
            "name": "Coat Filter",
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  192,
            "name": "Coat Glossiness"
        },
        "base_plugin_type": "RenderChannelGlossiness",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  186,
            "name": "Sheen Glossiness"
        },
        "base_plugin_type": "RenderChannelGlossiness",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  185,
            "name": "Sheen Filter"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  180,
            "name": "Toon Lighting"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "ADVANCED"
    },
    {
        "params":{
            "alias":  181,
            "name": "Toon Specular"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "ADVANCED"
    },


    #MATTE
    {
        "params":{
            "alias":  115,
            "consider_for_aa": 0,
            "filtering": 1,
            "name": "Material ID"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "MATTE"
    },
    {
        "params":{
            "alias":  128,
            "consider_for_aa": 0,
            "denoise": 0,
            "name": "Matte Shadow"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "MATTE"
    },

    #UTILITY
    {
        "params":{
            "alias":  132,
            "color_mapping": 0,
            "name": "Sample Rate"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "UTILITY",
    },
    #RAW
    {
        "params":{
            "alias":  111,
            "name": "Raw Lighting"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "RAW",
    },
    {
        "params":{
            "alias":  110,
            "name": "Raw GI"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "RAW",
    },
    {
        "params":{ 
            "alias":  130,
            "name": "Raw Total Light"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "RAW"
    },
    {
        "params":{
            "alias":  119,
            "name": "Raw Reflection"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "RAW",
    },
    {
        "params":{
            "alias":  121,
            "name": "Raw Refraction"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "RAW",
    },
    {
        "params":{
            "alias":  189,
            "name": "Raw Coat Filter"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "RAW",
    },
    {
        "params":{
            "alias":  190,
            "name": "Raw Coat Reflection"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "RAW",
    },
    {
        "params":{
            "alias":  183,
            "name": "Raw Sheen Filter"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "RAW",
    },
    {
        "params":{
            "alias":  112,
            "name": "Raw Shadow"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "RAW",
    },
    {
        "params":{
            "alias":  110,
            "name": "Raw Sheen Reflection"
        },
        "base_plugin_type": "RenderChannelColor",
        "Subtype" : "RAW",
    }

)