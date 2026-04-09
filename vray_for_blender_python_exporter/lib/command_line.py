# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy
import argparse
import sys
from bpy import path as bpyPath
from pathlib import PurePath

from vray_blender import debug


def _parseImageFormat(vrayScene, imgFormat: str):
    if not imgFormat.isupper():
        debug.printWarning("Image format (-F) must be specified in upper case on the command line. " \
                            "Support for non-upper case in V-Ray for Blender might be removed in the future.")
    imgFormat = imgFormat.upper()

    settingsOutput = vrayScene.SettingsOutput

    # Get a {'PNG': '0', 'JPG': 1 ...} dictionary
    # Note that the list of supported formats is different between V-Ray and Cycles
    # so Blender might print some error messages
    supportedFormats = {item.name.upper(): item.identifier for item in settingsOutput.bl_rna.properties['img_format'].enum_items}
    
    if imgFormat not in supportedFormats:
        raise Exception(f"Unsupported output format: {imgFormat}")
    
    _printParameter('Output format', imgFormat)
    settingsOutput.img_format = supportedFormats[imgFormat]


def _parseOutput(vrayScene, outputPath: str):
    if outputPath == '':
        raise Exception("Output path not set")
    
    outPath = PurePath(bpyPath.abspath(outputPath))

    vrayScene.SettingsOutput.img_file = outPath.name
    vrayScene.SettingsOutput.img_dir  = str(outPath.parent)
    vrayScene.Exporter.auto_save_render = True    # Tell V-Ray to save the rendered image

    _printParameter('Output directory', vrayScene.SettingsOutput.img_dir)
        

def _printParameter(setting: str, value):
    debug.printAlways(f"{setting + ':':<20} {value}")


def parseCommandLine():
    vrayScene = bpy.context.scene.vray
    vrayExporter = vrayScene.Exporter

    parser = argparse.ArgumentParser(description="V-Ray for Blender CLI")

    # Merge Blender and V-Ray's list into one
    argslist = sys.argv.remove('--') if '--' in sys.argv else sys.argv
                               
    parser.add_argument("-o", "--render-output", dest="output", type=str)
    parser.add_argument("-F", "--render-format", dest="img_format", type=str)
    parser.add_argument("-a", "--render-anim", dest="render_anim", action="store_true")
    parser.add_argument("-f", "--render-frame", dest="render_frame", type=str)
    parser.add_argument("-s", "--frame-start", dest="frame_start", type=str)
    parser.add_argument("-e", "--frame-end", dest="frame_end", type=str)

    args, _ = parser.parse_known_args(argslist)

    if args.output:
        _parseOutput(vrayScene, args.output)

    if args.img_format:
        _parseImageFormat(vrayScene, args.img_format)
    
    if args.render_frame:
        vrayExporter.frames_list = args.render_frame
        vrayExporter.use_frame_range = False
    elif any(item in ('-s', '-e') for item in sys.argv):
        vrayExporter.use_frame_range = True

    if args.render_anim:
        vrayExporter.animation_mode = 'ANIMATION'
    elif args.render_frame:
        # Override the animation mode set in the scene
        vrayExporter.animation_mode = 'FRAME'
    
    _printParameter('Animation mode', vrayExporter.animation_mode)