from __future__ import annotations # For the forward type hints
from typing import Dict, Set
import bpy

from vray_blender.lib.names import Names

# This file contains code for tracking VRay plugin deletion in response to
# objects/materials being deleted or hidden in Blender.


# During development, we need to easily turn on and off logs as they are not
# always needed. Uncomment the call to print() to achieve that
# TODO: this function and its invocations should be deleted in the release version
def log(msg: str):
    #print(msg)
    pass    

def getObjTrackId(obj: bpy.types.ID):
        """ Returns a unique identifier of an object """
        return obj.original.session_uid


def getNodeTrackId(obj: bpy.types.Node):
    """ Returns a unique identifier for the node. """
    return Names.node(obj)

def getConnectedTrackIds(node: bpy.types.Node, connectedIds=None):
    """ Returns a list of Track Ids of the nodes connected to 'node' """
    if connectedIds is None:
        connectedIds = set()

    for inputSocket in node.inputs:
        for link in [l for l in inputSocket.links if not l.is_muted and not l.is_hidden]:
            fromNode = link.from_node
            connectedIds.add(getNodeTrackId(fromNode))
            getConnectedTrackIds(fromNode, connectedIds) 

    return connectedIds


class ObjTracker:
    """ ObjTracker tracks the lifetimes of scene objects and their associated 
        VRay plugins. It is used to determine which objects and plugins should be 
        removed in reponse to changes to the Blender scene.
    """
    def __init__(self, type: str):
        self.type:      str                   = type  # Arbitrary description of the tracked objects' type
        self.ids:       Dict[str, Set(str)]   = {}    # objTrackId: set(pluginId) - plugins per object
        self.plugins:   Dict[str, int]        = {}    # pluginId: refCount - flat plugins list with refcounts
        self.instanced: Dict[str, bool]       = {}    # objTrackId: Bool - True if the object is instanced.


    def trackPlugin(self, objTrackId: str, pluginId: str, isInstanced=False):
        """ Add plugin to the object's tracking list """
        self._trackObj(objTrackId)
        self._addPluginToObj(objTrackId, pluginId)
        self.instanced[pluginId] = isInstanced
        log(f"TRACK PLUGIN {self.type}: {objTrackId} => {pluginId}  [{self.ids[objTrackId]}]")

    
    def forget(self, objTrackId: str):
        """ Remove object from the tracking list """ 
        if objTrackId not in self.ids:
            return
        
        # Release refcount on plugins first
        for pluginId in self.ids[objTrackId]:
           self._releasePlugin(pluginId)
        
        # Delete tracked object's registration
        del self.ids[objTrackId]
        log(f"FORGET ID {self.type}: {objTrackId}")
    

    def forgetPlugin(self, objTrackId: str, pluginId: str):
        if pluginId in self.ids.get(objTrackId, []):
            self._releasePlugin(pluginId)
            self.ids[objTrackId].remove(pluginId)


    def diff(self, objTrackIds):
        """ Returns all tracked objects which are not in objTrackIds """
        return set(self.ids.keys()).difference(objTrackIds)
    

    def getOwnedPlugins(self, objTrackId: str):
        """ Returns all object's plugins with reference count 1 ( i.e. that can be deleted ) """
        if objTrackId in self.ids:
            return [p for p in self.ids[objTrackId] if self.plugins[p] == 1]
        return []
    
    def getTrackedObjects(self):
        return self.ids.keys()
    

    def getPlugins(self, objTrackId):
        """ Returns all tracked plugins for the object """
        if objTrackId in self.ids:
            return self.ids[objTrackId]
        return set()

    def getPluginInstanced(self, pluginId):
        """ Returns True if the tracked plugin is instanced """
        if pluginId in self.instanced:
            return self.instanced[pluginId]
        return False


    def _trackObj(self, objTrackId: str):
        if objTrackId not in self.ids.keys():
            self.ids[objTrackId] = set()
            log(f"TRACK ID {self.type}: {objTrackId}")


    # Add to the list of all tracked plugins / increment recount on a plugin 
    def _addRefPlugin(self, pluginId):
        if pluginId not in self.plugins:
            self.plugins[pluginId] = 1
        else:
            self.plugins[pluginId] += 1


    # Decrement refcount on the plugin and delete from the list of tracked plugins
    # if the refcount has reached 0
    def _releasePlugin(self, pluginId):
        self.plugins[pluginId] -= 1
        if self.plugins[pluginId] == 0:
            del self.plugins[pluginId]
            del self.instanced[pluginId]


    # Register plugin with object
    def _addPluginToObj(self, objTrackId, pluginId):
        if pluginId not in self.ids[objTrackId]:
            self.ids[objTrackId].add(pluginId)
            self._addRefPlugin(pluginId)


class NodeTracker:
    """ NodeTracker tracks the lifetimes of nodes in vray nodetrees and their associated 
        VRay plugins. It is used to determine which nodes and plugins should be 
        removed in reponse to changes to the Blender scene.
    """
    def __init__(self, type: str):
        self.type:    str = type  # Arbitrary description of the tracked objects' type
        self.nodes:   Dict[str, Dict[str, Set(str)]]   = {}   # objTrackId: dict(node name -> plugins )  object


    def trackNodePlugin(self, objTrackId: str, nodeTrackId: str, pluginId: str):
        """ Add plugin to the node's tracking list """
        self._trackNode(objTrackId, nodeTrackId)
        self._addPluginToNode(objTrackId, nodeTrackId, pluginId)
        log(f"TRACK NODE PLUGIN {self.type}: {objTrackId}:{nodeTrackId} => {pluginId}  [{self.nodes[objTrackId][nodeTrackId]}]")


    def forgetNode(self, objTrackId: str, nodeTrackId: str):
        if not self._isTrackedNode(objTrackId, nodeTrackId):
            return
        
        del self.nodes[objTrackId][nodeTrackId]
        log(f"FORGET NODE {self.type}: {objTrackId}:{nodeTrackId}")


    def forgetObj(self, objTrackId: str):
        """ Remove object from the tracking list """ 
        if objTrackId not in self.nodes:
            return
        
        del self.nodes[objTrackId]
        log(f"FORGET NODETREE {self.type}: {objTrackId}")
 

    def diffObjs(self, objTrackIds: list[str]):
        """ Returns all tracked objects which are not in objTrackIds """
        return set(self.nodes.keys()).difference(objTrackIds)

        
    def diffNodes(self, objTrackId: str, nodeTrackIds: list[str]):
        """ Returns all tracked nodes which are not in nodeTrackIds """
        if objTrackId not in self.nodes:
            return
        
        return set(self.nodes[objTrackId].keys()).difference(nodeTrackIds)
    

    def getOwnedNodes(self, objTrackId: str):
        if objTrackId in self.nodes:
            return [nodeId for nodeId in self.nodes[objTrackId]]
        return []
    
  
    def getNodePlugins(self, objTrackId: str, nodeTrackId: str):
        """ Returns all object's plugins with reference count 1 ( i.e. that can be deleted ) """
        if self._isTrackedNode(objTrackId, nodeTrackId):
            return [p for p in self.nodes[objTrackId][nodeTrackId]]
        return []
  

    def _trackNode(self, objTrackId: str, nodeTrackId: str):
        if objTrackId not in self.nodes:
            self.nodes[objTrackId] = {}
            
        if nodeTrackId not in self.nodes[objTrackId]:
            self.nodes[objTrackId][nodeTrackId] = set()
            log(f"TRACK NODE {self.type}: {objTrackId}:{nodeTrackId}")


    def _addPluginToNode(self, objTrackId, nodeTrackId, pluginId):
        nodePlugins = self.nodes[objTrackId][nodeTrackId]
        nodePlugins.add(pluginId)


    def _isTrackedNode(self, objTrackId: str, nodeTrackId: str):
        return objTrackId in self.nodes and nodeTrackId in self.nodes[objTrackId]


class ScopedNodeTracker(NodeTracker):
    """ This class adds functionality for defining obj context to NodeTracker.
        This is useful when tracking of plugins for the same netity spans more
        than 1 function.

        It is used in tandem with the TrackXXX context managers.
    """
    def __init__(self, type: str):
        super().__init__(type)

        # Identification of the object being currently updated. Set/reset by the trackStart/trackEnd methods
        self.objId: str = ""      
        self.nodeId: str = ""
        self.nodeStack = [] # A stack of all tracked nodes


    def trackObjStart(self, objTrackId: str):
        """ Just register the current object being tracked """
        self.objId = objTrackId
    

    def trackNodeStart(self, nodeTrackId):
        """ Switch on update mode for the node. All calls to trackPlugin() will register the plugins 
            with this node, until trackEnd() is called
        """
        if self.nodeId != "":
            self.nodeStack.append(self.nodeId)

        self.nodeId = nodeTrackId
        self._trackNode(self.objId, self.nodeId)


    def trackObjEnd(self):
        """ Switch off update mode for the current object """
        self.objId = ""


    def trackNodeEnd(self):
        """ Switch off update mode for the current node and activate
            the previous one, if any
        """
        if len(self.nodeStack) > 0:
            self.nodeId = self.nodeStack.pop()
        else:
            self.nodeId = ""


    def trackPlugin(self, pluginId: str):
        """ Add plugin to the current object's tracking list """
        self._addPluginToNode(self.objId, self.nodeId, pluginId)
        log(f"TRACK NODE PLUGIN {self.type}: {self.objId}:{self.nodeId} => {pluginId}  [{self.nodes[self.objId][self.nodeId]}]")


################################
## Context managers
################################
class TrackNode:
    """ Context manager for ScopedNodeTracker """

    def __init__(self, tracker: ScopedNodeTracker, nodeTrackId: str):
        self.tracker = tracker
        self.trackId = nodeTrackId

    def __enter__(self):
        self.tracker.trackNodeStart(self.trackId)
        return self.tracker

    def __exit__(self, *args):
        self.tracker.trackNodeEnd()
         
class TrackObj:
    """ Context manager for ScopedNodeTracker """

    def __init__(self, tracker: ScopedNodeTracker, objTrackId: str):
        self.tracker = tracker
        self.trackId = objTrackId

    def __enter__(self):
        self.tracker.trackObjStart(self.trackId)
        return self.tracker

    def __exit__(self, *args):
        self.tracker.trackObjEnd()
         


################################
## No-op implementations
################################
class FakeScopedNodeTracker:
    """ A no-op impementation for use with production exports """
    def forgetNode(self, objTrackId: str, nodeTrackId: str):
        pass

    def forgetObj(self, objTrackId: str):
        pass

    def diffObjs(self, objTrackIds: list[str]):
        return set()

    def diffNodes(self, objTrackId: str, nodeTrackIds: list[str]):
        return set()

    def getOwnedNodes(self, objTrackId: str):
        return []

    def getNodePlugins(self, objTrackId: str, nodeTrackId: str):
        return []

    def trackObjStart(self, objTrackId: str):
        pass

    def trackObjEnd(self):
        pass

    def trackNodeStart(self, nodeTrackId):
        pass

    def trackNodeEnd(self):
        pass

    def trackPlugin(self, pluginId: str):
        pass

    def trackNodePlugin(self, objTrackId: str, nodeTrackId: str, pluginId: str):
        pass

    def forgetNode(self, objTrackId: str, nodeTrackId: str):
        pass

class FakeObjTracker:
    """ A no-op impementation for use with production exports """
    def trackPlugin(self, objTrackId: str, pluginId: str, isInstanced = False):
        pass

    def forget(self, objTrackId: str):
        pass

    def forgetPlugin(self, objTrackId: str, pluginId: str):
        pass

    def diff(self, objTrackIds: list[str]):
        return set()

    def getOwnedPlugins(self, objTrackId: str):
        return []

    def getTrackedObjects(self):
        return ()

    def getPlugins(self, objTrackId):
        return set()
