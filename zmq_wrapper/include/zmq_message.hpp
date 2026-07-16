// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#define ZMQ_BUILD_DRAFT_API

#include <string>
#include <vector>

#include "cppzmq/zmq.hpp"
#include "base_types.h"
#include "msg_serializer.hpp"


/// Protocol serialization
/// How to define a new message:
///
///	All helper macros and functions are defined in msg_serializer.hpp.
///
/// 1. Add a new message type to the appropriate range in MsgType enum. Messages sent
///    from the client are called 'message', messages sent from the server are called 'event'.
///
///     enum MsgType {
///        ...
///        ControlNewMessage
///     }
///
/// 2. Define the new message type. The name of the resulting class will be the message type name
///    prefixed by 'Msg', i.e. MsgControlNewMessage.
///
///	     PROTO_MESSAGE(ControlNewMessage,
///		    int number;
///         std::string item;
///      );
///
///		NOTE: For empty messages, use the EMPTY_PROTO_MESSAGE macro.
///
/// 3. Declare the default serialization procedure
///      SERIALIZE_MESSAGE(ControlNewMessage,
///         PARAM(number)
///         PARAM(item)
///      );
///
///		NOTE: For empty messages, use the SERIALIZE_EMPTY_MESSAGE macro.
///
///   3.a. If default serialization cannot be used, define custom serialization:
///      static SerializerStream& operator&& (SerializerStream& stream, const MsgControlNewMessage& msg) {
///         stream  << msg.nubmer << msg.item
///         return stream;
///      }
///      static DeserializerStream& operator&& (DeserializerStream& stream, MsgControlNewMessage& msg) {
///       	stream  >> msg.number >> msg.item;
///       	return stream;
///      }
///
/// 4. To serialize
///      const auto& zmq::message_t = serializeMessage(MsgControlNewMessage{10, "boxes"});
///
/// 5. Te deserialize
///		 DeserializerStream& stream; // Unpacked from zmq::message_t
///      const MsgControlNewMessage& message = deserializeMessage<MsgControlNewMessage>(stream);
///	     std::cout << message.number << message.item;
///

#pragma warning (push)
#pragma warning (disable: 4505) // Unreferenced function has been removed

namespace VrayZmqWrapper {

namespace vray = VRayBaseTypes;

enum class MsgType : char {
	None,

	// Plugin messages
	FirstPluginMessage,
	PluginCreate,
	PluginRemove,
	PluginUpdate,
	PluginReplace,
	LastPluginMessage,

	// Renderer messages
	FirstRendererMessage,
	RendererFree,
	RendererStart,
	RendererStop,
	RendererPause,
	RendererResume,
	RendererResize,
	RendererReset,
	RendererAbort,
	RendererInit,
	RendererEnableDistributedRendering,
	RendererLoadScene,
	RendererAppendScene,
	RendererExportScene,
	RendererExportProxy,
	RendererSetRenderMode,
	RendererSetCurrentTime,
	RendererSetCurrentFrame,
	RendererClearFrameValues,
	RendererGetImage,
	RendererSetQuality,
	RendererSetCurrentCamera,
	RendererSetCommitAction,
	RendererSetVfbOptions,
	RendererSetViewportImageFormat,
	RendererSetRenderRegion,
	RendererSetCropRegion,
	RendererRenderSequence,
	RendererContinueSequence,
	RendererSetResumableRendering,
	RendererElementDone,            ///< Client -> server: element SHM buffer consumed; safe to overwrite.
	LastRendererMessage,

	// Renderer events
	FirstRendererEvent,
	RendererOnVRayLog,
	RendererOnImage,
	RendererOnChangeState,
	RendererOnAsyncOpComplete,
	RendererOnProgress,
	RendererOnElementReady,
	LastRendererEvent,

	// Control messages
	FirstControlMessage,
	ControlSetLogLevel,
	ControlOpenCollaboration,

	// Cosmos
	ControlOnOpenCosmos,
	ControlOnCosmosCalculateDownloadSize,
	ControlOnCosmosDownloadSize,
	ControlOnCosmosDownloadAssets,
	ControlOnCosmosDownloadedAssets,
	ControlOnUpdateScenePath,

	// Scanned materials
	ControlOnScannedLicenseCheck,
	ControlOnScannedEncodeParameters,
	ControlOnScannedEncodedParameters,

	ControlShowUserDialog,
	ControlSetTelemetryState,
	ControlSetVfbOnTop,
	ControlUpdateVfbSettings,
	ControlUpdateVfbLayers,
	ControlShowVfb,
	ControlResetVfbToolbar,
	ControlSetVfbRenderRegion,
	ControlGetComputeDevices,
	ControlSetComputeDevices,
	ControlSetUpdateAvailable,
	ControlLogVfbMessage,
	ControlClearVfbImage,
	ControlSetVisualDebugger,
	LastControlMessage,

	// Control events
	FirstControlEvent,
	ControlOnStartViewportRender,
	ControlOnStartProductionRender,
	ControlOnUpdateVfbSettings,
	ControlOnUpdateVfbLayers,
	ControlOnLogMessage,
	ControlOnImportAsset,
	ControlOnRendererStatus,
	ControlOnGetComputeDevices,
	ControlOnAutoUpdateCheckChanged,
	ControlOnAppUpdateRequested,
	ControlOnLightMixTransferToScene,
	ControlOnVFBMenu,
	ControlOnAddRenderElementToScene,
	ControlOnVFBShowMessagesWindow,
	ControlOnVFBRenderRegionChanged,
	ControlOnSwitchLicenseToCommunity,
	LastControlEvent,

	// Compute devices
};



enum DRFlags : char {
	None              = 0,
	EnableDr          = 1 << 1,
	RenderOnlyOnHosts = 1 << 2,
	_SerializationShift = 8,
};

enum class RendererType : char {
	None				= 0,
	RT,
	Animation,
	SingleFrame,
	Preview,
	_SerializationShift = 0,
};


enum class RendererState : char {
	None,
	Abort,				// Rendering aborted
	Continue,			// Rendering resumed by user
	Stopped,			// Rendering stopped by stop() or from UI
	Done,				// Rendering done
	Progress,			// [Not a state] Numerical progress message
	ProgressMessage,	// [Not a state] Textual progress message
};

/// Asyncronous operations for which a RendererOnAsyncOpComplete message will be sent to the client
enum class RendererAsyncOp: char {
	None,
	ExportVrscene,		// Export .vrscene
	ExportVrmesh		// Export .vrmesh
};


enum class ImportedAssetType : char {
	None,
	Material,
	VRMesh,
	HDRI,
	Extras,
	ParallaxInterior
};


enum class VRayStatusType : char {
	None = 0,
	MainRendererAvailable,
	MainRendererUnavailable,
	LicenseAcquired,
	LicenseDropped

};

enum class ComputeDeviceType : int {
	CUDA = 0,
	Optix = 1,
	Metal = 2,
	HIP = 3,
	LastDevice = HIP
};

enum class VfbMessageLevel : int {
	MessageError = 0,
	MessageWarning = 1,
	MessageInfo = 2,
	MessageDebug = 3
};


// Impelments the protocol for VRay::RenderSizeParams.
struct RenderSizes {
	enum class Bitmask : int {
		None      = 0x0,
		ImgSize   = 0x1,
		CropRgn   = 0x2,
		RenderRgn = 0x4
	};

	RenderSizes() = default;

	bool operator==(const RenderSizes rhs) const {
		return
			imgWidth      == rhs.imgWidth &&
			imgHeight     == rhs.imgHeight &&
			bmpWidth      == rhs.bmpWidth &&
			bmpHeight     == rhs.bmpHeight &&
			cropRgnLeft   == rhs.cropRgnLeft &&
			cropRgnTop    == rhs.cropRgnTop &&
			cropRgnWidth  == rhs.cropRgnWidth &&
			cropRgnHeight == rhs.cropRgnHeight &&
			rgnLeft       == rhs.rgnLeft &&
			rgnTop        == rhs.rgnTop &&
			rgnWidth      == rhs.rgnWidth &&
			rgnHeight     == rhs.rgnHeight;
	}


	int bitmask = 0;	// Same as VRay::RenderSizeParams::RenderSizeBitmask

	// Output image size. This is the size of the image that we get in onImageUpdated.
	int imgWidth = 0;
	int imgHeight = 0;

	// Framebuffer size that VRay uses for the actual rendering. The image is scaled
	// to imgWidth/Height to produce the actual output. If scaling is disproportional,
	// the output image will be stretched.
	// Usually this is the same as imgWidth/Height for mono and double that in one of
	// the directions for stereo rendering.
	int bmpWidth = 0;
	int bmpHeight = 0;

	// Size of the region inside bmpWidth/Height we actually want to render.
	// This is used when only a portion of the viewport/VBF is selected for rendering.
	float cropRgnLeft = 0;
	float cropRgnTop = 0;
	float cropRgnWidth = 0;
	float cropRgnHeight = 0;

	// Size of the region inside imgWidth/Height we want to render
	int rgnLeft = 0;
	int rgnTop = 0;
	int rgnWidth = 0;
	int rgnHeight = 0;
};


SERIALIZE_STRUCT(RenderSizes,
	PARAM(bitmask)
	PARAM(imgWidth)
	PARAM(imgHeight)
	PARAM(bmpWidth)
	PARAM(bmpHeight)
	PARAM(cropRgnLeft)
	PARAM(cropRgnTop)
	PARAM(cropRgnWidth)
	PARAM(cropRgnHeight)
	PARAM(rgnLeft)
	PARAM(rgnTop)
	PARAM(rgnWidth)
	PARAM(rgnHeight)
);


struct AssetMetaData {
	ImportedAssetType type;
	std::string materialFile;
	std::string objectFile;
	VRayBaseTypes::AttrListString assetNames;
	VRayBaseTypes::AttrListString assetLocations;
};


/// Options to pass in VRayExportSettings when exporting a. vrscene
struct ExportSettings {
	struct SubFileInfo {
		std::string fileNameSuffix;
		std::string pluginType;
	};

	bool compressed;
	bool hexArrays;
	bool hexTransforms;
	bool cloudExport;
	std::string hostAppString;
	std::string filePath;
	std::vector<SubFileInfo> subFileInfo;
};



static SerializerStream& operator&& (SerializerStream& stream, const ExportSettings& settings) {
	stream  << settings.compressed
			<< settings.hexArrays << settings.hexTransforms
			<< settings.cloudExport
			<< settings.hostAppString
			<< settings.filePath
			<< settings.subFileInfo.size();

	for ( const auto item : settings.subFileInfo) {
		stream << item.fileNameSuffix << item.pluginType;
	}

	return stream;
}

static DeserializerStream& operator&& (DeserializerStream& stream, ExportSettings& settings) {
	size_t numSubFiles = 0;

	stream  >> settings.compressed
			>> settings.hexArrays >> settings.hexTransforms
			>> settings.cloudExport
			>> settings.hostAppString
			>> settings.filePath
			>> numSubFiles;

	settings.subFileInfo.resize(numSubFiles);

	for (size_t i = 0; i < numSubFiles; ++i) {
		stream  >> settings.subFileInfo[i].fileNameSuffix
			>> settings.subFileInfo[i].pluginType;
	}

	return stream;
}


struct HostInfo {
	std::string vrayVersion;
	std::string buildVersion;
	std::string blenderVersion;
};


SERIALIZE_STRUCT(HostInfo,
	PARAM(vrayVersion)
	PARAM(buildVersion)
	PARAM(blenderVersion)
);


PROTO_MESSAGE( PluginCreate,
	std::string pluginName;
	std::string pluginType;
	bool allowTypeChanges = false;
);

SERIALIZE_MESSAGE(PluginCreate,
	PARAM(pluginName)
	PARAM(pluginType)
	PARAM(allowTypeChanges)
);


PROTO_MESSAGE(PluginRemove,
	std::string pluginName;
);

SERIALIZE_MESSAGE(PluginRemove,
	PARAM(pluginName)
);


/// MsgPluginUpdate
PROTO_MESSAGE(PluginUpdate,
	std::string pluginName;
	std::string propertyName;
	vray::AttrValue propertyValue;

	uint32_t flags = 0;
	inline void setAnimatable(bool animatable) {
		if (animatable) {
			flags |= vray::PluginUpdateFlags::PluginValueAnimatable;
		}
	}
	inline void setForceUpdate(bool forceUpdate) {
		if (forceUpdate) {
			flags |= vray::PluginUpdateFlags::PluginValueForceUpdate;
		}
	}

	inline void setReCreateAttribute(bool recreateAttribute) {
		if (recreateAttribute) {
			flags |= vray::PluginUpdateFlags::PluginReCreateAttr;
		}
    }
	inline bool getForceUpdate() const { return (flags & vray::PluginUpdateFlags::PluginValueForceUpdate) != 0; }
	inline bool getAsString() const { return (flags & vray::PluginUpdateFlags::PluginValueAsString) != 0; }
	inline bool getAnimatable() const { return (flags & vray::PluginUpdateFlags::PluginValueAnimatable) != 0; }
	inline bool getReCreateAttribute() const { return (flags & vray::PluginUpdateFlags::PluginReCreateAttr) != 0; }

);


SERIALIZE_MESSAGE(PluginUpdate,
	PARAM(pluginName)
	PARAM(propertyName)
	PARAM(propertyValue)
	PARAM(flags)
);


/// MsgPluginReplace
PROTO_MESSAGE(PluginReplace,
	std::string oldPluginName;
	std::string newPluginName;
);

SERIALIZE_MESSAGE(PluginReplace,
	PARAM(oldPluginName)
	PARAM(newPluginName)
);

/// MsgImage
PROTO_MESSAGE(RendererOnImage,
	vray::AttrImageSet imageSet;
	int imgId = -1;
	int bufferIndex = 0;  ///< Which of the two double-buffers was written (0 or 1)
);

SERIALIZE_MESSAGE(RendererOnImage,
	PARAM(imageSet)
	PARAM(imgId)
	PARAM(bufferIndex)
);


/// MsgRendererElementReady - per-element shared-memory notification. The pixel data is
/// already in the element SHM region; this message tells the client which slot to copy
/// from and where it goes.
PROTO_MESSAGE(RendererOnElementReady,
	std::string pluginInstanceName;  ///< V-Ray plugin instance name (the `name` attribute).
	int subIndex = 0;                ///< Cryptomatte layer, ObjectSelect 0/1/2, or 0 for generic.
	int width    = 0;
	int height   = 0;
	int channels = 0;                ///< 1, 3, or 4.
	int imageType = 0;               ///< VRayBaseTypes::AttrImage::ImageType cast to int.
	std::string metadataKey;         ///< Optional metadata key (e.g. "cryptomatte.<instance>"). Empty if absent.
	std::string metadataValue;
);

SERIALIZE_MESSAGE(RendererOnElementReady,
	PARAM(pluginInstanceName)
	PARAM(subIndex)
	PARAM(width)
	PARAM(height)
	PARAM(channels)
	PARAM(imageType)
	PARAM(metadataKey)
	PARAM(metadataValue)
);


/// Client -> server: per-element SHM consumed; the server may overwrite the slot
/// for the next layer. Required because multiple layers reuse one SHM region.
PROTO_MESSAGE(RendererElementDone,
	int subIndex = 0;
);

SERIALIZE_MESSAGE(RendererElementDone,
	PARAM(subIndex)
);


/// MsgVfbLayers
PROTO_MESSAGE(ControlOnUpdateVfbLayers,
	std::string vfbLayersJson;
);

SERIALIZE_MESSAGE(ControlOnUpdateVfbLayers,
	PARAM(vfbLayersJson)
);

/// MsgVRayLog
PROTO_MESSAGE(RendererOnVRayLog,
	int logLevel;
	std::string log;
);

SERIALIZE_MESSAGE(RendererOnVRayLog,
	PARAM(logLevel)
	PARAM(log)
);


/// MsgRendererFree
EMPTY_PROTO_MESSAGE(RendererFree);
SERIALIZE_EMPTY_MESSAGE(RendererFree);



/// MsgRendererStart
/// imageToBlender = false suppresses both the Combined image and per-element emits
/// for the duration of this render - Blender allocates no pass buffers and V-Ray's
/// VFB is the only consumer. The client sets it from scene.vray.Exporter.image_to_blender
/// before calling vray.renderStart().
PROTO_MESSAGE(RendererStart,
	bool imageToBlender = true;
);

SERIALIZE_MESSAGE(RendererStart,
	PARAM(imageToBlender)
);


/// MsgRendererStop
EMPTY_PROTO_MESSAGE(RendererStop);
SERIALIZE_EMPTY_MESSAGE(RendererStop);


/// MsgRendererPause
EMPTY_PROTO_MESSAGE(RendererPause);
SERIALIZE_EMPTY_MESSAGE(RendererPause);

/// MsgRendererResume
EMPTY_PROTO_MESSAGE(RendererResume);
SERIALIZE_EMPTY_MESSAGE(RendererResume);

/// MsgRendererReset
EMPTY_PROTO_MESSAGE(RendererReset);
SERIALIZE_EMPTY_MESSAGE(RendererReset);

/// MsgRendererAbort
EMPTY_PROTO_MESSAGE(RendererAbort);
SERIALIZE_EMPTY_MESSAGE(RendererAbort);

/// MsgRendererInit
PROTO_MESSAGE(RendererInit,
	RendererType rendererType;
	int renderThreads;
	int exporterType;
);

SERIALIZE_MESSAGE(RendererInit,
	PARAM(rendererType)
	PARAM(renderThreads)
	PARAM(exporterType)
);


/// MsgRendererResize
PROTO_MESSAGE(RendererResize,
	RenderSizes renderSizes;
);

SERIALIZE_MESSAGE(RendererResize,
	PARAM(renderSizes)
);


/// MsgRendererEnableDistributedRendering
PROTO_MESSAGE(RendererEnableDistributedRendering,
	std::string hosts;
	DRFlags drFlags;
	std::string remoteDispatcher;
);

SERIALIZE_MESSAGE(RendererEnableDistributedRendering,
	PARAM(hosts)
	PARAM(drFlags)
	PARAM(remoteDispatcher)
);


/// MsgRendererLoadScene
PROTO_MESSAGE(RendererLoadScene,
	std::string fileName;
);

SERIALIZE_MESSAGE(RendererLoadScene,
	PARAM(fileName)
);


/// MsgRendererAppendScene
PROTO_MESSAGE(RendererAppendScene,
	std::string fileName;
);

SERIALIZE_MESSAGE(RendererAppendScene,
	PARAM(fileName)
);


/// MsgRendererExportScene
PROTO_MESSAGE(RendererExportScene,
	ExportSettings exportSettings;
);

SERIALIZE_MESSAGE(RendererExportScene,
	PARAM(exportSettings)
);

/// MsgRendererExportProxy
PROTO_MESSAGE(RendererExportProxy,
	std::string filePath;
	int elementsPerVoxel = 64;
	int previewFaces = 10000;
	int previewType = 3; //!< @see VRay::ProxyCreateParams::PreviewTypes (0-3)
	int animOn = 0;
	int startFrame = 0;
	int endFrame = 0;
);

SERIALIZE_MESSAGE(RendererExportProxy,
	PARAM(filePath)
	PARAM(elementsPerVoxel)
	PARAM(previewFaces)
	PARAM(previewType)
	PARAM(animOn)
	PARAM(startFrame)
	PARAM(endFrame)
);

/// MsgRendererSetRenderMode
PROTO_MESSAGE(RendererSetRenderMode,
	int renderMode;	// VRay::VRayRenderer::RenderMode
);

SERIALIZE_MESSAGE(RendererSetRenderMode,
	PARAM(renderMode)
);


/// MsgRendererSetCurrentTime
PROTO_MESSAGE(RendererSetCurrentTime,
	float frame;
);

SERIALIZE_MESSAGE(RendererSetCurrentTime,
	PARAM(frame)
);


/// MsgRendererSetCurrentFrame
PROTO_MESSAGE(RendererSetCurrentFrame,
	float frame;
);

SERIALIZE_MESSAGE(RendererSetCurrentFrame,
	PARAM(frame)
);


/// MsgRendererClearFrameValues
PROTO_MESSAGE(RendererClearFrameValues,
	float upToTime;
);

SERIALIZE_MESSAGE(RendererClearFrameValues,
	PARAM(upToTime)
);


/// MsgRendererGetImage
PROTO_MESSAGE(RendererGetImage,
	int         renderElementType;   // VRay::RenderElement::Type
	std::string pluginInstanceName;  // V-Ray plugin instance name; empty selects the first match.
	int         subIndex      = 0;   // Cryptomatte layer index, or ObjectSelect 0=matte/1=filter/2=alpha.
);

SERIALIZE_MESSAGE(RendererGetImage,
	PARAM(renderElementType)
	PARAM(pluginInstanceName)
	PARAM(subIndex)
);


/// MsgRendererSetQuality
PROTO_MESSAGE(RendererSetQuality,
	int jpegQuality;
);

SERIALIZE_MESSAGE(RendererSetQuality,
	PARAM(jpegQuality)
);


/// MsgRendererSetCurrentCamera
PROTO_MESSAGE(RendererSetCurrentCamera,
	std::string cameraName;
);

SERIALIZE_MESSAGE(RendererSetCurrentCamera,
	PARAM(cameraName)
);


/// MsgRendererSetResumableRendering
PROTO_MESSAGE(RendererSetResumableRendering,
	bool        enabled;
	std::string outputFileName;
	int         autosaveSeconds;
	bool        deleteOnSuccess;
);

SERIALIZE_MESSAGE(RendererSetResumableRendering,
	PARAM(enabled) PARAM(outputFileName) PARAM(autosaveSeconds) PARAM(deleteOnSuccess)
);


/// MsgRendererSetCommitAction
PROTO_MESSAGE(RendererSetCommitAction,
	vray::CommitAction commitAction;
);

SERIALIZE_MESSAGE(RendererSetCommitAction,
	PARAM(commitAction)
);


/// MsgRendererSetVfbOptions
PROTO_MESSAGE(RendererSetVfbOptions,
	int vfbFlags;
);

SERIALIZE_MESSAGE(RendererSetVfbOptions,
	PARAM(vfbFlags)
);


/// MsgRendererSetViewportImageFormat
PROTO_MESSAGE(RendererSetViewportImageFormat,
	vray::AttrImage::ImageType format;
);

SERIALIZE_MESSAGE(RendererSetViewportImageFormat,
	PARAM(format)
);


/// MsgRendererSetRenderRegion
PROTO_MESSAGE(RendererSetRenderRegion,
	vray::AttrListInt coords;
);

SERIALIZE_MESSAGE(RendererSetRenderRegion,
	PARAM(coords)
);


/// MsgRendererSetCropRegion
PROTO_MESSAGE(RendererSetCropRegion,
	vray::AttrListInt coords;
);

SERIALIZE_MESSAGE(RendererSetCropRegion,
	PARAM(coords)
);


/// MsgRendererRenderSequence
PROTO_MESSAGE(RendererRenderSequence,
	vray::AttrListInt sequence;
	bool imageToBlender = true;
);

SERIALIZE_MESSAGE(RendererRenderSequence,
	PARAM(sequence)
	PARAM(imageToBlender)
);


/// MsgRendererContinueSequence
EMPTY_PROTO_MESSAGE(RendererContinueSequence);
SERIALIZE_EMPTY_MESSAGE(RendererContinueSequence);


/// MsgRendererOnChangeState
PROTO_MESSAGE(RendererOnChangeState,
	RendererState state;
	float renderProgress;
	std::string progressMessage;
	int lastRenderedFrame;
);

static SerializerStream& operator<< (SerializerStream& s, const MsgRendererOnChangeState&  msg) {
	s << msg.state;

	switch(msg.state){
		case RendererState::Progress:			s << msg.renderProgress; break;
		case RendererState::ProgressMessage:	s << msg.progressMessage; break;
		case RendererState::Continue:			s << msg.lastRenderedFrame; break;
		default:
			;
	}

	return s;
}

static DeserializerStream& operator>> (DeserializerStream& s, MsgRendererOnChangeState&  msg) {
	s >> msg.state;

	switch(msg.state){
	case RendererState::Progress:			s >> msg.renderProgress; break;
	case RendererState::ProgressMessage:	s >> msg.progressMessage; break;
	case RendererState::Continue:			s >> msg.lastRenderedFrame; break;
	default:
		;
	}

	return s;
}


/// MsgRendererOnAsyncOpComplete
PROTO_MESSAGE(RendererOnAsyncOpComplete,
	RendererAsyncOp operation;
	bool success;
	std::string message;
);

SERIALIZE_MESSAGE(RendererOnAsyncOpComplete,
	PARAM(operation)
	PARAM(success)
	PARAM(message)
);


/// MsgRendererOnProgress
PROTO_MESSAGE(RendererOnProgress,
	int elements;		// Render job completed elements
	int totalElements;	// Render job total elements
);

SERIALIZE_MESSAGE(RendererOnProgress,
	PARAM(elements)
	PARAM(totalElements)
);

/// MsgControlSetLogLevel
PROTO_MESSAGE(ControlSetLogLevel,
	int logLevel;
	bool enableQtLogs;
);

SERIALIZE_MESSAGE(ControlSetLogLevel,
	PARAM(logLevel)
	PARAM(enableQtLogs)
);


/// MsgControlOpenCollaboration
PROTO_MESSAGE(ControlOpenCollaboration,
	HostInfo hostInfo;
);

SERIALIZE_MESSAGE(ControlOpenCollaboration,
	PARAM(hostInfo)
);

/////////////////////// /////////////////////// /////////////////////// ///////////////////////
/////////////////////// /////////////////////// /////////////////////// ///////////////////////

/// MsgControlOnOpenCosmos
PROTO_MESSAGE(ControlOnOpenCosmos,
	int browserPage;
);

SERIALIZE_MESSAGE(ControlOnOpenCosmos,
	PARAM(browserPage)
);

/// MsgControlOnCosmosCalculateDownloadSize
PROTO_MESSAGE(ControlOnCosmosCalculateDownloadSize,
	vray::AttrListString packgeIds;
	vray::AttrListInt revisionIds;
	vray::AttrListString missingTextures;
);

SERIALIZE_MESSAGE(ControlOnCosmosCalculateDownloadSize,
	PARAM(packgeIds)
	PARAM(revisionIds)
	PARAM(missingTextures)
);

enum class CosmosRelinkStatus {
	AllAssetsValid,
	NotLoggedIn,
	RelinkOnly,
	DownloadAndRelink
};

/// MsgControlOnCosmosDownloadSize
PROTO_MESSAGE(ControlOnCosmosDownloadSize,
	int32_t downloadSizeMb;
	CosmosRelinkStatus relinkStatus;
);

SERIALIZE_MESSAGE(ControlOnCosmosDownloadSize,
	PARAM(downloadSizeMb)
	PARAM(relinkStatus)
);

/// MsgControlOnCosmosDownloadAssets
EMPTY_PROTO_MESSAGE(ControlOnCosmosDownloadAssets);
SERIALIZE_EMPTY_MESSAGE(ControlOnCosmosDownloadAssets);


enum class CosmosDownloadStatus {
	Cancelled = 0,
    Timeout = 1,
    Done = 2,
};

/// MsgControlOnCosmosDownloadedAssets
PROTO_MESSAGE(ControlOnCosmosDownloadedAssets,
	vray::AttrListString relinkedPaths;
	CosmosDownloadStatus downloadStatus;
);

SERIALIZE_MESSAGE(ControlOnCosmosDownloadedAssets,
	PARAM(relinkedPaths)
	PARAM(downloadStatus)
);

/// MsgControlOnUpdateScenePath
PROTO_MESSAGE(ControlOnUpdateScenePath,
	std::string scenePath;
);

SERIALIZE_MESSAGE(ControlOnUpdateScenePath,
	PARAM(scenePath)
);

/// MsgControlOnImportAsset
PROTO_MESSAGE(ControlOnImportAsset,
	ImportedAssetType assetType;
	vray::AttrListString assetNames;
	vray::AttrListString assetLocations;
	std::string materialFile;
	std::string objectFile;
	std::string lightFile;
	std::string packageId;
	uint32_t revisionId;
	bool isAnimated;
	// Plane dimensions in centimeters for ParallaxInterior assets; zero otherwise.
	double planeWidth;
	double planeHeight;
	// Whether the Cosmos browser requested wrapping material textures in TexTriPlanar.
	bool applyTriplanarMapping;
	// Real-world texture dimensions in centimeters supplied by Cosmos; zero when unknown.
	// Used to derive the triplanar size when applyTriplanarMapping is true.
	float texRealWorldWidth;
	float texRealWorldHeight;
);

SERIALIZE_MESSAGE(ControlOnImportAsset,
	PARAM(assetType)
	PARAM(assetNames)
	PARAM(assetLocations)
	PARAM(materialFile)
	PARAM(objectFile)
	PARAM(lightFile)
	PARAM(packageId)
	PARAM(revisionId)
	PARAM(isAnimated)
	PARAM(planeWidth)
	PARAM(planeHeight)
	PARAM(applyTriplanarMapping)
	PARAM(texRealWorldWidth)
	PARAM(texRealWorldHeight)
);

/// MsgControlOnScannedLicenseCheck
PROTO_MESSAGE(ControlOnScannedLicenseCheck,
	bool license;
);

SERIALIZE_MESSAGE(ControlOnScannedLicenseCheck,
	PARAM(license)
);

/// MsgControlOnScannedEncodeParameters
PROTO_MESSAGE(ControlOnScannedEncodeParameters,
	int materialId;
	std::string nodeName;
	std::string paramsJson;
);

SERIALIZE_MESSAGE(ControlOnScannedEncodeParameters,
	PARAM(materialId)
	PARAM(nodeName)
	PARAM(paramsJson)
);

/// MsgControlOnScannedEncodedParameters
PROTO_MESSAGE(ControlOnScannedEncodedParameters,
	int materialId;
	std::string nodeName;
	bool licensed;
	vray::AttrListInt encodedParams;
);

SERIALIZE_MESSAGE(ControlOnScannedEncodedParameters,
	PARAM(materialId)
	PARAM(nodeName)
	PARAM(licensed)
	PARAM(encodedParams)
);

/// MsgControlShowProductInfo
PROTO_MESSAGE(ControlShowUserDialog,
	std::string json;
);

SERIALIZE_MESSAGE(ControlShowUserDialog,
	PARAM(json)
);


/// MsgControlSetTelemetryState
PROTO_MESSAGE(ControlSetTelemetryState,
	bool anonymous;
	bool personalized;
);

SERIALIZE_MESSAGE(ControlSetTelemetryState,
	PARAM(anonymous)
	PARAM(personalized)
);


/// MsgControlSetVfbOnTop
PROTO_MESSAGE(ControlSetVfbOnTop,
	int onTopFlags;
);

SERIALIZE_MESSAGE(ControlSetVfbOnTop,
	PARAM(onTopFlags)
);



/// MsgControlUpdateVfbSettings
PROTO_MESSAGE(ControlUpdateVfbSettings,
	std::string vfbSettings;
);

SERIALIZE_MESSAGE(ControlUpdateVfbSettings,
	PARAM(vfbSettings)
);



/// MsgControlUpdateVfbLayers
PROTO_MESSAGE(ControlUpdateVfbLayers,
	std::string vfbLayersInfo;
);

SERIALIZE_MESSAGE(ControlUpdateVfbLayers,
	PARAM(vfbLayersInfo)
);


/// MsgControlOnRenderStatus
PROTO_MESSAGE(ControlOnRendererStatus,
	VRayStatusType status;
);

SERIALIZE_MESSAGE(ControlOnRendererStatus,
	PARAM(status)
);



/// MsgControlShowVfb
PROTO_MESSAGE(ControlShowVfb,
	bool show;
);

SERIALIZE_MESSAGE(ControlShowVfb,
	PARAM(show)
);


/// MsgControlResetVfbToolbar
EMPTY_PROTO_MESSAGE(ControlResetVfbToolbar);
SERIALIZE_EMPTY_MESSAGE(ControlResetVfbToolbar);

/// MsgControlClearVfbImage - clears the VFB image. Sent by the client on a new
/// scene load and at the start of IPR VFB / PROD renders without a render region
/// so the previous frame is wiped before fresh pixels arrive.
EMPTY_PROTO_MESSAGE(ControlClearVfbImage);
SERIALIZE_EMPTY_MESSAGE(ControlClearVfbImage);

/// MsgControlSetVfbRenderRegion - sets the VFB Render Region rectangle and toolbar
/// button state. When 'enabled' is false (or width/height are invalid), the render
/// region is cleared (renders the whole image) and the toolbar button is turned off.
/// imgWidth/imgHeight set the VFB image size at the same time so the region coords
/// are interpreted against a known canvas; pass <= 0 to leave the image size as-is.
PROTO_MESSAGE(ControlSetVfbRenderRegion,
	int  x;
	int  y;
	int  width;
	int  height;
	int  imgWidth;
	int  imgHeight;
	bool enabled;
);

SERIALIZE_MESSAGE(ControlSetVfbRenderRegion,
	PARAM(x)
	PARAM(y)
	PARAM(width)
	PARAM(height)
	PARAM(imgWidth)
	PARAM(imgHeight)
	PARAM(enabled)
);

/// MsgControlOnStartViewportRender
EMPTY_PROTO_MESSAGE(ControlOnStartViewportRender);
SERIALIZE_EMPTY_MESSAGE(ControlOnStartViewportRender);

/// MsgControlOnStartProductionRender
EMPTY_PROTO_MESSAGE(ControlOnStartProductionRender);
SERIALIZE_EMPTY_MESSAGE(ControlOnStartProductionRender);

/// MsgControlOnUpdateVfbSettings
PROTO_MESSAGE(ControlOnUpdateVfbSettings,
	std::string vfbSettings;
);

SERIALIZE_MESSAGE(ControlOnUpdateVfbSettings,
	PARAM(vfbSettings)
);


/// MsgControlOnLogMessage
PROTO_MESSAGE(ControlOnLogMessage,
	int logLevel;
	std::string logMessage;
);

SERIALIZE_MESSAGE(ControlOnLogMessage,
	PARAM(logLevel)
	PARAM(logMessage)
);


//ControlRequestComputeDevices
EMPTY_PROTO_MESSAGE(ControlGetComputeDevices);

SERIALIZE_EMPTY_MESSAGE(ControlGetComputeDevices);

PROTO_MESSAGE(ControlOnGetComputeDevices,
	ComputeDeviceType deviceType;
	vray::AttrListString computeDevices;
	vray::AttrListInt defaultDeviceStates;
);

SERIALIZE_MESSAGE(ControlOnGetComputeDevices,
	PARAM(deviceType)
	PARAM(computeDevices)
	PARAM(defaultDeviceStates)
);


PROTO_MESSAGE(ControlSetComputeDevices,
	vray::AttrListInt computeDeviceIndices;
	ComputeDeviceType deviceType;
);

SERIALIZE_MESSAGE(ControlSetComputeDevices,
	PARAM(computeDeviceIndices)
	PARAM(deviceType)
);

PROTO_MESSAGE(ControlSetUpdateAvailable,
	bool hasUpdate;
);

SERIALIZE_MESSAGE(ControlSetUpdateAvailable,
	PARAM(hasUpdate)
);

PROTO_MESSAGE(ControlLogVfbMessage,
	VfbMessageLevel level;
	std::string message;
);

SERIALIZE_MESSAGE(ControlLogVfbMessage,
	PARAM(level)
	PARAM(message)
);

PROTO_MESSAGE(ControlOnAutoUpdateCheckChanged,
	bool autoCheck;
);

SERIALIZE_MESSAGE(ControlOnAutoUpdateCheckChanged,
	PARAM(autoCheck)
);


EMPTY_PROTO_MESSAGE(ControlOnAppUpdateRequested);

SERIALIZE_EMPTY_MESSAGE(ControlOnAppUpdateRequested);


/// MsgControlOnSwitchLicenseToCommunity - fired when the user clicks the
/// "Switch to Community Edition" button in the no-license dialog. Tells
/// Blender to flip the community_edition preference and restart the server.
EMPTY_PROTO_MESSAGE(ControlOnSwitchLicenseToCommunity);

SERIALIZE_EMPTY_MESSAGE(ControlOnSwitchLicenseToCommunity);


/// Data for a single light mix change, mirrors VRay::LightMixChange
/// with plugin name instead of VRay::Plugin reference.
struct LightMixChangeData {
	std::string pluginName;
	float colorR = 1.f;
	float colorG = 1.f;
	float colorB = 1.f;
	float intensityMult = 1.f;
	bool enabled = true;
};

/// MsgControlOnLightMixTransferToScene
PROTO_MESSAGE(ControlOnLightMixTransferToScene,
	std::vector<LightMixChangeData> changes;
);

static SerializerStream& operator&& (SerializerStream& s, const MsgControlOnLightMixTransferToScene& msg) {
	s << static_cast<int>(msg.changes.size());
	for (const auto& c : msg.changes) {
		s << c.pluginName << c.colorR << c.colorG << c.colorB << c.intensityMult << c.enabled;
	}
	return s;
}

static DeserializerStream& operator&& (DeserializerStream& s, MsgControlOnLightMixTransferToScene& msg) {
	int count;
	s >> count;
	msg.changes.resize(count);
	for (auto& c : msg.changes) {
		s >> c.pluginName >> c.colorR >> c.colorG >> c.colorB >> c.intensityMult >> c.enabled;
	}
	return s;
}


/// VFB context menu action modes
enum class VFBMenuMode : int {
	SelectObject = 1,
	SelectMaterial = 2,
	SetFocusPoint = 3
};

/// MsgControlOnVFBMenu - sent when user selects a VFB context menu action
PROTO_MESSAGE(ControlOnVFBMenu,
	int mode;
	std::string targetName;      // Node plugin name (for object selection) or material plugin name
	std::string objectName;      // Node plugin name of the object owning the material (material selection only)
	double distance;
);

SERIALIZE_MESSAGE(ControlOnVFBMenu,
	PARAM(mode)
	PARAM(targetName)
	PARAM(objectName)
	PARAM(distance)
);


/// MsgControlOnAddRenderElementToScene
PROTO_MESSAGE(ControlOnAddRenderElementToScene,
	int renderElementType;
);

SERIALIZE_MESSAGE(ControlOnAddRenderElementToScene,
	PARAM(renderElementType)
);


/// MsgControlOnVFBShowMessagesWindow
EMPTY_PROTO_MESSAGE(ControlOnVFBShowMessagesWindow);
SERIALIZE_EMPTY_MESSAGE(ControlOnVFBShowMessagesWindow);


/// MsgControlOnVFBRenderRegionChanged - sent when the VFB render region changes
PROTO_MESSAGE(ControlOnVFBRenderRegionChanged,
	int  x;
	int  y;
	int  width;
	int  height;
	bool enabled;
);

SERIALIZE_MESSAGE(ControlOnVFBRenderRegionChanged,
	PARAM(x)
	PARAM(y)
	PARAM(width)
	PARAM(height)
	PARAM(enabled)
);


/// MsgControlSetVisualDebugger
PROTO_MESSAGE(ControlSetVisualDebugger,
	bool enable;
);

SERIALIZE_MESSAGE(ControlSetVisualDebugger,
	PARAM(enable)
);


};  // end VrayZmqWrapper namespace

#pragma warning (pop)


