# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy


def register():
    """ Register a hotkey for the V-Ray's Render operator. The hotkey is set to the same value as the one
        currently registered for the built-in Blender render operator. If no hotkey is registered for Blender,
        do not register any for V-Ray.
    """

    if bpy.app.background:
        # In headless mode, no keymaps can be used
        return
    
    # On first activation, register the same keymap for V-Ray's Render command as 
    # the one set for the Blender's Render. From then on, the user is responsible
    # for managing the keymap 
    # TODO: If the user deletes the keymap, the next add-on activation will re-create it.
    # Find a way to identify the first-ever activation of the add-on.
    wm = bpy.context.window_manager
    
    # The currently active keyconfig (the one selected in the UI). Its contents is merged with the
    # Blender's default keyconfig in runtime.
    kconfActive = wm.keyconfigs.active.keymaps

    # The currently active 'Screen' keymap (computed below)
    kmActive = None

    if kmScreen := kconfActive.get('Screen'):
        # The active keymap collection has a 'Screen' section, we can use it.
        kmActive = kmScreen.keymap_items
    elif kmBlender := wm.keyconfigs['Blender']:
        # The active keymap collection does not have a Screen section. Use the default Blender keymap.
        # It is guaranteed to be present, at least it cannot be deleted through the UI, but
        # handle the case where its missing just to stay on the safe side.
        if kmBlender.keymaps.get('Screen'):
            kmActive = wm.keyconfigs['Blender'].keymaps['Screen'].keymap_items

    if kmActive is None:
        # No keymap has been found that defines the 'Render' shortcut. Do not register a shortcut for V-Ray.
        return
    
    # Get the addon's 'private' keyconfig - the one that is active only when the addon is active.
    kconfVray = wm.keyconfigs.addon.keymaps
    
    # Make sure there is a 'Screen' keymap in the addon's keyconfig. This is where we will register
    # V-Ray's 'Render' shortcut
    kmVray = kconfVray.get('Screen', kconfVray.new(name='Screen')).keymap_items

    # For rendering with V-Ray, register the same shortcut key which is registered for the built-in Blender 
    # 'render' operartor. If no key is registered, do not register any key for V-Ray.
    if ('vray.render' not in kmVray) and ('render.render' in kmActive):
        for blenderKeymap in [i for i in kmActive if i.idname == 'render.render']:
            vrayKeymap = kmVray.new_from_item(blenderKeymap)
            vrayKeymap.idname = 'vray.render'
            vrayKeymap.properties.animation = blenderKeymap.properties.animation


def unregister():
    # The keymap will be automatically removed when Blender is restarted 
    # after the add-on is unregistered. Until then, it will live under different name
    # (idname vs name) regardless of whether we remove it here or not.
    pass
            