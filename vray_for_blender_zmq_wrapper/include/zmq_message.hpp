#pragma once

#define ZMQ_BUILD_DRAFT_API

#include "cppzmq/zmq.hpp"
#include "base_types.h"
#include "zmq_serializer.hpp"
#include "zmq_deserializer.hpp"

namespace VrayZmqWrapper{

class VRayMessage {
public:
	enum class Type : char {
		None,
		Image,
		ChangePlugin,
		ChangeRenderer,
		VRayLog,
		VfbLayers,
		Control,
		ImportAsset
	};

	enum class PluginAction : char {
		None,
		Create,			// Create a new plugin
		Remove,			// Remove a plugin 
		Update,			// Update plugin property
		ForceUpdate,	// Update plugin property bypassing the property cache
		Replace			// Replace a plugin with a new instance
	};

	enum class RendererAction : char {
		None,
		Free,
		Start,
		Stop,
		Pause,
		Resume,
		Resize,
		Reset,
		Abort,
		_ArgumentRenderAction,
		Init,
		ResetsHosts,
		LoadScene,
		AppendScene,
		ExportScene,
		SetRenderMode,
		SetAnimationProperties,
		SetCurrentTime,
		SetCurrentFrame,
		ClearFrameValues,
		SetRendererState,
		GetImage,
		SetQuality,
		SetCurrentCamera,
		SetCommitAction,
		SetVfbOptions,
		SetViewportImageFormat,
		SetRenderRegion,
		SetCropRegion,
		RenderSequence,
	};


	enum class ControlAction : char {
		None		= 0,
		SetLogLevel,
		OpenCollaboration,
		OpenCosmos,
		ShowVfb,
		SetVfbOnTop,
		StartViewportRender,
		StartProductionRender,
		UpdateVfbSettings,
		UpdateVfbLayers,
		ShowUserDialog,
		LogMessage,
		SetTelemetryState,
		VRayStatus
	};

	enum class VRayStatusType : char {
		None = 0,
		MainRendererAvailable,
		MainRendererUnavailable,
		LicenseAcquired,
		LicenseDropped
		
	};

	enum class DRFlags : char {
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

	enum class ValueSetter : char {
		None,
		Default,
		AsString
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

	enum class ImportedAssetType : char {
		None,
		Material,
		VRMesh,
		HDRI
	};

	// Structure describing render sequence
	struct RenderSequenceDesc {
		int start = 1; // Starting frame
		int end = 1; // Last frame
		int step = 1; // Frame step
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

	struct AssetMetaData {
		ImportedAssetType type;
		std::string materialFile;
		std::string objectFile;
		std::string lightFile;
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
		std::string hostAppString;
		std::string filePath;
		std::vector<SubFileInfo> subFileInfo;
	};

	struct HostInfo {
		std::string vrayVersion;
		std::string buildVersion;
		std::string blenderVersion;
	};

	

	VRayMessage() = default;


	VRayMessage(VRayMessage&& other)
		: message(0)
		, type(other.type)
		, rendererAction(other.rendererAction)
		, rendererType(other.rendererType)
		, drFlags(other.drFlags)
		, rendererState(other.rendererState)
		, renderSize(other.renderSize)
		, valueSetter(other.valueSetter)
		, pluginAction(other.pluginAction)
		, pluginName(std::move(other.pluginName))
		, pluginType(std::move(other.pluginType))
		, pluginProperty(std::move(other.pluginProperty))
		, dialogJson(std::move(other.dialogJson))
		, telemetryAnonymousState(other.telemetryAnonymousState)
		, telemetryPersonalizedState(other.telemetryPersonalizedState)
		, controlAction(other.controlAction)
		, logLevel(other.logLevel)
		, vfbAlwaysOnTop(other.vfbAlwaysOnTop)
		, vfbSettings(std::move(other.vfbSettings))
		, vfbLayers(std::move(other.vfbLayers))
		, renderSequenceDesc(other.renderSequenceDesc)
		, value(other.value)
		, assetMetaData(std::move(other.assetMetaData))
		, hostInfo(std::move(other.hostInfo))
		, exportSettings(std::move(other.exportSettings))
		, vrayStatus(std::move(other.vrayStatus))
	{
		this->message.move(other.message);
	}

	/// Create message from data, usually to be sent
	VRayMessage(const char * data, int size)
	    : message(data, size)
	{}

	/// Create VRayMessage from zmq::message_t parsing the data
	static VRayMessage fromZmqMessage(zmq::message_t & message) {
		VRayMessage msg;
		msg.message.move(message);
		msg.parse();
		return msg;
	}

	static zmq::message_t fromData(const char * data, size_t size) {
		return zmq::message_t(data, size);
	}

	zmq::message_t & getInternalMessage() {
		return this->message;
	}

	const std::string getPluginNew() const {
		if (pluginAction == PluginAction::Replace && type == Type::ChangePlugin) {
			return value.as<VRayBaseTypes::AttrSimpleType<std::string>>();
		} else {
			assert((pluginAction == PluginAction::Replace && type == Type::ChangePlugin) && "Getting plugin new");
			return "";
		}
	}

	/// Get plugin property
	const std::string & getProperty() const {
		return pluginProperty;
	}

	/// Get the plugin instance id
	const std::string & getPlugin() const {
		return pluginName;
	}

	/// Get the plugin type name
	const std::string & getPluginType() const {
		return pluginType;
	}

	/// Get the message type
	Type getType() const {
		return type;
	}

	/// If type == ChangePlugin then get the plugin action
	PluginAction getPluginAction() const {
		return pluginAction;
	}

	/// If type == ChangeRenderer then get the renderer action
	RendererAction getRendererAction() const {
		return rendererAction;
	}

	/// If PluginAction will update plugin property check if it should be set as string
	ValueSetter getValueSetter() const {
		return valueSetter;
	}

	/// Get renderer type if renderer action is init
	RendererType getRendererType() const {
		return rendererType;
	}

	/// Get renderer statate for renderer action set renderer state
	RendererState getRendererState() const {
		return rendererState;
	}

	/// Get dr flags for renderer init renderer action
	DRFlags getDrFlags() const {
		return drFlags;
	}

	/// If type == Control then get the contol action
	ControlAction getControlAction() const {
		return controlAction;
	}

	/// Get logLevel for VRay log messages
	int getLogLevel() const {
		return logLevel;
	}

	/// Get vfbAlwaysOnTop for VRay log messages
	int getVfbAlwaysOnTop() const {
		return vfbAlwaysOnTop;
	}

	/// Get the JSON settings for showing a user dialog
	std::string getUserDialogJson() const {
		return dialogJson;
	}

	/// Get whether the anonymous telemetry should be enabled for the renderer
	int getTelemetryAnonymousState() const {
		return telemetryAnonymousState;
	}

	/// Get whether the personalized telemetry should be enabled for the renderer
	int getTelemetryPersonalizedState() const {
		return telemetryPersonalizedState;
	}

	/// Get vfbSettings for VRay log messages
	const std::string getVfbSettings() const {
		return vfbSettings;
	}

	const std::string getVfbLayers() const {
		return vfbLayers;
	}


	const std::string getLogMessage() const {
		return logMessage;
	}

	/// Get the renderer size for renderer action resize
	void getRenderSizes(VRayMessage::RenderSizes& sizes) const {
		sizes = renderSize;
	}

	RenderSequenceDesc getRenderSequenceDesc() const {
		return renderSequenceDesc;
	}

	const AssetMetaData& getAssetMetaData() const {
		return assetMetaData;
	}

	const HostInfo& getHostInfo() const {
		return hostInfo;
	}

	const VRayStatusType getVrayStatus() const {
		return vrayStatus;
	}

	const ExportSettings& getExportSettings() const {
		return exportSettings;
	}

	/// If message is update plugin param, get pointer to the internal param value
	template <typename T>
	const T * getValue() const {
		return value.asPtr<T>();
	}

	/// If message is update plugin param get the attr value object that stores the param value
	const VRayBaseTypes::AttrValue & getAttrValue() const {
		return value;
	}

	/// If message is update plugin param, get the value type
	VRayBaseTypes::ValueType getValueType() const {
		return value.type;
	}

	/// Static methods for creating messages
	///
	static zmq::message_t msgPluginCreate(const std::string & pluginName, const std::string & pluginType) {
		SerializerStream strm;
		strm << VRayMessage::Type::ChangePlugin << pluginName << PluginAction::Create << pluginType;
		return fromStream(strm);
	}

	static zmq::message_t msgPluginReplace(const std::string & pluginOld, const std::string & pluginNew) {
		VRayBaseTypes::AttrSimpleType<std::string> valWrapper(pluginNew);
		SerializerStream strm;
		strm << VRayMessage::Type::ChangePlugin << pluginOld << PluginAction::Replace << valWrapper.getType() << valWrapper;
		return fromStream(strm);
	}

	static zmq::message_t msgPluginRemove(const std::string & plugin) {
		SerializerStream strm;
		strm << VRayMessage::Type::ChangePlugin << plugin << PluginAction::Remove;
		return fromStream(strm);
	}

	/// Create a message to set a plugin property
	/// @param plugin - plugin ID
	/// @param property - property name
	/// @param value - new property value
	/// @param forceUpdate - force value update (bypass cache)
	template <typename T>
	static zmq::message_t msgPluginSetProperty(const std::string & plugin, const std::string & property, const T & value, bool forceUpdate) {
		using namespace std;

		auto actionType = forceUpdate ? PluginAction::ForceUpdate : PluginAction::Update;
		SerializerStream strm;
		strm << VRayMessage::Type::ChangePlugin << plugin << actionType << property << ValueSetter::Default << value.getType() << value;
		return fromStream(strm);
	}

	/// Create a message to set a plugin property
	/// @param plugin - plugin ID
	/// @param property - property name
	/// @param value - new property value
	/// @param forceUpdate - force value update (bypass cache)
	static zmq::message_t msgPluginSetProperty(const std::string & plugin, const std::string & property, const VRayBaseTypes::AttrValue & value, bool forceUpdate) {
		using namespace std;

		auto actionType = forceUpdate ? PluginAction::ForceUpdate : PluginAction::Update;
		SerializerStream strm;
		strm << VRayMessage::Type::ChangePlugin << plugin << actionType << property << ValueSetter::Default << value;
		return fromStream(strm);
	}

	/// Create a message to set a plugin property
	/// @param plugin - plugin ID
	/// @param property - property name
	/// @param value - new property value
	/// @param forceUpdate - force value update (bypass cache)
	static zmq::message_t msgPluginSetPropertyString(const std::string & plugin, const std::string & property, const std::string & value, bool forceUpdate) {
		using namespace std;

		auto actionType = forceUpdate ? PluginAction::ForceUpdate : PluginAction::Update;
		SerializerStream strm;
		strm << VRayMessage::Type::ChangePlugin << plugin << actionType << property
		     << ValueSetter::AsString << VRayBaseTypes::ValueType::ValueTypeString << value;
		return fromStream(strm);
	}

	static zmq::message_t msgImageSet(const VRayBaseTypes::AttrImageSet & value) {
		SerializerStream strm;
		strm << VRayMessage::Type::Image << value.getType() << value;
		return fromStream(strm);
	}

	static zmq::message_t msgVRayLog(int level, const std::string & log) {
		SerializerStream strm;
		VRayBaseTypes::AttrSimpleType<std::string> val(log);
		strm << VRayMessage::Type::VRayLog << level << val.getType() << log;
		return fromStream(strm);
	}

	static zmq::message_t msgVfbLayers(const std::string& layers) {
		SerializerStream strm;
		VRayBaseTypes::AttrSimpleType<std::string> val(layers);
		strm << VRayMessage::Type::VfbLayers << val.getType() << layers;
		return fromStream(strm);
	}

	/// Create message to control renderer
	static zmq::message_t msgRendererAction(RendererAction action) {
		assert(action < RendererAction::_ArgumentRenderAction && "Renderer action provided requires argument!");
		SerializerStream strm;
		strm << Type::ChangeRenderer << action;
		return fromStream(strm);
	}

	template <typename T>
	static zmq::message_t msgRendererAction(RendererAction action, const T & value) {
		assert(action > RendererAction::_ArgumentRenderAction && "Renderer action provided requires NO argument!");
		SerializerStream strm;
		VRayBaseTypes::AttrSimpleType<T> valWrapper(value);
		strm << Type::ChangeRenderer << action << valWrapper.getType() << valWrapper;
		return fromStream(strm);
	}

	static zmq::message_t msgRendererActionInit(RendererType type, DRFlags drFlags) {
		SerializerStream strm;
		const int value = static_cast<int>(drFlags) << static_cast<int>(DRFlags::_SerializationShift)
		                | static_cast<int>(type) << static_cast<int>(RendererType::_SerializationShift);
		return msgRendererAction(RendererAction::Init, value);
	}

	static zmq::message_t msgRendererAction(RendererAction action, const VRayBaseTypes::AttrListInt & value) {
		assert(action > RendererAction::_ArgumentRenderAction && "Renderer action provided requires NO argument!");
		SerializerStream strm;
		strm << Type::ChangeRenderer << action << value.getType() << value;
		return fromStream(strm);
	}

	// Creates message for starting of sequence rendering
	static zmq::message_t msgRendererActionRenderSequence(const int start, const int end, const int step) {
		assert(action > RendererAction::_ArgumentRenderAction && "Renderer action provided requires NO argument!");
		SerializerStream strm;
		strm << Type::ChangeRenderer << RendererAction::RenderSequence << start << end << step;

		return fromStream(strm);
	}

	// Creates message for exporting a .vrscene
	static zmq::message_t msgRendererActionExportScene(const ExportSettings& exportSettings) {
		assert(action > RendererAction::_ArgumentRenderAction && "Renderer action provided requires NO argument!");
		SerializerStream strm;
		strm << Type::ChangeRenderer << RendererAction::ExportScene 
				<< exportSettings.compressed
				<< exportSettings.hexArrays << exportSettings.hexTransforms
				<< exportSettings.hostAppString
				<< exportSettings.filePath
				<< exportSettings.subFileInfo.size();

				for ( const auto item : exportSettings.subFileInfo) {
					strm << item.fileNameSuffix << item.pluginType;
				}

		return fromStream(strm);
	}

	template <typename T>
	static zmq::message_t msgRendererState(RendererState state, const T & val) {
		VRayBaseTypes::AttrSimpleType<T> valWrapper(val);
		SerializerStream strm;
		strm << Type::ChangeRenderer << RendererAction::SetRendererState << state << valWrapper.getType() << valWrapper;
		return fromStream(strm);
	}

	static zmq::message_t msgRendererResize(const RenderSizes& renderSize) {
		SerializerStream strm;
		strm << Type::ChangeRenderer << RendererAction::Resize 
			 << renderSize.bitmask << renderSize.imgWidth << renderSize.imgHeight
			 << renderSize.bmpWidth << renderSize.bmpHeight
			 << renderSize.cropRgnLeft << renderSize.cropRgnTop << renderSize.cropRgnWidth << renderSize.cropRgnHeight
			 << renderSize.rgnLeft << renderSize.rgnTop << renderSize.rgnWidth << renderSize.rgnHeight;
		return fromStream(strm);
	}

	static zmq::message_t msgControlSetLogLevel(int level) {
		SerializerStream strm;
		strm << Type::Control << ControlAction::SetLogLevel << level;
		return fromStream(strm);
	}

	static zmq::message_t msgControlOpenCollaboration(const HostInfo& hostInfo) {
		SerializerStream strm;
		strm << Type::Control << ControlAction::OpenCollaboration 
				<< hostInfo.vrayVersion << hostInfo.buildVersion << hostInfo.blenderVersion;
		return fromStream(strm);
	}

	static zmq::message_t msgControlOpenCosmos() {
		SerializerStream strm;
		strm << Type::Control << ControlAction::OpenCosmos;
		return fromStream(strm);
	}

	static zmq::message_t msgControlShowVFB() {
		SerializerStream strm;
		strm << Type::Control << ControlAction::ShowVfb;
		return fromStream(strm);
	}

	static zmq::message_t msgControlShowUserDialog(const std::string& json) {
		SerializerStream strm;
		strm << Type::Control << ControlAction::ShowUserDialog << json;
		return fromStream(strm);
	}

	static zmq::message_t msgControlSetTelemetryState(bool anonymousState, bool personalizedState) {
		SerializerStream strm;
		strm << Type::Control << ControlAction::SetTelemetryState << anonymousState << personalizedState;
		return fromStream(strm);
	}

	static zmq::message_t msgControlSetVfbOnTop(bool alwaysOnTop) {
		SerializerStream strm;
		strm << Type::Control << ControlAction::SetVfbOnTop << alwaysOnTop;
		return fromStream(strm);
	}

	static zmq::message_t msgControlStartInteractiveRendering() {
		SerializerStream strm;
		strm << Type::Control << ControlAction::StartViewportRender;
		return fromStream(strm);
	}

	static zmq::message_t msgControlStartProductionRendering() {
		SerializerStream strm;
		strm << Type::Control << ControlAction::StartProductionRender;
		return fromStream(strm);
	}

	static zmq::message_t msgControlUpdateVfbSettings(const std::string &settings) {
		SerializerStream strm;
		strm << Type::Control << ControlAction::UpdateVfbSettings << settings;
		return fromStream(strm);
	}

	static zmq::message_t msgControlUpdateVfbLayers(const std::string& layers) {
		SerializerStream strm;
		strm << Type::Control << ControlAction::UpdateVfbLayers << layers;
		return fromStream(strm);
	}

	static zmq::message_t msgControlLogMessage(int logLevel, const std::string& message) {
		SerializerStream strm;
		strm << Type::Control << ControlAction::LogMessage << logLevel << message;
		return fromStream(strm);
	}

	static zmq::message_t msgControlVrayStatus(VRayStatusType status) {
		SerializerStream strm;
		strm << Type::Control << ControlAction::VRayStatus << status;
		return fromStream(strm);
	}

	static zmq::message_t msgAssetImport(
		ImportedAssetType assetType,
		const std::unordered_map<std::string, std::string>& assetLocationsMap,
		const std::string& materialFile,
		const std::string& objectFile="",
		const std::string& lightFile = "") {
		
		SerializerStream strm;

		VRayBaseTypes::AttrListString assetNames(static_cast<int>(assetLocationsMap.size()));
		VRayBaseTypes::AttrListString assetLocations(static_cast<int>(assetLocationsMap.size()));
		for (auto &asset : assetLocationsMap)
		{
			assetNames.append(asset.first);
			assetLocations.append(asset.second);
		}
		strm << Type::ImportAsset << assetType << assetNames << assetLocations << materialFile << objectFile << lightFile;
		return fromStream(strm);
	}

private:
	static zmq::message_t fromStream(SerializerStream & strm) {
		return fromData(strm.getData(), strm.getSize());
	}

	void parse() {
		using namespace VRayBaseTypes;

		DeserializerStream stream(reinterpret_cast<char*>(message.data()), message.size());
		stream >> type;

		if (type == Type::ChangePlugin) {
			stream >> pluginName >> pluginAction;
			
			switch (pluginAction){
				case PluginAction::Update:
				case PluginAction::ForceUpdate:
					stream >> pluginProperty >> valueSetter >> value;
					break;
				case PluginAction::Create:
					if (stream.hasMore()) {
						stream >> pluginType;
					}
					break;
				case PluginAction::Replace:
					assert(stream.hasMore() && "Missing new plugin for replace plugin");
					stream >> value;
					break;
				default:
					assert(!"Invalid message type");
			}
		} else if (type == Type::Image) {
			stream >> value;
		}
		else if (type == Type::VfbLayers) {
			stream >> value;
			assert(value.type == VRayBaseTypes::ValueTypeString && "Type::VfbLayers must be a string value");
		} else if (type == Type::VRayLog) {
			stream >> logLevel >> value;
			assert(value.type == VRayBaseTypes::ValueTypeString && "Type::VRayLog must be a string value");
		} else if (type == Type::ChangeRenderer) {
			stream >> rendererAction;
			if (rendererAction == RendererAction::Resize) {
				stream >> renderSize.bitmask >> renderSize.imgWidth >> renderSize.imgHeight
					>> renderSize.bmpWidth >> renderSize.bmpHeight
					>> renderSize.cropRgnLeft >> renderSize.cropRgnTop >> renderSize.cropRgnWidth >> renderSize.cropRgnHeight
					>> renderSize.rgnLeft >> renderSize.rgnTop >> renderSize.rgnWidth >> renderSize.rgnHeight;
			} else if (rendererAction == RendererAction::Init) {
				stream >> value;
				const int val = value.as<AttrSimpleType<int>>().value;
				drFlags = static_cast<DRFlags>((val >> static_cast<int>(DRFlags::_SerializationShift)) & 0xff);
				rendererType = static_cast<RendererType>((val >> static_cast<int>(RendererType::_SerializationShift)) & 0xff);
			} else if (rendererAction == RendererAction::SetRendererState) {
				stream >> rendererState >> value;
			} else if (rendererAction == RendererAction::RenderSequence) {
				stream >> renderSequenceDesc.start >> renderSequenceDesc.end >> renderSequenceDesc.step;
			} else if (rendererAction == RendererAction::ExportScene) {
				size_t numSubFiles = 0;

				stream  >> exportSettings.compressed 
						>> exportSettings.hexArrays >> exportSettings.hexTransforms
						>> exportSettings.hostAppString 
						>> exportSettings.filePath 
						>> numSubFiles;
						
				exportSettings.subFileInfo.resize(numSubFiles);
				for (size_t i = 0; i < numSubFiles; ++i) {
					stream  >> exportSettings.subFileInfo[i].fileNameSuffix
							>> exportSettings.subFileInfo[i].pluginType;
				}
			} else if (rendererAction > RendererAction::_ArgumentRenderAction) {
				stream >> value;
			}
		} else if (type == Type::Control) {
			stream >> controlAction;
			switch (controlAction){
				case ControlAction::SetLogLevel:      { stream >> logLevel; break; }
				case ControlAction::SetVfbOnTop:      { stream >> vfbAlwaysOnTop; break; }
				case ControlAction::ShowUserDialog:   { stream >> dialogJson; break; }
				case ControlAction::UpdateVfbSettings:{ stream >> vfbSettings; break; }
				case ControlAction::UpdateVfbLayers:  { stream >> vfbLayers; break;	}
				case ControlAction::LogMessage:		  { stream >> logLevel >> logMessage; break;	}
				case ControlAction::SetTelemetryState:{ stream >> telemetryAnonymousState >> telemetryPersonalizedState; break; }
				case ControlAction::OpenCollaboration:{ stream >> hostInfo.vrayVersion >> hostInfo.buildVersion >> hostInfo.blenderVersion; break; }
				case ControlAction::VRayStatus:		  { stream >> vrayStatus; break; }
			}
		}
		else if (type == Type::ImportAsset)
		{
			stream >> assetMetaData.type;
			stream >> assetMetaData.assetNames;
			stream >> assetMetaData.assetLocations;
			stream >> assetMetaData.materialFile;
			stream >> assetMetaData.objectFile;
			stream >> assetMetaData.lightFile;
		}
	}


	zmq::message_t            message;
	Type                      type				= Type::None;

	RendererAction            rendererAction	= RendererAction::None;
	RendererType              rendererType		= RendererType::None;
	DRFlags                   drFlags			= DRFlags::None;
	RendererState             rendererState		= RendererState::None;
	RenderSizes               renderSize;

	ValueSetter               valueSetter		= ValueSetter::None;

	PluginAction              pluginAction		= PluginAction::None;
	std::string               pluginName;
	std::string               pluginType;
	std::string               pluginProperty;

	ControlAction			  controlAction		= ControlAction::None;
	int                       logLevel			= 0;
	bool					  vfbAlwaysOnTop	= false;
	bool                      telemetryAnonymousState	= false;
	bool                      telemetryPersonalizedState = false;
	std::string				  dialogJson;	// Settings for showing a user dialog in JSON format
	std::string				  vfbSettings;
	std::string				  vfbLayers;
	std::string				  logMessage;

	RenderSequenceDesc		  renderSequenceDesc;

	VRayBaseTypes::AttrValue  value;
	AssetMetaData			  assetMetaData;
	HostInfo				  hostInfo;
	ExportSettings			  exportSettings;
	VRayStatusType			  vrayStatus;



private:
	VRayMessage(const VRayMessage&) = delete;
	VRayMessage& operator=(const VRayMessage&) = delete;
};


};  // end VrayZmqWrapper namespace 

