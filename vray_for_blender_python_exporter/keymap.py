
import bpy

# The following list will be used to keep track of the keymaps registered
# by the addon so that they could be unregistered when the add-on is unregistered
_addonKeymaps = []


def register():
    if bpy.app.background:
        # In headless mode, no keymaps can be used
        return
    
    # On first activation, register the same keymap for V-Ray's Render command as 
    # the one set for the Blender's Render. From then on, the user is responsible
    # for managing the keymap 
    # TODO: If the user deletes the keymap, the next add-on activation will re-create it.
    # Find a way to identify the first-ever activation of the add-on.
    wm = bpy.context.window_manager
    kmActive = wm.keyconfigs.active.keymaps
    kmiActive = kmActive['Screen'].keymap_items
    kmAddon = wm.keyconfigs.addon.keymaps

    if ('vray.render' not in kmiActive) and ('render.render' in kmiActive):
        # If no keymap is registered for the Blender's command, do not register
        # one for V-Ray, as it might clash with another user-defined keymap.
        if blenderKeymap := kmiActive['render.render']:
            if 'Screen' not in kmAddon:
                kmAddon.new(name='Screen')
            
            vrayKeymap = kmAddon['Screen'].keymap_items.new_from_item(blenderKeymap)
            vrayKeymap.idname='vray.render'
            _addonKeymaps.append(kmAddon['Screen'])
                    

def unregister():
    wm = bpy.context.window_manager
    for km in _addonKeymaps:
        wm.keyconfigs.addon.keymaps.remove(km)

    _addonKeymaps.clear()
   