

def getRegModules():
    from vray_blender.plugins.templates import (
        common,
        multi_select,
        include_exclude
    )

    return (
        common, # Keep this first in the list
        multi_select,
        include_exclude
    )


def register():
    for module in getRegModules():
        module.register()


def unregister():
    for module in reversed(getRegModules()):
        module.unregister()



def templateSingleObjectSelect():
    from vray_blender.plugins.templates import single_select
    return single_select.TemplateSingleObjectSelect

def templateMultiObjectSelect():
    from vray_blender.plugins.templates import multi_select
    return multi_select.TemplateMultiObjectSelect

def templateIncludeExclude():
    from vray_blender.plugins.templates import include_exclude
    return include_exclude.TemplateIncludeExclude


