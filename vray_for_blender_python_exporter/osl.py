#
# Copyright 2011-2013 Blender Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

# <pep8 compliant>

import os
import bpy
from vray_blender.exporting.tools import saveShaderScript

import mathutils

from vray_blender.lib import sys_utils
from vray_blender.bin import VRayBlenderLib as vray

def get_stdosl_path():
    def getPaths(pathStr):
        if pathStr:
            return pathStr.strip().replace('\"','').split(os.pathsep)
        return []

    env = os.environ
    for key in sorted(env.keys()):
        if key.startswith('VRAY_OSL_PATH_'):
            for p in getPaths(env[key]):
                stdPath = os.path.join(p, 'stdosl.h')
                if os.path.exists(stdPath):
                    return stdPath

    cyclesPath = sys_utils.getCyclesShaderPath()
    if cyclesPath:
        return os.path.join(cyclesPath, 'stdosl.h')

    return ''

def update_script_node(node, report):

    script = ""
    if node.mode == "INTERNAL":
        script = saveShaderScript(node.script)
        
    script = bpy.path.abspath(node.filepath)

    oslParamList = vray.getOslScriptParameters(script)
    

    for socRm in filter(lambda soc:  not soc.name == "Uvwgen", node.inputs):
        node.inputs.remove(socRm)

    for socRm in filter(lambda soc:  not soc.name == "Ci", node.outputs):
        node.outputs.remove(socRm)


    for param in oslParamList:
        value = None
        if param.socketType == 'VRaySocketColor':
            value = mathutils.Color(tuple(param.socketDefaultValue))
        else:
            value = param.socketDefaultValue

        if param.isOutputSocket:
            node.outputs.new(param.socketType, param.name)
            node.outputs[param.name].value = value
        else:
            node.inputs.new(param.socketType, param.name)
            node.inputs[param.name].value = value
