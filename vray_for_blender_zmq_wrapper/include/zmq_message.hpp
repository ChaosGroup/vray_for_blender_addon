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
	PluginCreate = FirstPluginMessage,
	PluginRemove,
	PluginUpdate,
	PluginReplace,
	LastPluginMessage = PluginReplace,

	// Renderer messages
	FirstRendererMessage,
	RendererFree = FirstRendererMessage,
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
	LastRendererMessage = RendererContinueSequence,

	// Renderer events
	FirstRendererEvent,
	RendererOnVRayLog = FirstRendererEvent,
	RendererOnImage,
	RendererOnChangeState,
	RendererOnAsyncOpComplete,
	RendererOnProgress,
	LastRendererEvent = RendererOnProgress,

	// Control messages
	FirstControlMessage,
	ControlSetLogLevel = FirstControlMessage,
	ControlOpenCollaboration,

	// Cosmos
	ControlOnOpenCosmos,
	ControlOnCosmosCalculateDownloadSize,
	ControlOnCosmosDownloadSize,
	ControlOnCosmosDownloadAssets,
	ControlOnCosmosDownloadedAssets,
	ControlOnCosmosUpdateSceneName,

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
	ControlGetComputeDevices,
	ControlSetComputeDevices,
	LastControlMessage = ControlSetComputeDevices,

	// Control events
	FirstControlEvent,
	ControlOnStartViewportRender = FirstControlEvent,
	ControlOnStartProductionRender,
	ControlOnUpdateVfbSettings,
	ControlOnUpdateVfbLayers,
	ControlOnLogMessage,
	ControlOnImportAsset,
	ControlOnRendererStatus,
	ControlOnGetComputeDevices,
	LastControlEvent = ControlOnGetComputeDevices,

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
	ExportVrscene		// Export .vrscene
};


enum class ImportedAssetType : char {
	None,
	Material,
	VRMesh,
	HDRI,
	Extras
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
	LastDevice = Metal
};


// Structure describing render sequence
struct RenderSequenceDesc {
	int start = 1; // Starting frame
	int end = 1;   // Last frame
	int step = 1;  // Frame step
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
);

SERIALIZE_MESSAGE(RendererOnImage,
	PARAM(imageSet);
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
EMPTY_PROTO_MESSAGE(RendererStart);
SERIALIZE_EMPTY_MESSAGE(RendererStart);


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
	int renderElementType; // VRay::RenderElement::Type
);

SERIALIZE_MESSAGE(RendererGetImage,
	PARAM(renderElementType)
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
	RenderSequenceDesc description;
);

SERIALIZE_MESSAGE(RendererRenderSequence,
	PARAM(description)
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

/// MsgControlOnCosmosUpdateSceneName
PROTO_MESSAGE(ControlOnCosmosUpdateSceneName,
	std::string sceneName;
);

SERIALIZE_MESSAGE(ControlOnCosmosUpdateSceneName,
	PARAM(sceneName)
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


//ControlRequestComputeDevices,
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

};  // end VrayZmqWrapper namespace

#pragma warning (pop)


