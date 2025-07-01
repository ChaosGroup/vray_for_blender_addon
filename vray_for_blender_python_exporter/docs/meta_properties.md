# Meta properties

The way plugin properties are presented to the user is not always the same as they have been defined in the plugin itself. To bridge this gap, V4B supports the so-called meta properties. 

A meta property is seen as a regular property by Blender but at the same time acts as a proxy to one or more underlying V-Ray plugin properties. The meta property is a property that is added to the list of regular plugin properties, but is marked as 'derived' in the plugin description. Example: color/texture which should be presented as one socket, but exported to either a COLOR or a TEXTURE property.

## General representation

```json
{
    "Parameters": [
        {
            "attr": "some_property",
            "type": "META_TYPE",
            "bound_prop1": "color",
            "bound_prop2": "color_tex"
        }
    ]
}
```

`type` is the meta property types, see the list below.
`bound_prop_n` are the actual properties which will wrapped by the meta property.

## A list of meta property types
The following meta property types are currently supported:

#### COLOR_TEXTURE
<ul>
Combines two plugin properties - a COLOR and a TEXTURE, which should be presented to the user as a single one. 

**Properties**

* color_prop - (string) the (A)COLOR plugin property
* tex_prop - (string) the TEXTURE plugin property

**Exports**

TEXTURE if the socket is connected, otherwise - COLOR
</ul>

#### BRDF_USE
<ul>
Combines two plugin properties - a BRDF  and a BOOL, which should be presented to the user as a single one. 

**Properties**

* target_prop - (string) a PLUIGN plugin
* use_prop - (string) a BOOL/INT flag property

**Exports**

PLUGIN if the socket is connected and the flag is ON, otherwise resets the PLUGIN.
</ul>