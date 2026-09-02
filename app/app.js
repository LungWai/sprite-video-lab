const MATTE_THRESHOLD_DEFAULTS = Object.freeze({
  chroma: 80,
  corridorkey: 35,
});
const CORRIDOR_THRESHOLD_DEFAULT_VERSION = 2;
const PREVIOUS_CORRIDOR_THRESHOLD_DEFAULT = 20;

const state = {
  upload: null,
  job: null,
  exportResult: null,
  processPreview: null,
  selected: new Set(),
  orderedSelectionMode: false,
  selectionOrder: [],
  segment: { start: 0, end: 0, startFrame: 1, endFrame: 1, confirmed: false },
  segmentPlaybackRafId: null,
  preview: {
    rafId: null,
    currentIndex: 0,
    isPlaying: true,
    isReversed: false,
    renderToken: 0,
    warmupToken: 0,
    imageCache: new Map(),
    background: "#F6FBF6",
  },
  magicPreview: null,
  magicInFlight: false,
  magicResizeMode: "hard",
  magicUseRealesrgan: true,
  magicVariantKeys: new Set(["half"]),
  processPreviewZoom: {
    source: 100,
    processed: 100,
  },
  processPreviewBackground: {
    mode: "checkerboard",
    color: "#F6FBF6",
  },
  processPreviewPan: {
    source: { x: 0, y: 0 },
    processed: { x: 0, y: 0 },
  },
  processPreviewDrag: null,
  instantChromaPreviewActive: false,
  matteThresholds: { ...MATTE_THRESHOLD_DEFAULTS },
  manualKeyColors: [],
  keySamplingActive: false,
  keySamplingReplacePrimary: false,
  keySampleMarkers: [],
};

const els = {};
const STORAGE_KEY = "sprite-video-lab-session-v2";
const SUPPORTED_VIDEO_EXTENSIONS = [".mp4", ".mov", ".mkv", ".webm", ".gif"];
const SUPPORTED_IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".bmp"];
const SUPPORTED_UPLOAD_EXTENSIONS = [...SUPPORTED_VIDEO_EXTENSIONS, ...SUPPORTED_IMAGE_EXTENSIONS];
const AI_RESOLUTION_MIN = 256;
const AI_RESOLUTION_MAX = 2560;
const AI_RESOLUTION_STEP = 32;
const AI_RESOLUTION_DEFAULT = 1024;
const AI_RESOLUTION_AUTO = "auto";
const AI_MODEL_AUTO = "birefnet-hr-matting";
const MAX_MANUAL_KEY_COLORS = 12;
const KEY_SAMPLE_DUPLICATE_DISTANCE = 6;
const MAGIC_VARIANT_CONFIGS = [
  {
    key: "full",
    label: "100%",
    panelId: "magicFullPreviewPanel",
    canvasId: "magicFullPreviewCanvas",
    emptyId: "magicFullPreviewEmptyState",
    frameLabelId: "magicFullPreviewFrameLabel",
    countId: "magicFullPreviewSelectedCount",
    progressFillId: "magicFullPreviewProgressFill",
    progressLabelId: "magicFullPreviewProgressLabel",
    sizeLabelId: "magicFullOutputSizeLabel",
    exportButtonId: "exportMagicFullFramesButton",
  },
  {
    key: "half",
    label: "1/2",
    panelId: "magicPreviewPanel",
    canvasId: "magicPreviewCanvas",
    emptyId: "magicPreviewEmptyState",
    frameLabelId: "magicPreviewFrameLabel",
    countId: "magicPreviewSelectedCount",
    progressFillId: "magicPreviewProgressFill",
    progressLabelId: "magicPreviewProgressLabel",
    sizeLabelId: "magicOutputSizeLabel",
    exportButtonId: "exportMagicFramesButton",
  },
  {
    key: "quarter",
    label: "1/4",
    panelId: "magicQuarterPreviewPanel",
    canvasId: "magicQuarterPreviewCanvas",
    emptyId: "magicQuarterPreviewEmptyState",
    frameLabelId: "magicQuarterPreviewFrameLabel",
    countId: "magicQuarterPreviewSelectedCount",
    progressFillId: "magicQuarterPreviewProgressFill",
    progressLabelId: "magicQuarterPreviewProgressLabel",
    sizeLabelId: "magicQuarterOutputSizeLabel",
    exportButtonId: "exportMagicQuarterFramesButton",
  },
  {
    key: "eighth",
    label: "1/8",
    panelId: "magicEighthPreviewPanel",
    canvasId: "magicEighthPreviewCanvas",
    emptyId: "magicEighthPreviewEmptyState",
    frameLabelId: "magicEighthPreviewFrameLabel",
    countId: "magicEighthPreviewSelectedCount",
    progressFillId: "magicEighthPreviewProgressFill",
    progressLabelId: "magicEighthPreviewProgressLabel",
    sizeLabelId: "magicEighthOutputSizeLabel",
    exportButtonId: "exportMagicEighthFramesButton",
  },
];
const MAGIC_RESIZE_MODE_LABELS = {
  hard: "硬",
  soft: "软",
};
let hotReloadVersion = null;
let hotReloadTimerId = null;
let uploadDragDepth = 0;
let skipSessionPersistence = false;
let lastAcceptedMatteMode = "chroma";
let aiModelInstallPromise = null;
let realesrganInstallPromise = null;
let preprocessSmoothingInstalling = false;
let chromaPreviewRafId = null;
let chromaPreviewCanvas = null;
let corridorPreviewTimerId = null;
let corridorPreviewInFlight = false;
let corridorPreviewPending = false;
let birefnetPreviewTimerId = null;
let birefnetPreviewInFlight = false;
let birefnetPreviewPending = false;

document.addEventListener("DOMContentLoaded", () => {
  bindElements();
  bindEvents();
  updatePreviewBackground(state.preview.background, false);
  updateProcessPreviewBackground(state.processPreviewBackground.mode, state.processPreviewBackground.color, false);
  syncManualColorLabel();
  renderManualKeySamples();
  updateChromaVisibility();
  normalizePreviewInterval();
  updatePreviewControls(0);
  drawPreviewPlaceholder();
  resetProcessPreview();
  updateSegmentConfirmationUI();
  showAnimationWorkbench();
  setStatus("\u7B49\u5F85\u5BFC\u5165\u7D20\u6750\u3002");
  void loadOutputPath();
  restoreSessionFromStorage();
  void validateRestoredPreprocessSmoothing();
  normalizeAiResolutionInput(false);
  lastAcceptedMatteMode = els.matteModeInput.value || "chroma";
  startHotReloadPolling();
  window.addEventListener("beforeunload", persistSession);
  window.addEventListener("scroll", scheduleWorkflowRailSync, { passive: true });
  window.addEventListener("resize", scheduleWorkflowRailSync);
  scheduleWorkflowRailSync();
});

let workflowRailFrame = null;

function syncWorkflowRail() {
  const items = Array.from(document.querySelectorAll(".rail-item"));
  const candidates = items
    .map((item) => ({ item, section: document.querySelector(item.getAttribute("href")) }))
    .filter(({ section }) => section && !section.hidden && section.offsetParent !== null);
  if (!candidates.length) return;
  const marker = window.scrollY + Math.min(window.innerHeight * 0.3, 180);
  const active = candidates.reduce((selected, candidate) => {
    const top = candidate.section.getBoundingClientRect().top + window.scrollY;
    return top <= marker ? candidate : selected;
  }, candidates[0]);
  items.forEach((item) => {
    const isActive = item === active.item;
    item.classList.toggle("active", isActive);
    if (isActive) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
}

function scheduleWorkflowRailSync() {
  if (workflowRailFrame !== null) return;
  workflowRailFrame = window.requestAnimationFrame(() => {
    workflowRailFrame = null;
    syncWorkflowRail();
  });
}

function bindElements() {
  [
    "pathInput",
    "importPathButton",
    "outputPathInput",
    "saveOutputPathButton",
    "clearRuntimeFilesButton",
    "uploadDropzone",
    "uploadInput",
    "videoName",
    "videoSize",
    "videoFps",
    "videoDuration",
    "previewPanel",
    "processPanel",
    "resultPanel",
    "videoPreview",
    "mediaPreviewImage",
    "videoWrap",
    "keySampleMarkers",
    "keySamplingOverlay",
    "videoProgress",
    "videoProgressFill",
    "videoProgressLabel",
    "videoToolbar",
    "currentTimeLabel",
    "startRange",
    "startInput",
    "startStepUpButton",
    "startStepDownButton",
    "endRange",
    "endInput",
    "endStepUpButton",
    "endStepDownButton",
    "segmentLength",
    "segmentConfirmStatus",
    "segmentConfirmHint",
    "keepEveryInput",
    "matteModeInput",
    "keyModeInput",
    "manualColorField",
    "manualKeyInput",
    "manualKeyLabel",
    "manualKeySampleCount",
    "manualKeySamples",
    "addPaletteKeyColorButton",
    "keySamplingToggleButton",
    "clearExtraKeySamplesButton",
    "thresholdInput",
    "thresholdValueLabel",
    "softnessInput",
    "despillInput",
    "haloInput",
    "birefnetEdgeShrinkInput",
    "corridorEnabledInput",
    "corridorCoarseMaskInput",
    "corridorScreenInput",
    "corridorColorSpaceInput",
    "corridorDespillInput",
    "corridorDespillValueLabel",
    "corridorRefinerInput",
    "corridorRefinerValueLabel",
    "corridorDespeckleEnabledInput",
    "corridorDespeckleSizeInput",
    "corridorGarbageEnabledInput",
    "corridorGarbagePixelsInput",
    "corridorKeySettings",
    "corridorSettingsSummaryValue",
    "corridorPreviewState",
    "birefnetEdgeShrinkValueLabel",
    "birefnetPreviewState",
    "aiModelInput",
    "aiDeviceInput",
    "aiResolutionInput",
    "lumaBlackInput",
    "lumaWhiteInput",
    "lumaGammaInput",
    "lumaStrengthInput",
    "lumaPolarityInput",
    "batchBackgroundToBlackInput",
    "batchBackgroundDesaturateInput",
    "batchSemiTransparentToBlackInput",
    "batchSemiTransparentToOpaqueInput",
    "preprocessEsrSmoothingInput",
    "aiLivePreviewOption",
    "aiLivePreviewInput",
    "watermarkRemovalInput",
    "previewFrameButton",
    "savePreviewButton",
    "processPreviewTimeLabel",
    "processPreviewKeyLabel",
    "previewSourceImage",
    "previewSourceEmpty",
    "previewSourceZoomInput",
    "previewSourceZoomLabel",
    "previewSourceZoomOutButton",
    "previewSourceZoomResetButton",
    "previewSourcePanResetButton",
    "previewSourceZoomInButton",
    "previewProcessedImage",
    "previewProcessedEmpty",
    "previewProcessedStage",
    "processPreviewBackgroundModeInput",
    "processPreviewBackgroundInput",
    "processPreviewBackgroundLabel",
    "processPreviewBackgroundColorRow",
    "previewProcessedZoomInput",
    "previewProcessedZoomLabel",
    "previewProcessedZoomOutButton",
    "previewProcessedZoomResetButton",
    "previewProcessedPanResetButton",
    "previewProcessedZoomInButton",
    "processStepShell",
    "processLockNote",
    "quickReferenceToggle",
    "quickReferencePanel",
    "processButton",
    "jobSummary",
    "selectionCount",
    "customAnimationInput",
    "customAnimationFolderInput",
    "clearPreviewFramesButton",
    "importAnimationButton",
    "importAnimationFolderButton",
    "openProcessedButton",
    "animationPreviewCanvas",
    "previewEmptyState",
    "previewFrameLabel",
    "previewSelectedCount",
    "previewProgressBar",
    "previewProgressFill",
    "previewProgressLabel",
    "previewPlayPauseButton",
    "previewRestartButton",
    "previewReverseInput",
    "previewBackgroundInput",
    "previewBackgroundLabel",
    "previewIntervalInput",
    "comparisonTitle",
    "scaleResultsState",
    "animationComparisonStrip",
    "originalVariantExportButton",
    "originalVariantExportOptions",
    "magicFullPreviewPanel",
    "magicFullPreviewCanvas",
    "magicFullPreviewEmptyState",
    "magicFullPreviewFrameLabel",
    "magicFullPreviewSelectedCount",
    "magicFullPreviewProgressFill",
    "magicFullPreviewProgressLabel",
    "magicFullOutputSizeLabel",
    "exportMagicFullFramesButton",
    "magicPreviewPanel",
    "magicPreviewCanvas",
    "magicPreviewEmptyState",
    "magicPreviewFrameLabel",
    "magicPreviewSelectedCount",
    "magicPreviewProgressBar",
    "magicPreviewProgressFill",
    "magicPreviewProgressLabel",
    "magicOutputSizeLabel",
    "exportMagicFramesButton",
    "magicQuarterPreviewPanel",
    "magicQuarterPreviewCanvas",
    "magicQuarterPreviewEmptyState",
    "magicQuarterPreviewFrameLabel",
    "magicQuarterPreviewSelectedCount",
    "magicQuarterPreviewProgressBar",
    "magicQuarterPreviewProgressFill",
    "magicQuarterPreviewProgressLabel",
    "magicQuarterOutputSizeLabel",
    "exportMagicQuarterFramesButton",
    "magicEighthPreviewPanel",
    "magicEighthPreviewCanvas",
    "magicEighthPreviewEmptyState",
    "magicEighthPreviewFrameLabel",
    "magicEighthPreviewSelectedCount",
    "magicEighthPreviewProgressBar",
    "magicEighthPreviewProgressFill",
    "magicEighthPreviewProgressLabel",
    "magicEighthOutputSizeLabel",
    "exportMagicEighthFramesButton",
    "frameGrid",
    "selectAllButton",
    "selectNoneButton",
    "selectOddButton",
    "selectEvenButton",
    "invertSelectionButton",
    "orderedSelectionInput",
    "exportButton",
    "exportOptions",
    "exportFramesButton",
    "exportSpriteSheetButton",
    "exportMovButton",
    "exportGifButton",
    "scaleProcessToggleButton",
    "scaleProcessingControls",
    "scaleVariantButtons",
    "magicUseRealesrganButton",
    "magicResizeHardButton",
    "magicResizeSoftButton",
    "scaleModeExplanation",
    "scaleProcessingHint",
    "magicButton",
    "exportResult",
    "appStatus",
  ].forEach((id) => {
    els[id] = document.getElementById(id);
  });
}

function normalizeMagicResizeMode(mode) {
  return mode === "soft" ? "soft" : "hard";
}

function magicResizeModeLabel(mode = state.magicResizeMode) {
  return MAGIC_RESIZE_MODE_LABELS[normalizeMagicResizeMode(mode)];
}

function setChoiceButtonState(button, selected) {
  if (!button) return;
  button.classList.toggle("is-selected", selected);
  button.setAttribute("aria-pressed", String(selected));
}

function markScaleResultsStale(message = "帧或参数已变化，点击“更新缩放处理”只补算差异。") {
  if (!state.magicPreview) return;
  state.magicPreview.stale = true;
  MAGIC_VARIANT_CONFIGS.forEach((config) => {
    const ui = magicVariantElements(config);
    if (ui.exportButton) ui.exportButton.disabled = true;
    const options = els.animationComparisonStrip?.querySelector(`[data-variant-export-options="${config.key}"]`);
    if (options) options.hidden = true;
  });
  if (els.scaleResultsState) {
    els.scaleResultsState.textContent = message;
  }
  if (els.magicButton) {
    els.magicButton.textContent = "更新缩放处理";
  }
}

function setMagicResizeMode(mode, { clearExisting = true } = {}) {
  const normalized = normalizeMagicResizeMode(mode);
  const changed = state.magicResizeMode !== normalized;
  state.magicResizeMode = normalized;
  setChoiceButtonState(els.magicResizeHardButton, normalized === "hard");
  setChoiceButtonState(els.magicResizeSoftButton, normalized === "soft");
  if (els.scaleModeExplanation) {
    els.scaleModeExplanation.textContent = normalized === "hard"
      ? "硬：最近邻缩小，像素边缘更利落；适合像素风和清晰硬边。"
      : "软：BOX 缩小，边缘更平滑、抗锯齿更明显；适合柔和插画和非像素素材。";
  }
  if (changed && clearExisting) {
    markScaleResultsStale();
  }
}

function setMagicUseRealesrgan(enabled, { clearExisting = true } = {}) {
  const next = Boolean(enabled);
  const changed = state.magicUseRealesrgan !== next;
  state.magicUseRealesrgan = next;
  setChoiceButtonState(els.magicUseRealesrganButton, next);
  const fullButton = els.scaleVariantButtons?.querySelector('[data-scale-variant="full"]');
  if (fullButton) {
    fullButton.disabled = !next;
    fullButton.title = next ? "ESR ×4 后缩回原尺寸" : "100% 必须启用 Real-ESRGAN";
  }
  if (!next && state.magicVariantKeys.has("full")) {
    state.magicVariantKeys.delete("full");
    if (state.magicVariantKeys.size === 0) state.magicVariantKeys.add("half");
    syncMagicVariantButtons();
    if (clearExisting) setStatus("已取消 100%：该版本必须先用 Real-ESRGAN 放大，再缩回原尺寸。");
  }
  if (changed && clearExisting) {
    markScaleResultsStale();
  }
}

function syncMagicVariantButtons() {
  els.scaleVariantButtons?.querySelectorAll("[data-scale-variant]").forEach((button) => {
    setChoiceButtonState(button, state.magicVariantKeys.has(button.dataset.scaleVariant));
  });
}

function toggleMagicVariant(key) {
  const normalized = MAGIC_VARIANT_CONFIGS.some((config) => config.key === key) ? key : "";
  if (!normalized) return;
  if (state.magicVariantKeys.has(normalized)) {
    if (state.magicVariantKeys.size === 1) {
      setStatus("至少保留一个输出尺寸。", "error");
      return;
    }
    state.magicVariantKeys.delete(normalized);
  } else {
    state.magicVariantKeys.add(normalized);
  }
  syncMagicVariantButtons();
  markScaleResultsStale();
  persistSession();
}

function toggleScaleProcessingControls() {
  const shouldExpand = els.scaleProcessingControls.hidden;
  els.scaleProcessingControls.hidden = !shouldExpand;
  els.scaleProcessToggleButton.setAttribute("aria-expanded", String(shouldExpand));
  els.scaleProcessToggleButton.textContent = shouldExpand ? "收起缩放处理" : "缩放处理";
  if (shouldExpand) {
    els.exportOptions.hidden = true;
    els.exportButton.setAttribute("aria-expanded", "false");
    els.exportButton.textContent = "直接导出";
  }
}

function toggleVariantExportOptions(variantKey) {
  const options = els.animationComparisonStrip.querySelector(`[data-variant-export-options="${variantKey}"]`);
  if (options) options.hidden = !options.hidden;
}

function handleVariantExportClick(event) {
  const originalButton = event.target.closest("[data-original-export]");
  if (originalButton) {
    void exportSelectedFormat(originalButton.dataset.originalExport, originalButton);
    return;
  }
  const variantButton = event.target.closest("[data-variant-export]");
  if (variantButton) {
    void exportMagicFrames(
      variantButton.dataset.variantKey,
      variantButton,
      variantButton.dataset.variantExport
    );
  }
}

function bindEvents() {
  els.importPathButton.addEventListener("click", importFromPath);
  els.saveOutputPathButton.addEventListener("click", selectOutputPath);
  els.clearRuntimeFilesButton.addEventListener("click", clearRuntimeFiles);
  els.uploadInput.addEventListener("change", handleUploadInputChange);
  els.preprocessEsrSmoothingInput.addEventListener("change", handlePreprocessSmoothingToggle);
  els.aiLivePreviewInput.addEventListener("change", handleAiLivePreviewToggle);
  els.previewFrameButton.addEventListener("click", () => previewCurrentFrame());
  els.savePreviewButton.addEventListener("click", downloadProcessPreviewResult);
  els.processButton.addEventListener("click", processVideo);
  els.quickReferenceToggle.addEventListener("click", () => {
    const shouldExpand = els.quickReferencePanel.hidden;
    els.quickReferencePanel.hidden = !shouldExpand;
    els.quickReferenceToggle.setAttribute("aria-expanded", String(shouldExpand));
  });
  els.exportButton.addEventListener("click", toggleExportOptions);
  els.exportFramesButton.addEventListener("click", () => exportSelectedFormat("frames", els.exportFramesButton));
  els.exportSpriteSheetButton.addEventListener("click", () => exportSelectedFormat("sprite_sheet", els.exportSpriteSheetButton));
  els.exportMovButton.addEventListener("click", () => exportSelectedFormat("mov", els.exportMovButton));
  els.exportGifButton.addEventListener("click", () => exportSelectedFormat("gif", els.exportGifButton));
  els.scaleProcessToggleButton.addEventListener("click", toggleScaleProcessingControls);
  els.magicButton.addEventListener("click", runMagicPreview);
  els.magicUseRealesrganButton.addEventListener("click", () => {
    setMagicUseRealesrgan(!state.magicUseRealesrgan);
    persistSession();
  });
  els.magicResizeHardButton.addEventListener("click", () => {
    setMagicResizeMode("hard");
    persistSession();
  });
  els.magicResizeSoftButton.addEventListener("click", () => {
    setMagicResizeMode("soft");
    persistSession();
  });
  els.scaleVariantButtons.addEventListener("click", (event) => {
    const button = event.target.closest("[data-scale-variant]");
    if (button) toggleMagicVariant(button.dataset.scaleVariant);
  });
  els.originalVariantExportButton.addEventListener("click", () => {
    els.originalVariantExportOptions.hidden = !els.originalVariantExportOptions.hidden;
  });
  els.animationComparisonStrip.addEventListener("click", handleVariantExportClick);
  setMagicUseRealesrgan(state.magicUseRealesrgan, { clearExisting: false });
  setMagicResizeMode(state.magicResizeMode, { clearExisting: false });
  syncMagicVariantButtons();
  els.exportMagicFullFramesButton.addEventListener("click", () => toggleVariantExportOptions("full"));
  els.exportMagicFramesButton.addEventListener("click", () => toggleVariantExportOptions("half"));
  els.exportMagicQuarterFramesButton.addEventListener("click", () => toggleVariantExportOptions("quarter"));
  els.exportMagicEighthFramesButton.addEventListener("click", () => toggleVariantExportOptions("eighth"));
  bindUploadDropzone();

  bindTimePair("start", els.startRange, els.startInput, els.startStepDownButton, els.startStepUpButton);
  bindTimePair("end", els.endRange, els.endInput, els.endStepDownButton, els.endStepUpButton);

  els.videoPreview.addEventListener("loadedmetadata", () => {
    if (!isVideoUpload()) {
      return;
    }
    muteVideoPreview();
    updateVideoProgress(state.segment.start || 0);
    restartSegmentPlayback({ autoplay: false });
  });

  els.videoPreview.addEventListener("timeupdate", () => {
    if (!isVideoUpload()) {
      return;
    }
    const current = els.videoPreview.currentTime || 0;
    els.currentTimeLabel.textContent = `\u5f53\u524d ${formatSeconds(current)}`;
    updateVideoProgress(current);
  });
  els.videoPreview.addEventListener("play", startSegmentPlaybackMonitor);
  els.videoPreview.addEventListener("pause", stopSegmentPlaybackMonitor);
  els.videoPreview.addEventListener("ended", () => restartSegmentPlayback({ autoplay: true }));
  els.videoPreview.addEventListener("click", toggleSourceVideoPlayback);

  els.manualKeyInput.addEventListener("input", syncManualColorLabel);
  els.manualKeyInput.addEventListener("change", addPaletteKeyColor);
  els.addPaletteKeyColorButton.addEventListener("click", () => els.manualKeyInput.click());
  els.thresholdInput.addEventListener("input", handleMatteToleranceInput);
  els.thresholdInput.addEventListener("change", handleMatteToleranceInput);
  els.keySamplingToggleButton.addEventListener("click", () => {
    setKeySamplingActive(!state.keySamplingActive);
  });
  els.clearExtraKeySamplesButton.addEventListener("click", clearExtraManualKeyColors);
  els.manualKeySamples.addEventListener("click", handleManualKeySampleClick);
  els.videoWrap.addEventListener("click", handleSourceKeySampleClick);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.keySamplingActive) {
      setKeySamplingActive(false);
    }
  });
  els.matteModeInput.addEventListener("change", handleMatteModeChange);
  els.keyModeInput.addEventListener("change", () => {
    updateChromaVisibility();
    if (els.keyModeInput.value !== "manual") {
      setKeySamplingActive(false, { announce: false });
    }
  });
  els.corridorEnabledInput.addEventListener("change", updateChromaVisibility);
  els.corridorCoarseMaskInput.addEventListener("change", handleCorridorCoarseMaskChange);
  els.corridorDespillInput.addEventListener("input", syncCorridorControlState);
  els.corridorRefinerInput.addEventListener("input", syncCorridorControlState);
  els.corridorDespeckleEnabledInput.addEventListener("change", syncCorridorControlState);
  els.corridorGarbageEnabledInput.addEventListener("change", syncCorridorControlState);
  els.corridorKeySettings.addEventListener("toggle", persistSession);
  [
    els.corridorScreenInput,
    els.corridorColorSpaceInput,
    els.corridorDespillInput,
    els.corridorRefinerInput,
    els.corridorDespeckleEnabledInput,
    els.corridorDespeckleSizeInput,
    els.corridorGarbageEnabledInput,
    els.corridorGarbagePixelsInput,
  ].forEach((element) => {
    const eventName = element.type === "range" ? "input" : "change";
    element.addEventListener(eventName, () => {
      syncCorridorControlState();
      markCorridorPreviewStale();
      scheduleCorridorLivePreview(element.type === "range" ? 220 : 80);
    });
  });

  els.frameGrid.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || target.type !== "checkbox") {
      return;
    }
    const index = Number(target.dataset.index);
    if (Number.isNaN(index)) {
      return;
    }
    setFrameSelected(index, target.checked);
    const selectedFrames = getSelectedFrames();
    if (target.checked) {
      const selectedPosition = selectedFrames.findIndex((frame) => frame.index === index);
      if (selectedPosition >= 0) state.preview.currentIndex = selectedPosition;
    } else {
      state.preview.currentIndex = Math.min(
        state.preview.currentIndex,
        Math.max(0, selectedFrames.length - 1)
      );
    }
    markScaleResultsStale();
    if (state.orderedSelectionMode) {
      renderFrames();
    } else {
      refreshCardSelection(index, target.checked);
      renderSelectionCount();
      syncAnimationPreview();
      syncResultActions();
      persistSession();
    }
  });

  els.selectAllButton.addEventListener("click", () => selectFrames(() => true));
  els.selectNoneButton.addEventListener("click", () => {
    state.selected = new Set();
    state.selectionOrder = [];
    state.preview.currentIndex = 0;
    markScaleResultsStale();
    renderFrames();
  });
  els.selectOddButton.addEventListener("click", () => selectFrames((frame) => (frame.index + 1) % 2 === 1));
  els.selectEvenButton.addEventListener("click", () => selectFrames((frame) => (frame.index + 1) % 2 === 0));
  els.invertSelectionButton.addEventListener("click", () => {
    if (!state.job) return;
    const next = new Set();
    state.job.frames.forEach((frame) => {
      if (!state.selected.has(frame.index)) {
        next.add(frame.index);
      }
    });
    state.selected = next;
    state.selectionOrder = state.job.frames.filter((frame) => next.has(frame.index)).map((frame) => frame.index);
    state.preview.currentIndex = 0;
    markScaleResultsStale();
    renderFrames();
  });
  els.orderedSelectionInput.addEventListener("change", () => {
    setOrderedSelectionMode(els.orderedSelectionInput.checked);
    normalizeSelectionOrder();
    state.preview.currentIndex = 0;
    markScaleResultsStale("帧顺序已变化，点击“更新缩放处理”会复用图像并更新顺序。");
    renderFrames();
  });
  els.clearPreviewFramesButton.addEventListener("click", clearPreviewFrames);
  els.importAnimationButton.addEventListener("click", () => els.customAnimationInput.click());
  els.importAnimationFolderButton.addEventListener("click", () => els.customAnimationFolderInput.click());
  els.customAnimationInput.addEventListener("change", async () => {
    await importCustomAnimationFrames(Array.from(els.customAnimationInput.files || []), els.importAnimationButton);
    els.customAnimationInput.value = "";
  });
  els.customAnimationFolderInput.addEventListener("change", async () => {
    await importCustomAnimationFrames(Array.from(els.customAnimationFolderInput.files || []), els.importAnimationFolderButton);
    els.customAnimationFolderInput.value = "";
  });

  els.openProcessedButton.addEventListener("click", async () => {
    if (state.job?.processed_dir) {
      await openPath(state.job.processed_dir);
    }
  });

  els.previewPlayPauseButton.addEventListener("click", togglePreviewPlayback);
  els.previewRestartButton.addEventListener("click", restartPreviewPlayback);
  els.previewReverseInput.addEventListener("change", () => {
    state.preview.isReversed = els.previewReverseInput.checked;
    state.preview.currentIndex = 0;
    markScaleResultsStale("播放与导出顺序已变化，点击“更新缩放处理”会复用图像并更新顺序。");
    syncAnimationPreview();
    persistSession();
  });
  els.previewBackgroundInput.addEventListener("input", () => {
    updatePreviewBackground(els.previewBackgroundInput.value, true);
    syncAnimationPreview(false);
  });
  els.processPreviewBackgroundModeInput.addEventListener("change", () => {
    updateProcessPreviewBackground(
      els.processPreviewBackgroundModeInput.value,
      state.processPreviewBackground.color,
      true
    );
  });
  els.processPreviewBackgroundInput.addEventListener("input", () => {
    updateProcessPreviewBackground("color", els.processPreviewBackgroundInput.value, true);
  });
  els.previewIntervalInput.addEventListener("change", () => {
    normalizePreviewInterval();
    restartPreviewTimer();
    persistSession();
  });
  bindProcessPreviewZoom("source");
  bindProcessPreviewZoom("processed");
  bindProcessPreviewPan("source");
  bindProcessPreviewPan("processed");
  window.addEventListener("resize", () => {
    applyProcessPreviewTransform("source");
    applyProcessPreviewTransform("processed");
  });

  [
    els.keepEveryInput,
    els.keyModeInput,
    els.manualKeyInput,
    els.thresholdInput,
    els.softnessInput,
    els.despillInput,
    els.haloInput,
    els.birefnetEdgeShrinkInput,
    els.corridorEnabledInput,
    els.corridorScreenInput,
    els.corridorColorSpaceInput,
    els.corridorDespillInput,
    els.corridorRefinerInput,
    els.corridorDespeckleEnabledInput,
    els.corridorDespeckleSizeInput,
    els.corridorGarbageEnabledInput,
    els.corridorGarbagePixelsInput,
    els.aiModelInput,
    els.aiDeviceInput,
    els.aiResolutionInput,
    els.lumaBlackInput,
    els.lumaWhiteInput,
    els.lumaGammaInput,
    els.lumaStrengthInput,
    els.lumaPolarityInput,
    els.batchBackgroundToBlackInput,
    els.batchBackgroundDesaturateInput,
    els.batchSemiTransparentToBlackInput,
    els.batchSemiTransparentToOpaqueInput,
    els.aiLivePreviewInput,
    els.watermarkRemovalInput,
    els.startInput,
    els.endInput,
  ].forEach((element) => {
    const eventName = element instanceof HTMLInputElement && element.type === "checkbox" ? "change" : "input";
    element.addEventListener(eventName, persistSession);
  });
}

function setPreprocessSmoothingInstalling(installing) {
  preprocessSmoothingInstalling = Boolean(installing);
  els.preprocessEsrSmoothingInput.disabled = preprocessSmoothingInstalling;
  updateSegmentConfirmationUI();
}

async function validateRestoredPreprocessSmoothing() {
  if (!els.preprocessEsrSmoothingInput.checked) {
    return;
  }
  setPreprocessSmoothingInstalling(true);
  try {
    const statusData = await apiJson("/api/realesrgan-status", {
      method: "POST",
      body: {},
    });
    if (!statusData.status?.installed) {
      els.preprocessEsrSmoothingInput.checked = false;
      setStatus("Real-ESRGAN 当前未安装。请重新勾选“先做平滑处理”，确认后会自动安装。", "error");
      persistSession();
    }
  } catch (error) {
    els.preprocessEsrSmoothingInput.checked = false;
    setStatus(`无法确认 Real-ESRGAN 安装状态：${error.message || String(error)}`, "error");
    persistSession();
  } finally {
    setPreprocessSmoothingInstalling(false);
  }
}

async function handlePreprocessSmoothingToggle() {
  if (els.preprocessEsrSmoothingInput.checked) {
    const confirmed = window.confirm(
      "“先做平滑处理”会在抠图前执行以下流程：\n\n" +
        "1. 每一帧先使用 Real-ESRGAN anime 放大 4 倍。\n" +
        "2. 再高质量缩回原始尺寸。\n" +
        "3. 最后才开始抠图。\n\n" +
        "用途：平滑原图锯齿与压缩噪点，让抠图边缘更连续。最终画布尺寸不会改变。\n\n" +
        "注意：预览和批处理都会明显变慢。若本机未安装 Real-ESRGAN，确认后会自动下载并安装官方 Windows 便携包。安装完成前，预览和批处理按钮都不可用。\n\n" +
        "确认启用吗？"
    );
    if (!confirmed) {
      els.preprocessEsrSmoothingInput.checked = false;
      setStatus("已取消先做平滑处理，仍按原始帧直接抠图。");
      persistSession();
      return;
    }

    if (realesrganInstallPromise) {
      await realesrganInstallPromise;
      return;
    }

    setPreprocessSmoothingInstalling(true);
    realesrganInstallPromise = (async () => {
      try {
        setStatus("正在检查 Real-ESRGAN 安装状态，完成前不能预览或批处理...");
        const statusData = await apiJson("/api/realesrgan-status", {
          method: "POST",
          body: {},
        });
        if (!statusData.status?.installed) {
          setStatus("正在自动下载并安装 Real-ESRGAN，完成前不能预览或批处理...");
          const installData = await apiJson("/api/install-realesrgan", {
            method: "POST",
            body: { confirmed: true },
          });
          if (!installData.result?.status?.installed) {
            throw new Error("Real-ESRGAN 安装未完成，请重试。");
          }
        }
        setStatus("Real-ESRGAN 已就绪，已启用：ESR x4 → 缩回原尺寸 → 抠图。", "success");
        return true;
      } catch (error) {
        els.preprocessEsrSmoothingInput.checked = false;
        setStatus(`Real-ESRGAN 自动安装失败：${error.message || String(error)}`, "error");
        return false;
      }
    })();

    try {
      await realesrganInstallPromise;
    } finally {
      realesrganInstallPromise = null;
      setPreprocessSmoothingInstalling(false);
      resetProcessPreview();
      persistSession();
    }
    return;
  } else {
    setStatus("已关闭先做平滑处理，恢复为原始帧直接抠图。");
  }
  resetProcessPreview();
  persistSession();
}

function bindTimePair(key, rangeEl, numberEl, decreaseButton, increaseButton) {
  const frameKey = key === "start" ? "startFrame" : "endFrame";
  const applySegmentFrame = (nextFrame) => {
    state.segment[frameKey] = clampSegmentFrame(nextFrame);
    normalizeSegment(key);
    renderSegmentControls();
    updateSegmentConfirmationUI();
    restartSegmentPlayback({ autoplay: true });
    persistSession();
  };

  const handler = (event) => {
    const nextValue = Number(event.target.value);
    if (Number.isNaN(nextValue)) {
      return;
    }
    applySegmentFrame(nextValue);
  };

  rangeEl.addEventListener("input", handler);
  numberEl.addEventListener("input", handler);
  numberEl.addEventListener("change", handler);
  decreaseButton.addEventListener("click", () => {
    applySegmentFrame(state.segment[frameKey] - 1);
  });
  increaseButton.addEventListener("click", () => {
    applySegmentFrame(state.segment[frameKey] + 1);
  });
  numberEl.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") {
      return;
    }
    event.preventDefault();
    const direction = event.key === "ArrowUp" ? 1 : -1;
    applySegmentFrame(state.segment[frameKey] + direction);
  });
}

function bindProcessPreviewZoom(kind) {
  const { input, decreaseButton, zoomResetButton, panResetButton, increaseButton } = getProcessPreviewElements(kind);

  input.addEventListener("input", () => {
    updateProcessPreviewZoom(kind, Number(input.value || 100), true);
  });
  decreaseButton.addEventListener("click", () => {
    updateProcessPreviewZoom(kind, state.processPreviewZoom[kind] - 10, true);
  });
  zoomResetButton.addEventListener("click", () => {
    resetProcessPreviewZoom(kind, true);
  });
  panResetButton.addEventListener("click", () => {
    resetProcessPreviewPan(kind, true);
  });
  increaseButton.addEventListener("click", () => {
    updateProcessPreviewZoom(kind, state.processPreviewZoom[kind] + 10, true);
  });
}

function bindProcessPreviewPan(kind) {
  const { image, stage } = getProcessPreviewElements(kind);
  if (!stage) {
    return;
  }

  image.addEventListener("load", () => {
    applyProcessPreviewTransform(kind);
  });

  stage.addEventListener("pointerdown", (event) => {
    if (image.hidden || !image.getAttribute("src")) {
      return;
    }
    if (event.button != null && event.button !== 0) {
      return;
    }

    const pan = state.processPreviewPan[kind] || { x: 0, y: 0 };
    state.processPreviewDrag = {
      kind,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startPanX: pan.x,
      startPanY: pan.y,
    };
    stage.classList.add("dragging");
    if (typeof stage.setPointerCapture === "function") {
      stage.setPointerCapture(event.pointerId);
    }
    event.preventDefault();
  });

  stage.addEventListener("pointermove", (event) => {
    const drag = state.processPreviewDrag;
    if (!drag || drag.kind !== kind || drag.pointerId !== event.pointerId) {
      return;
    }

    updateProcessPreviewPan(
      kind,
      drag.startPanX + event.clientX - drag.startX,
      drag.startPanY + event.clientY - drag.startY
    );
    event.preventDefault();
  });

  const endDrag = (event) => {
    const drag = state.processPreviewDrag;
    if (!drag || drag.kind !== kind || drag.pointerId !== event.pointerId) {
      return;
    }

    state.processPreviewDrag = null;
    stage.classList.remove("dragging");
    if (typeof stage.hasPointerCapture === "function" && stage.hasPointerCapture(event.pointerId)) {
      stage.releasePointerCapture(event.pointerId);
    }
  };

  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);
  stage.addEventListener("lostpointercapture", endDrag);
  stage.addEventListener("dblclick", () => {
    updateProcessPreviewPan(kind, 0, 0);
  });
}

function getProcessPreviewElements(kind) {
  const isSource = kind === "source";
  const image = isSource ? els.previewSourceImage : els.previewProcessedImage;
  return {
    input: isSource ? els.previewSourceZoomInput : els.previewProcessedZoomInput,
    label: isSource ? els.previewSourceZoomLabel : els.previewProcessedZoomLabel,
    image,
    stage: image?.closest(".image-preview-stage") || null,
    decreaseButton: isSource ? els.previewSourceZoomOutButton : els.previewProcessedZoomOutButton,
    zoomResetButton: isSource ? els.previewSourceZoomResetButton : els.previewProcessedZoomResetButton,
    panResetButton: isSource ? els.previewSourcePanResetButton : els.previewProcessedPanResetButton,
    increaseButton: isSource ? els.previewSourceZoomInButton : els.previewProcessedZoomInButton,
  };
}

function updateProcessPreviewZoom(kind, value, shouldPersist = false) {
  const normalized = clamp(Math.round(value / 10) * 10, 50, 800);
  state.processPreviewZoom[kind] = normalized;

  const { input, label } = getProcessPreviewElements(kind);

  input.value = String(normalized);
  label.textContent = `${normalized}%`;
  applyProcessPreviewTransform(kind);

  if (shouldPersist) {
    persistSession();
  }
}

function updateProcessPreviewPan(kind, x, y) {
  state.processPreviewPan[kind] = normalizeProcessPreviewPan(x, y);
  applyProcessPreviewTransform(kind);
}

function normalizeProcessPreviewPan(x, y) {
  const panX = Number.isFinite(Number(x)) ? Number(x) : 0;
  const panY = Number.isFinite(Number(y)) ? Number(y) : 0;
  return { x: panX, y: panY };
}

function applyProcessPreviewTransform(kind) {
  const { image } = getProcessPreviewElements(kind);
  if (!image) {
    return;
  }
  const pan = state.processPreviewPan[kind] || { x: 0, y: 0 };
  const scale = state.processPreviewZoom[kind] / 100;
  image.style.transform = `translate3d(${pan.x}px, ${pan.y}px, 0) scale(${scale})`;
}

function resetProcessPreviewPan(kind, shouldPersist = false) {
  state.processPreviewPan[kind] = { x: 0, y: 0 };
  applyProcessPreviewTransform(kind);
  if (shouldPersist) {
    persistSession();
  }
}

function resetProcessPreviewZoom(kind, shouldPersist = false) {
  updateProcessPreviewZoom(kind, 100, false);
  if (shouldPersist) {
    persistSession();
  }
}

function resetProcessPreviewView(kind, shouldPersist = false) {
  resetProcessPreviewZoom(kind, false);
  resetProcessPreviewPan(kind, false);
  if (shouldPersist) {
    persistSession();
  }
}

function setProcessPreviewStageActive(kind, isActive) {
  const { stage } = getProcessPreviewElements(kind);
  if (!stage) {
    return;
  }
  stage.classList.toggle("is-pannable", isActive);
  if (!isActive) {
    stage.classList.remove("dragging");
  }
}

function currentMatteMode() {
  return els.matteModeInput.value || "chroma";
}

function matteModeUsesBiRefNet(mode) {
  return mode === "birefnet";
}

function matteModeUsesCorridorKey(mode) {
  return mode === "corridorkey" || mode === "corridorkey_blue";
}

function corridorScreenForMatteMode(mode) {
  return mode === "corridorkey_blue" ? "blue" : "green";
}

function processingMatteMode(mode) {
  return matteModeUsesCorridorKey(mode) ? "corridorkey" : mode;
}

function syncCorridorScreenFromMatteMode(mode) {
  if (matteModeUsesCorridorKey(mode)) {
    els.corridorScreenInput.value = corridorScreenForMatteMode(mode);
  }
}

function matteModeUsesLuma(mode) {
  return mode === "luma";
}

function matteModeUsesChromaSeed(mode, corridorkeyCoarseMask = els.corridorCoarseMaskInput.value) {
  return mode === "chroma" || (matteModeUsesCorridorKey(mode) && corridorkeyCoarseMask !== "birefnet");
}

function matteThresholdStorageMode(mode, corridorkeyCoarseMask = els.corridorCoarseMaskInput.value) {
  return matteModeUsesChromaSeed(mode, corridorkeyCoarseMask) ? "chroma" : processingMatteMode(mode);
}

function matteModeRequiresAiModel(mode) {
  return matteModeUsesBiRefNet(mode) || matteModeUsesCorridorKey(mode);
}

function aiModelRequestPayload(mode) {
  return {
    matte_mode: processingMatteMode(mode),
    ai_model: els.aiModelInput.value,
    ai_device: els.aiDeviceInput.value,
    corridorkey_coarse_mask: els.corridorCoarseMaskInput.value,
    corridorkey_screen: els.corridorScreenInput.value,
  };
}

async function ensureAiModelsReady(mode) {
  if (!matteModeRequiresAiModel(mode)) {
    return true;
  }
  if (aiModelInstallPromise) {
    return aiModelInstallPromise;
  }

  aiModelInstallPromise = (async () => {
    const payload = aiModelRequestPayload(mode);
    try {
      const statusData = await apiJson("/api/ai-model-status", {
        method: "POST",
        body: payload,
      });
      if (statusData.status?.installed) {
        return true;
      }

      const confirmed = window.confirm(
        "\u8BE5\u62A0\u56FE\u65B9\u6CD5\u9700\u8981\u4E0B\u8F7D\u5E76\u5B89\u88C5 AI \u6A21\u578B\uFF0C\u6587\u4EF6\u8F83\u5927\u4E14\u9700\u8981\u8054\u7F51\u3002\u786E\u8BA4\u73B0\u5728\u5B89\u88C5\u5417\uFF1F"
      );
      if (!confirmed) {
        setStatus("\u5DF2\u53D6\u6D88 AI \u6A21\u578B\u5B89\u88C5\uFF0C\u672A\u4E0B\u8F7D\u4EFB\u4F55\u6A21\u578B\u6587\u4EF6\u3002", "error");
        return false;
      }

      setStatus("\u6B63\u5728\u5B89\u88C5 AI \u6A21\u578B\uFF0C\u9996\u6B21\u4E0B\u8F7D\u53EF\u80FD\u9700\u8981\u51E0\u5206\u949F\uFF0C\u8BF7\u52FF\u5173\u95ED\u9875\u9762\u3002");
      const installData = await apiJson("/api/install-ai-model", {
        method: "POST",
        body: { ...payload, confirmed: true },
      });
      if (!installData.result?.status?.installed) {
        throw new Error("AI \u6A21\u578B\u5B89\u88C5\u672A\u5B8C\u6210\uFF0C\u8BF7\u91CD\u8BD5\u3002");
      }
      setStatus("AI \u6A21\u578B\u5B89\u88C5\u5B8C\u6210\uFF0C\u53EF\u4EE5\u5F00\u59CB\u9884\u89C8\u6216\u5904\u7406\u3002", "success");
      return true;
    } catch (error) {
      setStatus(`AI \u6A21\u578B\u5B89\u88C5\u5931\u8D25\uFF1A${error.message || String(error)}`, "error");
      return false;
    }
  })();

  try {
    return await aiModelInstallPromise;
  } finally {
    aiModelInstallPromise = null;
  }
}

async function handleMatteModeChange() {
  const selectedMode = els.matteModeInput.value || "chroma";
  rememberMatteThreshold(lastAcceptedMatteMode);
  syncCorridorScreenFromMatteMode(selectedMode);
  applyMatteThreshold(selectedMode);
  updateChromaVisibility();
  els.matteModeInput.disabled = true;
  const ready = await ensureAiModelsReady(selectedMode);
  if (ready) {
    lastAcceptedMatteMode = selectedMode;
    persistSession();
    if (state.upload) {
      if (matteModeUsesCorridorKey(selectedMode)) {
        scheduleCorridorLivePreview(0);
      } else if (selectedMode === "birefnet") {
        scheduleBirefnetLivePreview(0);
      }
    }
  } else {
    els.matteModeInput.value = lastAcceptedMatteMode;
    syncCorridorScreenFromMatteMode(lastAcceptedMatteMode);
    applyMatteThreshold(lastAcceptedMatteMode);
    updateChromaVisibility();
    persistSession();
  }
  els.matteModeInput.disabled = false;
}

async function handleCorridorCoarseMaskChange() {
  els.corridorCoarseMaskInput.value =
    els.corridorCoarseMaskInput.value === "birefnet" ? "birefnet" : "chroma";
  applyMatteThreshold(currentMatteMode());
  updateChromaVisibility();
  els.corridorCoarseMaskInput.disabled = true;
  const ready = await ensureAiModelsReady(currentMatteMode());
  if (!ready) {
    els.corridorCoarseMaskInput.value = "chroma";
    applyMatteThreshold(currentMatteMode());
    updateChromaVisibility();
  }
  els.corridorCoarseMaskInput.disabled = false;
  persistSession();
  markCorridorPreviewStale();
  scheduleCorridorLivePreview(0);
}

function aiLivePreviewEnabled() {
  return Boolean(els.aiLivePreviewInput?.checked);
}

function cancelAiLivePreviewTimers() {
  window.clearTimeout(birefnetPreviewTimerId);
  window.clearTimeout(corridorPreviewTimerId);
  birefnetPreviewTimerId = null;
  corridorPreviewTimerId = null;
  birefnetPreviewPending = false;
  corridorPreviewPending = false;
}

function handleAiLivePreviewToggle() {
  cancelAiLivePreviewTimers();
  if (!aiLivePreviewEnabled() || !state.upload) {
    return;
  }
  if (matteModeUsesCorridorKey(currentMatteMode())) {
    scheduleCorridorLivePreview(0);
  } else if (currentMatteMode() === "birefnet") {
    scheduleBirefnetLivePreview(0);
  }
}

function currentUsesCorridorKey() {
  return matteModeUsesCorridorKey(currentMatteMode());
}

function normalizeAiResolution(value) {
  const rawText = String(value ?? "").trim().toLowerCase();
  if (!rawText || rawText === AI_RESOLUTION_AUTO) {
    return AI_RESOLUTION_AUTO;
  }
  const numeric = Number(value);
  const raw = Number.isFinite(numeric) ? numeric : AI_RESOLUTION_DEFAULT;
  const clamped = clamp(Math.round(raw), AI_RESOLUTION_MIN, AI_RESOLUTION_MAX);
  const aligned = Math.floor((clamped + AI_RESOLUTION_STEP / 2) / AI_RESOLUTION_STEP) * AI_RESOLUTION_STEP;
  return clamp(aligned, AI_RESOLUTION_MIN, AI_RESOLUTION_MAX);
}

function ensureAiResolutionOption(value) {
  const normalizedValue = String(value);
  if ([...els.aiResolutionInput.options].some((option) => option.value === normalizedValue)) {
    return;
  }
  const option = document.createElement("option");
  option.value = normalizedValue;
  option.textContent = `${normalizedValue} px`;
  els.aiResolutionInput.appendChild(option);
}

function setAiResolutionValue(value) {
  const normalized = normalizeAiResolution(value);
  ensureAiResolutionOption(normalized);
  els.aiResolutionInput.value = String(normalized);
}

function normalizeAiResolutionInput(shouldPersist = true) {
  const normalized = normalizeAiResolution(els.aiResolutionInput.value);
  setAiResolutionValue(normalized);
  if (shouldPersist) {
    persistSession();
  }
}

function applyAutomaticMatteDefaults() {
  state.matteThresholds = { ...MATTE_THRESHOLD_DEFAULTS };
  applyMatteThreshold(currentMatteMode());
  els.softnessInput.value = "16";
  els.despillInput.value = "0.6";
  els.haloInput.value = "1";
  els.lumaBlackInput.value = "0";
  els.lumaWhiteInput.value = "85";
  els.lumaGammaInput.value = "0.55";
  els.lumaStrengthInput.value = "1.7";
}

function collectFormState() {
  rememberMatteThreshold(currentMatteMode());
  syncCorridorScreenFromMatteMode(currentMatteMode());
  return {
    keep_every: Number(els.keepEveryInput.value || 1),
    output_scale: 1,
    canvas_mode: "auto",
    reduce_px: 0,
    chroma_enabled: true,
    matte_mode: currentMatteMode(),
    key_mode: els.keyModeInput.value,
    manual_key_hex: els.manualKeyInput.value,
    manual_key_colors: [...state.manualKeyColors],
    manual_key_colors_explicit: true,
    threshold: Number(els.thresholdInput.value || 0),
    chroma_threshold: state.matteThresholds.chroma,
    corridorkey_threshold: state.matteThresholds.corridorkey,
    corridorkey_threshold_default_version: CORRIDOR_THRESHOLD_DEFAULT_VERSION,
    softness: Number(els.softnessInput.value === "" ? 1 : els.softnessInput.value),
    despill_strength: Number(els.despillInput.value || 0),
    halo_pixels: currentMatteMode() === "birefnet" ? 0 : Number(els.haloInput.value || 0),
    birefnet_edge_shrink: 0,
    corridorkey_enabled: currentUsesCorridorKey(),
    corridorkey_coarse_mask: els.corridorCoarseMaskInput.value,
    corridorkey_screen: els.corridorScreenInput.value,
    corridorkey_color_space: "srgb",
    corridorkey_despill_strength: Number(els.corridorDespillInput.value || 0),
    corridorkey_refiner_scale: Number(els.corridorRefinerInput.value || 0),
    corridorkey_despeckle_enabled: els.corridorDespeckleEnabledInput.checked,
    corridorkey_despeckle_size: Number(els.corridorDespeckleSizeInput.value || 0),
    corridorkey_garbage_matte_enabled: els.corridorGarbageEnabledInput.checked,
    corridorkey_garbage_matte_px: Number(els.corridorGarbagePixelsInput.value || 20),
    corridorkey_settings_open: els.corridorKeySettings.open,
    ai_model: AI_MODEL_AUTO,
    ai_device: els.aiDeviceInput.value,
    ai_resolution: AI_RESOLUTION_AUTO,
    ai_resolution_mode: "auto",
    luma_black: Number(els.lumaBlackInput.value || 0),
    luma_white: Number(els.lumaWhiteInput.value || 85),
    luma_gamma: Number(els.lumaGammaInput.value || 0.55),
    luma_strength: Number(els.lumaStrengthInput.value || 1.7),
    luma_polarity: els.lumaPolarityInput.value || "auto",
    preprocess_esr_smoothing: els.preprocessEsrSmoothingInput.checked,
    ai_live_preview: els.aiLivePreviewInput.checked,
    watermark_removal: els.watermarkRemovalInput.checked,
    batch_background_to_black: els.batchBackgroundToBlackInput.checked,
    batch_background_desaturate: els.batchBackgroundDesaturateInput.checked,
    batch_semitransparent_to_black: els.batchSemiTransparentToBlackInput.checked,
    batch_semitransparent_to_opaque: els.batchSemiTransparentToOpaqueInput.checked,
    preview_background: state.preview.background,
    preview_interval: clamp(Number(els.previewIntervalInput.value || 100), 20, 5000),
    preview_reversed: state.preview.isReversed,
    process_preview_zoom: {
      source: state.processPreviewZoom.source,
      processed: state.processPreviewZoom.processed,
    },
    process_preview_background: {
      mode: state.processPreviewBackground.mode,
      color: state.processPreviewBackground.color,
    },
    magic_resize_mode: state.magicResizeMode,
    magic_use_realesrgan: state.magicUseRealesrgan,
    magic_variant_keys: [...state.magicVariantKeys],
    segment: {
      start: Number(state.segment.start || 0),
      end: Number(state.segment.end || 0),
      startFrame: Number(state.segment.startFrame || 1),
      endFrame: Number(state.segment.endFrame || 1),
      confirmed: Boolean(state.segment.confirmed),
    },
  };
}

function collectProcessingPayload() {
  const matteMode = currentMatteMode();
  syncCorridorScreenFromMatteMode(matteMode);
  const usesUserChromaSettings = matteModeUsesChromaSeed(matteMode);
  const usesChromaGuideTolerance = matteMode === "chroma" || matteModeUsesCorridorKey(matteMode);
  return {
    upload_id: state.upload?.upload_id || "",
    start_time: state.segment.start,
    end_time: state.segment.end,
    start_frame: state.segment.startFrame,
    end_frame: state.segment.endFrame,
    keep_every: Number(els.keepEveryInput.value || 1),
    output_scale: 1,
    canvas_mode: "auto",
    reduce_px: 0,
    chroma_enabled: true,
    matte_mode: processingMatteMode(matteMode),
    key_mode: usesUserChromaSettings ? els.keyModeInput.value : "auto",
    manual_key_hex: els.manualKeyInput.value,
    manual_key_colors: usesUserChromaSettings ? [...state.manualKeyColors] : [],
    threshold: usesChromaGuideTolerance
      ? Number(state.matteThresholds.chroma ?? MATTE_THRESHOLD_DEFAULTS.chroma)
      : MATTE_THRESHOLD_DEFAULTS.chroma,
    softness: Number(els.softnessInput.value === "" ? 1 : els.softnessInput.value),
    despill_strength: Number(els.despillInput.value || 0),
    halo_pixels: currentMatteMode() === "birefnet" ? 0 : Number(els.haloInput.value || 0),
    birefnet_edge_shrink: 0,
    corridorkey_enabled: currentUsesCorridorKey(),
    corridorkey_coarse_mask: els.corridorCoarseMaskInput.value,
    corridorkey_screen: els.corridorScreenInput.value,
    corridorkey_color_space: "srgb",
    corridorkey_despill_strength: Number(els.corridorDespillInput.value || 0),
    corridorkey_refiner_scale: Number(els.corridorRefinerInput.value || 0),
    corridorkey_despeckle_enabled: els.corridorDespeckleEnabledInput.checked,
    corridorkey_despeckle_size: Number(els.corridorDespeckleSizeInput.value || 0),
    corridorkey_garbage_matte_enabled: els.corridorGarbageEnabledInput.checked,
    corridorkey_garbage_matte_px: Number(els.corridorGarbagePixelsInput.value || 20),
    ai_model: AI_MODEL_AUTO,
    ai_device: els.aiDeviceInput.value,
    ai_resolution: AI_RESOLUTION_AUTO,
    luma_black: Number(els.lumaBlackInput.value || 0),
    luma_white: Number(els.lumaWhiteInput.value || 85),
    luma_gamma: Number(els.lumaGammaInput.value || 0.55),
    luma_strength: Number(els.lumaStrengthInput.value || 1.7),
    luma_polarity: els.lumaPolarityInput.value || "auto",
    preprocess_esr_smoothing: els.preprocessEsrSmoothingInput.checked,
    watermark_removal: els.watermarkRemovalInput.checked,
    batch_background_to_black: els.batchBackgroundToBlackInput.checked,
    batch_background_desaturate: els.batchBackgroundDesaturateInput.checked,
    batch_semitransparent_to_black: els.batchSemiTransparentToBlackInput.checked,
    batch_semitransparent_to_opaque: els.batchSemiTransparentToOpaqueInput.checked,
  };
}

function applyFormState(snapshot) {
  if (!snapshot) {
    return;
  }

  if (snapshot.keep_every != null) els.keepEveryInput.value = String(snapshot.keep_every);
  const storedCorridorMode = snapshot.corridorkey_screen === "blue" ? "corridorkey_blue" : "corridorkey";
  const legacyMatteModes = {
    chroma_birefnet: "chroma",
    birefnet_corridorkey: storedCorridorMode,
    birefnet_corridorkey_key: storedCorridorMode,
    birefnet_luma: "luma",
    birefnet_luma_key: "luma",
    birefnet_luma_corridorkey: "luma",
  };
  const snapshotMatteMode = snapshot.matte_mode === "corridorkey"
    ? storedCorridorMode
    : legacyMatteModes[snapshot.matte_mode] || snapshot.matte_mode;
  if (snapshotMatteMode && [...els.matteModeInput.options].some((option) => option.value === snapshotMatteMode)) {
    els.matteModeInput.value = snapshotMatteMode;
  }
  if (snapshot.corridorkey_enabled && !matteModeUsesCorridorKey(els.matteModeInput.value)) {
    els.matteModeInput.value = storedCorridorMode;
  }
  if (snapshot.key_mode) els.keyModeInput.value = snapshot.key_mode;
  const storedManualColors = Array.isArray(snapshot.manual_key_colors)
    ? snapshot.manual_key_colors
    : snapshot.manual_key_hex
      ? [snapshot.manual_key_hex]
      : [];
  const migratedManualColors = snapshot.manual_key_colors_explicit === true
    ? storedManualColors
    : storedManualColors.length === 1 && normalizeHexColor(storedManualColors[0], "") === "#00FF00"
      ? []
      : storedManualColors;
  setManualKeyColors(migratedManualColors, { persist: false });
  applyAutomaticMatteDefaults();
  if (snapshot.chroma_threshold != null) {
    state.matteThresholds.chroma = clamp(Number(snapshot.chroma_threshold), 0, 180);
  } else if (snapshot.threshold != null && !matteModeUsesCorridorKey(snapshotMatteMode)) {
    state.matteThresholds.chroma = clamp(Number(snapshot.threshold), 0, 180);
  }
  if (snapshot.corridorkey_threshold != null) {
    const storedCorridorThreshold = clamp(Number(snapshot.corridorkey_threshold), 0, 180);
    const storedDefaultVersion = Number(snapshot.corridorkey_threshold_default_version || 0);
    state.matteThresholds.corridorkey =
      storedDefaultVersion < CORRIDOR_THRESHOLD_DEFAULT_VERSION
      && storedCorridorThreshold === PREVIOUS_CORRIDOR_THRESHOLD_DEFAULT
        ? MATTE_THRESHOLD_DEFAULTS.corridorkey
        : storedCorridorThreshold;
  }
  applyMatteThreshold(currentMatteMode());
  els.corridorEnabledInput.checked = currentUsesCorridorKey();
  els.corridorCoarseMaskInput.value = snapshot.corridorkey_coarse_mask === "birefnet" ? "birefnet" : "chroma";
  syncCorridorScreenFromMatteMode(currentMatteMode());
  if (["srgb", "linear"].includes(snapshot.corridorkey_color_space)) {
    els.corridorColorSpaceInput.value = snapshot.corridorkey_color_space;
  }
  els.corridorColorSpaceInput.value = "srgb";
  if (snapshot.corridorkey_despill_strength != null) {
    els.corridorDespillInput.value = String(clamp(Number(snapshot.corridorkey_despill_strength), 0, 1));
  }
  if (snapshot.corridorkey_refiner_scale != null) {
    els.corridorRefinerInput.value = String(clamp(Number(snapshot.corridorkey_refiner_scale), 0, 3));
  }
  if (snapshot.corridorkey_despeckle_enabled != null) {
    els.corridorDespeckleEnabledInput.checked = Boolean(snapshot.corridorkey_despeckle_enabled);
  }
  if (snapshot.corridorkey_despeckle_size != null) {
    els.corridorDespeckleSizeInput.value = String(clamp(Number(snapshot.corridorkey_despeckle_size), 0, 999999));
  }
  if (snapshot.corridorkey_garbage_matte_enabled != null) {
    els.corridorGarbageEnabledInput.checked = Boolean(snapshot.corridorkey_garbage_matte_enabled);
  }
  if (snapshot.corridorkey_garbage_matte_px != null) {
    els.corridorGarbagePixelsInput.value = String(clamp(Number(snapshot.corridorkey_garbage_matte_px), 1, 500));
  }
  els.corridorKeySettings.open = Boolean(snapshot.corridorkey_settings_open);
  syncCorridorControlState();
  els.aiModelInput.value = AI_MODEL_AUTO;
  setAiResolutionValue(AI_RESOLUTION_AUTO);
  els.birefnetEdgeShrinkInput.value = "0";
  syncBirefnetControlState();
  if (
    snapshot.luma_polarity &&
    [...els.lumaPolarityInput.options].some((option) => option.value === snapshot.luma_polarity)
  ) {
    els.lumaPolarityInput.value = snapshot.luma_polarity;
  } else {
    els.lumaPolarityInput.value = "auto";
  }
  if (snapshot.preprocess_esr_smoothing != null) {
    els.preprocessEsrSmoothingInput.checked = Boolean(snapshot.preprocess_esr_smoothing);
  }
  els.aiLivePreviewInput.checked = Boolean(snapshot.ai_live_preview);
  els.watermarkRemovalInput.checked = Boolean(snapshot.watermark_removal);
  const batchBackgroundToBlack = snapshot.batch_background_to_black ?? snapshot.batch_green_to_black;
  if (batchBackgroundToBlack != null) {
    els.batchBackgroundToBlackInput.checked = Boolean(batchBackgroundToBlack);
  }
  const batchBackgroundDesaturate = snapshot.batch_background_desaturate ?? snapshot.batch_green_desaturate;
  if (batchBackgroundDesaturate != null) {
    els.batchBackgroundDesaturateInput.checked = Boolean(batchBackgroundDesaturate);
  }
  if (snapshot.batch_semitransparent_to_black != null) {
    els.batchSemiTransparentToBlackInput.checked = Boolean(snapshot.batch_semitransparent_to_black);
  }
  if (snapshot.batch_semitransparent_to_opaque != null) {
    els.batchSemiTransparentToOpaqueInput.checked = Boolean(snapshot.batch_semitransparent_to_opaque);
  }
  updatePreviewBackground(snapshot.preview_background || state.preview.background, false);
  if (snapshot.preview_interval != null) els.previewIntervalInput.value = String(snapshot.preview_interval);
  state.preview.isReversed = Boolean(snapshot.preview_reversed);
  if (els.previewReverseInput) {
    els.previewReverseInput.checked = state.preview.isReversed;
  }
  if (snapshot.process_preview_zoom) {
    updateProcessPreviewZoom("source", Number(snapshot.process_preview_zoom.source || 100), false);
    updateProcessPreviewZoom("processed", Number(snapshot.process_preview_zoom.processed || 100), false);
  } else {
    updateProcessPreviewZoom("source", 100, false);
    updateProcessPreviewZoom("processed", 100, false);
  }
  if (snapshot.process_preview_background) {
    updateProcessPreviewBackground(
      snapshot.process_preview_background.mode,
      snapshot.process_preview_background.color,
      false
    );
  } else {
    updateProcessPreviewBackground("checkerboard", state.processPreviewBackground.color, false);
  }

  if (snapshot.magic_resize_mode) {
    setMagicResizeMode(snapshot.magic_resize_mode, { clearExisting: false });
  }
  if (snapshot.magic_use_realesrgan != null) {
    setMagicUseRealesrgan(Boolean(snapshot.magic_use_realesrgan), { clearExisting: false });
  }
  if (Array.isArray(snapshot.magic_variant_keys)) {
    const validKeys = snapshot.magic_variant_keys.filter((key) =>
      MAGIC_VARIANT_CONFIGS.some((config) => config.key === key)
    );
    state.magicVariantKeys = new Set(validKeys.length ? validKeys : ["half"]);
    if (state.magicVariantKeys.has("full") && !state.magicUseRealesrgan) {
      setMagicUseRealesrgan(true, { clearExisting: false });
    }
    syncMagicVariantButtons();
  }

  if (snapshot.segment) {
    state.segment.start = Number(snapshot.segment.start || 0);
    state.segment.end = Number(snapshot.segment.end || 0);
    if (snapshot.segment.startFrame != null && snapshot.segment.endFrame != null) {
      state.segment.startFrame = Number(snapshot.segment.startFrame || 1);
      state.segment.endFrame = Number(snapshot.segment.endFrame || 1);
      syncSegmentTimesFromFrames();
    } else {
      syncSegmentFramesFromTimes();
    }
    state.segment.confirmed = Boolean(snapshot.segment.confirmed);
  }

  syncManualColorLabel();
  updateChromaVisibility();
  normalizePreviewInterval();
}

function persistSession() {
  if (skipSessionPersistence) {
    return;
  }
  try {
    const selectionOrder = normalizeSelectionOrder();
    const snapshot = {
      upload: state.upload,
      job: state.job,
      exportResult: state.exportResult,
      processPreview: state.processPreview,
      selectedIndices: Array.from(state.selected).sort((a, b) => a - b),
      selectionOrder,
      orderedSelectionMode: state.orderedSelectionMode,
      preview: {
        isPlaying: state.preview.isPlaying,
        currentIndex: state.preview.currentIndex,
        isReversed: state.preview.isReversed,
      },
      form: collectFormState(),
      savedAt: new Date().toISOString(),
    };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
  } catch (error) {
    console.warn("persistSession failed", error);
  }
}

function restoreSessionFromStorage() {
  let snapshot = null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return;
    }
    snapshot = JSON.parse(raw);
  } catch (error) {
    console.warn("restoreSessionFromStorage failed", error);
    return;
  }

  if (!snapshot) {
    return;
  }

  setOrderedSelectionMode(Boolean(snapshot.orderedSelectionMode));

  if (!snapshot.upload && !snapshot.job?.frames) {
    if (snapshot?.form) {
      applyFormState(snapshot.form);
      updateSegmentConfirmationUI();
    }
    return;
  }

  if (snapshot.upload) {
    applyUpload(snapshot.upload);
  } else {
    resetPreviewState();
    state.upload = null;
    state.processPreview = null;
    els.previewPanel.hidden = true;
    els.processPanel.hidden = true;
    resetProcessPreview();
    updateSegmentConfirmationUI();
  }
  applyFormState(snapshot.form);
  if (state.upload) {
    normalizeSegment("end");
    renderSegmentControls();
    updateSegmentConfirmationUI();
    restartSegmentPlayback({ autoplay: false });
  }

  if (snapshot.preview && typeof snapshot.preview.isPlaying === "boolean") {
    state.preview.isPlaying = snapshot.preview.isPlaying;
  }
  if (snapshot.preview && typeof snapshot.preview.isReversed === "boolean") {
    state.preview.isReversed = snapshot.preview.isReversed;
    els.previewReverseInput.checked = state.preview.isReversed;
  }

  if (snapshot.processPreview) {
    state.processPreview = snapshot.processPreview;
    renderProcessPreview();
  }

  if (snapshot.job?.frames) {
    state.job = snapshot.job;
    state.exportResult = snapshot.exportResult || null;
    if (Array.isArray(snapshot.selectedIndices)) {
      state.selected = new Set(
        snapshot.selectedIndices.map((index) => Number(index)).filter((index) => !Number.isNaN(index))
      );
    } else {
      state.selected = new Set(snapshot.job.frames.map((frame) => frame.index));
    }
    state.selectionOrder = Array.isArray(snapshot.selectionOrder)
      ? snapshot.selectionOrder.map((index) => Number(index)).filter((index) => !Number.isNaN(index))
      : Array.from(state.selected);
    normalizeSelectionOrder();
    state.preview.currentIndex = clamp(
      Number(snapshot.preview?.currentIndex || 0),
      0,
      Math.max(snapshot.job.frames.length - 1, 0)
    );
    renderJob();
    if (state.exportResult) {
      renderExportResult();
    }
  } else {
    syncAnimationPreview();
  }

  setStatus("\u5DF2\u6062\u590D\u4E0A\u6B21\u7684\u5DE5\u4F5C\u73B0\u573A\u3002", "success");
}

function startHotReloadPolling() {
  if (hotReloadTimerId !== null) {
    window.clearTimeout(hotReloadTimerId);
    hotReloadTimerId = null;
  }

  const poll = async () => {
    try {
      const data = await apiJson(`/api/app-version?ts=${Date.now()}`);
      const nextVersion = String(data.version || "0");
      const pollMs = Number(data.poll_ms || 1200);
      if (hotReloadVersion === null) {
        hotReloadVersion = nextVersion;
      } else if (nextVersion !== hotReloadVersion) {
        hotReloadVersion = nextVersion;
        persistSession();
        setStatus("\u68C0\u6D4B\u5230\u4EE3\u7801\u53D8\u66F4\uFF0C\u6B63\u5728\u81EA\u52A8\u5237\u65B0...", "success");
        window.setTimeout(() => window.location.reload(), 900);
        return;
      }
      hotReloadTimerId = window.setTimeout(poll, pollMs);
    } catch (error) {
      hotReloadTimerId = window.setTimeout(poll, 1200);
    }
  };

  poll();
}

function bindUploadDropzone() {
  els.uploadDropzone.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    if (els.uploadInput.disabled) {
      return;
    }
    els.uploadInput.click();
  });

  els.uploadDropzone.addEventListener("dragenter", (event) => {
    if (!dragEventHasFiles(event)) {
      return;
    }
    event.preventDefault();
    uploadDragDepth += 1;
    els.uploadDropzone.classList.add("dragging");
  });

  els.uploadDropzone.addEventListener("dragover", (event) => {
    if (!dragEventHasFiles(event)) {
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "copy";
    }
    els.uploadDropzone.classList.add("dragging");
  });

  els.uploadDropzone.addEventListener("dragleave", (event) => {
    if (!dragEventHasFiles(event)) {
      return;
    }
    event.preventDefault();
    uploadDragDepth = Math.max(0, uploadDragDepth - 1);
    if (uploadDragDepth === 0) {
      els.uploadDropzone.classList.remove("dragging");
    }
  });

  els.uploadDropzone.addEventListener("drop", async (event) => {
    if (!dragEventHasFiles(event)) {
      return;
    }
    event.preventDefault();
    uploadDragDepth = 0;
    els.uploadDropzone.classList.remove("dragging");
    await uploadSelectedFiles(Array.from(event.dataTransfer?.files || []));
  });
}

function dragEventHasFiles(event) {
  const types = Array.from(event.dataTransfer?.types || []);
  return types.includes("Files");
}

function setUploadDropzoneBusy(isBusy) {
  els.uploadDropzone.classList.toggle("busy", isBusy);
  els.uploadDropzone.setAttribute("aria-busy", isBusy ? "true" : "false");
  els.uploadDropzone.setAttribute("aria-disabled", isBusy ? "true" : "false");
  els.uploadInput.disabled = isBusy;
}

function currentUploadInfo(upload = state.upload) {
  return upload?.media_info || upload?.video_info || {};
}

function uploadMediaType(upload = state.upload) {
  const info = currentUploadInfo(upload);
  return String(upload?.media_type || info.media_type || "video").toLowerCase();
}

function isImageUpload(upload = state.upload) {
  return uploadMediaType(upload) === "image";
}

function isImageSequenceUpload(upload = state.upload) {
  return uploadMediaType(upload) === "image_sequence";
}

function isVideoUpload(upload = state.upload) {
  return uploadMediaType(upload) === "video";
}

function isSupportedUploadFile(file) {
  if (!file || !file.name) {
    return false;
  }
  const name = String(file.name).toLowerCase();
  return SUPPORTED_UPLOAD_EXTENSIONS.some((ext) => name.endsWith(ext));
}

function isSupportedImageFile(file) {
  if (!file || !file.name) {
    return false;
  }
  const name = String(file.name).toLowerCase();
  return SUPPORTED_IMAGE_EXTENSIONS.some((ext) => name.endsWith(ext));
}

function formatSourceModeLabel(ffmpegAccel, sourceMediaType = uploadMediaType()) {
  const type = String(sourceMediaType || "video").toLowerCase();
  if (type === "animation") {
    return "\u81EA\u5B9A\u4E49\u52A8\u753B";
  }
  if (type === "image_sequence") {
    return "\u56FE\u7247\u5E8F\u5217";
  }
  if (type === "image") {
    return "\u9759\u6001\u56FE\u7247";
  }
  return `FFmpeg ${formatFfmpegAccelLabel(ffmpegAccel)}`;
}

function formatMatteModeLabel(matte) {
  const mode = typeof matte === "string" ? matte : (matte?.mode || "chroma");
  let label = "Chroma";
  if (mode === "none") label = "\u4E0D\u53BB\u80CC\u666F";
  if (mode === "luma") label = "Luma";
  if (mode === "birefnet") label = "BiRefNet";
  if (mode === "corridorkey") label = "EZ CorridorKey";
  if (mode === "corridorkey_blue") label = "EZ CorridorKey（蓝幕）";
  if (
    mode !== "none" &&
    !matteModeUsesCorridorKey(mode) &&
    typeof matte !== "string" &&
    matte?.corridorkey_enabled
  ) {
    label = `${label} + CorridorKey`;
  }
  return label;
}

function formatCorridorScreenLabel(value) {
  if (value === "blue") return "\u84DD\u5E55";
  if (value === "green") return "\u7EFF\u5E55";
  return "\u81EA\u52A8";
}

function formatMatteDetail(matte) {
  if (!matte) {
    return "";
  }
  if (matte.mode === "none") {
    return "";
  }
  const parts = [];
  const usesBirefnetCoarseMask =
    matte.mode === "corridorkey" && matte.corridorkey_coarse_mask === "birefnet";
  if ((matteModeUsesBiRefNet(matte.mode) || usesBirefnetCoarseMask) && matte.model_label) {
    parts.push(matte.model_label);
  }
  if (matteModeUsesLuma(matte.mode) && matte.luma_enabled) {
    parts.push(matte.luma_resolved_polarity === "dark" ? "\u53BB\u767D\u5E95" : "\u53BB\u9ED1\u5E95");
  }
  if (matte.resolution) {
    parts.push(
      matteModeUsesBiRefNet(matte.mode) || usesBirefnetCoarseMask
        ? `AI ${matte.resolution}px`
        : `${matte.resolution}px`
    );
  }
  if (matte.solid_key_fallback && matte.solid_key_color) {
    parts.push(`\u8272\u952e\u515c\u5e95 ${matte.solid_key_color}`);
  }
  if (matte.corridorkey_enabled) {
    const screen = formatCorridorScreenLabel(matte.corridorkey_screen_color);
    const coarseMask = matte.corridorkey_coarse_mask === "birefnet" ? "BiRefNet" : "Chroma";
    const device = matte.corridorkey_device ? ` / ${matte.corridorkey_device}` : "";
    const despill = Number.isFinite(Number(matte.corridorkey_despill_strength))
      ? ` / \u8FB9\u7F18\u53BB\u6EA2\u8272 ${Number(matte.corridorkey_despill_strength).toFixed(2)}`
      : "";
    const refiner = Number.isFinite(Number(matte.corridorkey_refiner_scale))
      ? ` / \u7EC6\u5316 ${Number(matte.corridorkey_refiner_scale).toFixed(2)}`
      : "";
    parts.push(`EZ CorridorKey ${screen} / 粗遮罩 ${coarseMask}${device}${despill}${refiner}`);
  }
  if (matte.alpha_aware_despill) {
    parts.push("\u81EA\u52A8 alpha-aware \u53BB\u6EA2\u8272");
  }
  return parts.join(" / ");
}

function formatCanvasModeLabel(value) {
  if (value === "custom") return "\u539F\u59CB\u5E27\u5C3A\u5BF8";
  if (value === "square_bottom") return "\u65B9\u5F62 / \u5E95\u90E8";
  if (value === "square_center") return "\u65B9\u5F62 / \u5C45\u4E2D";
  return "\u4FDD\u7559\u6E90\u753B\u5E03";
}

function formatOutputScaleLabel(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return "-";
  }
  return `${Math.round(numeric * 100)}%`;
}

function formatSizeLabel(width, height) {
  const safeWidth = Number(width);
  const safeHeight = Number(height);
  if (!Number.isFinite(safeWidth) || !Number.isFinite(safeHeight) || safeWidth <= 0 || safeHeight <= 0) {
    return "";
  }
  return `${Math.round(safeWidth)} \u00d7 ${Math.round(safeHeight)}`;
}

async function importFromPath() {
  const path = els.pathInput.value.trim();
  if (!path) {
    setStatus("\u5148\u586B\u4E00\u4E2A\u672C\u5730\u89C6\u9891\u6216\u56FE\u7247\u7684\u7EDD\u5BF9\u8DEF\u5F84\u3002", "error");
    return;
  }

  await withBusy(els.importPathButton, async () => {
    setStatus("\u6B63\u5728\u5BFC\u5165\u672C\u5730\u7D20\u6750\u8DEF\u5F84...");
    const data = await apiJson("/api/import-path", {
      method: "POST",
      body: { path },
    });
    applyUpload(data.upload);
    setStatus(`\u5df2\u5bfc\u5165 ${data.upload.display_name}\u3002`, "success");
  });
}

function renderOutputPath(payload) {
  if (!els.outputPathInput || !payload) {
    return;
  }
  els.outputPathInput.value = payload.path || "";
  els.outputPathInput.placeholder = payload.default_path || "D:\\sprite-video-lab-exports";
}

async function loadOutputPath() {
  try {
    const data = await apiJson("/api/output-path");
    renderOutputPath(data.output_path);
  } catch (error) {
    console.warn("loadOutputPath failed", error);
  }
}

async function selectOutputPath() {
  await withBusy(els.saveOutputPathButton, async () => {
    setStatus("\u8BF7\u5728\u5F39\u51FA\u7A97\u53E3\u91CC\u9009\u62E9\u8F93\u51FA\u6587\u4EF6\u5939...");
    const data = await apiJson("/api/select-output-path", { method: "POST" });
    renderOutputPath(data.output_path);
    if (data.cancelled) {
      setStatus("\u5DF2\u53D6\u6D88\u8BBE\u7F6E\u8F93\u51FA\u8DEF\u5F84\u3002");
      return;
    }
    setStatus(`\u8F93\u51FA\u8DEF\u5F84\u5DF2\u8BBE\u7F6E\uFF1A${data.output_path.path}`, "success");
  });
}

async function clearRuntimeFiles() {
  const confirmed = window.confirm(
    "确定清空 Sprite Video Lab 的全部内部文件吗？\n\n" +
      "将删除导入副本、处理帧、预览、缩放/线稿缓存，以及输出目录中由本程序创建的时间戳导出文件夹。\n" +
      "不会删除已经下载/另存的文件，也不会删除输出目录中的其他文件。\n\n" +
      "此操作无法撤销。"
  );
  if (!confirmed) {
    return;
  }

  await withBusy(els.clearRuntimeFilesButton, async () => {
    setStatus("正在清空 WebApp 内部文件...");
    await apiJson("/api/clear-runtime-files", {
      method: "POST",
      body: { confirmed: true },
    });
    skipSessionPersistence = true;
    window.localStorage.removeItem(STORAGE_KEY);
    window.location.reload();
  });
}

async function handleUploadInputChange() {
  await uploadSelectedFiles(Array.from(els.uploadInput.files || []));
  els.uploadInput.value = "";
}

async function uploadSelectedFiles(files) {
  if (!files.length) {
    return;
  }
  if (files.length > 1 && !files.every(isSupportedImageFile)) {
    setStatus("\u591A\u6587\u4EF6\u5BFC\u5165\u53EA\u652F\u6301\u56FE\u7247\u5E8F\u5217\uFF0C\u8BF7\u4E00\u6B21\u9009\u5165\u591A\u5F20 PNG/JPG/WebP/BMP\u3002", "error");
    return;
  }
  if (files.length === 1 && !isSupportedUploadFile(files[0])) {
    setStatus("\u53EA\u652F\u6301\u89C6\u9891\u3001GIF \u52A8\u56FE\u3001\u5355\u5F20\u56FE\u7247\u6216\u591A\u56FE\u5E8F\u5217\uFF1A.mp4 / .mov / .mkv / .webm / .gif / .png / .jpg / .jpeg / .webp / .bmp\u3002", "error");
    return;
  }

  const form = new FormData();
  files.forEach((file) => {
    form.append("video", file, file.webkitRelativePath || file.name);
  });
  const isSequence = files.length > 1;

  setUploadDropzoneBusy(true);
  await withBusy(els.importPathButton, async () => {
    try {
      setStatus(isSequence ? `\u6B63\u5728\u6309\u6587\u4EF6\u540D\u8F7D\u5165 ${files.length} \u5F20\u56FE\u7247...` : `\u6b63\u5728\u8F7D\u5165 ${files[0].name}...`);
      const response = await fetch("/api/upload", {
        method: "POST",
        body: form,
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "\u4E0A\u4F20\u5931\u8D25");
      }
      applyUpload(data.upload);
      setStatus(
        isSequence
          ? `\u5DF2\u6309\u6587\u4EF6\u540D\u987A\u5E8F\u8F7D\u5165 ${data.upload.media_info?.frame_count || files.length} \u5F20\u56FE\u7247\u3002`
          : `\u5DF2\u8F7D\u5165 ${data.upload.display_name}\u3002`,
        "success"
      );
    } finally {
      setUploadDropzoneBusy(false);
      uploadDragDepth = 0;
      els.uploadDropzone.classList.remove("dragging");
      els.uploadInput.value = "";
    }
  });
}

function sortFilesByDisplayName(files) {
  return [...files].sort((a, b) => {
    const aName = a.webkitRelativePath || a.name || "";
    const bName = b.webkitRelativePath || b.name || "";
    return aName.localeCompare(bName, undefined, { numeric: true, sensitivity: "base" });
  });
}

async function importCustomAnimationFrames(files, button) {
  const imageFiles = sortFilesByDisplayName(files).filter(isSupportedImageFile);
  if (imageFiles.length === 0) {
    setStatus("\u8BF7\u9009\u62E9 PNG / JPG / WebP / BMP \u5E8F\u5217\u5E27\u3002", "error");
    return;
  }

  const form = new FormData();
  imageFiles.forEach((file) => {
    form.append("frames", file, file.webkitRelativePath || file.name);
  });

  await withBusy(button, async () => {
    stopPreviewTimer();
    setStatus(`\u6B63\u5728\u6309\u6587\u4EF6\u540D\u5BFC\u5165 ${imageFiles.length} \u5E27...`);
    const data = await apiJson("/api/import-animation", {
      method: "POST",
      body: form,
    });

    state.upload = null;
    state.processPreview = null;
    state.job = data.job;
    state.exportResult = null;
    clearMagicPreview();
    state.selected = new Set(data.job.frames.map((frame) => frame.index));
    state.selectionOrder = data.job.frames.map((frame) => frame.index);
    setOrderedSelectionMode(false);
    state.preview.currentIndex = 0;
    state.preview.isPlaying = true;
    els.previewReverseInput.checked = state.preview.isReversed;
    els.previewPanel.hidden = true;
    els.processPanel.hidden = true;
    resetProcessPreview();
    updateSegmentConfirmationUI();
    renderJob();
    setStatus(`\u5DF2\u6309\u6587\u4EF6\u540D\u987A\u5E8F\u5BFC\u5165 ${data.job.frame_count} \u5E27\u3002`, "success");
  });
}

function clearPreviewFrames() {
  stopPreviewTimer();
  resetPreviewState();
  state.job = null;
  state.exportResult = null;
  clearMagicPreview();
  state.selected = new Set();
  state.selectionOrder = [];
  setOrderedSelectionMode(false);
  els.jobSummary.innerHTML = "";
  els.frameGrid.innerHTML = "";
  els.exportResult.hidden = true;
  els.exportResult.innerHTML = "";
  showAnimationWorkbench();
  renderSelectionCount();
  drawPreviewPlaceholder();
  updatePreviewControls(0);
  persistSession();
  setStatus("\u5DF2\u6E05\u7A7A\u53C2\u4E0E\u52A8\u753B\u9884\u89C8\u7684\u5E27\u3002", "success");
}

function showAnimationWorkbench() {
  els.resultPanel.hidden = false;
  if (!state.job) {
    els.jobSummary.innerHTML = "";
    els.frameGrid.innerHTML = "";
    els.exportResult.hidden = true;
    els.exportResult.innerHTML = "";
    renderSelectionCount();
    syncAnimationPreview(false);
  }
  syncResultActions();
  scheduleWorkflowRailSync();
}

function syncResultActions() {
  const hasJob = Boolean(state.job);
  const hasSelection = hasJob && state.selected.size > 0;
  els.openProcessedButton.disabled = !hasJob || !state.job?.processed_dir;
  els.exportButton.disabled = !hasSelection;
  els.scaleProcessToggleButton.disabled = !hasSelection;
  els.originalVariantExportButton.disabled = !hasSelection;
  [els.exportFramesButton, els.exportSpriteSheetButton, els.exportMovButton, els.exportGifButton].forEach((button) => {
    button.disabled = !hasSelection;
  });
  els.magicButton.disabled = !hasSelection || state.magicInFlight;
  els.selectAllButton.disabled = !hasJob;
  els.selectNoneButton.disabled = !hasJob;
  els.selectOddButton.disabled = !hasJob;
  els.selectEvenButton.disabled = !hasJob;
  els.invertSelectionButton.disabled = !hasJob;
  els.orderedSelectionInput.disabled = !hasJob;
}

function applyUpload(upload) {
  setKeySamplingActive(false, { announce: false });
  clearKeySampleMarkers();
  resetPreviewState();
  state.upload = upload;
  state.job = null;
  state.exportResult = null;
  state.processPreview = null;
  clearMagicPreview();
  state.selected = new Set();
  state.selectionOrder = [];
  setOrderedSelectionMode(false);

  const info = currentUploadInfo(upload);
  const mediaType = uploadMediaType(upload);
  const isSequence = mediaType === "image_sequence";
  state.segment.start = 0;
  state.segment.startFrame = 1;
  state.segment.endFrame = mediaType === "video" || isSequence ? getSegmentFrameCount(upload) : 1;
  state.segment.end = mediaType === "video" ? segmentFrameToTime(getSegmentFrameCount(upload), "end", upload) : 0;
  state.segment.confirmed = true;
  normalizeSegment("end");

  els.videoName.textContent = upload.display_name || (mediaType === "image" ? "\u672a\u547d\u540d\u56fe\u7247" : isSequence ? "\u672a\u547d\u540d\u56fe\u7247\u5e8f\u5217" : "\u672a\u547d\u540d\u89c6\u9891");
  els.videoSize.textContent = info.width && info.height ? `${info.width} \u00d7 ${info.height}` : "-";
  els.videoFps.textContent = mediaType === "image" ? "\u5355\u5e27\u56fe\u7247" : isSequence ? `${getSegmentFrameCount(upload)} \u5f20\u56fe\u7247` : (info.fps ? `${Number(info.fps).toFixed(2)} fps` : "-");
  els.videoDuration.textContent = mediaType === "image" ? "\u5355\u5f20\u56fe\u7247" : isSequence ? "\u6309\u6587\u4ef6\u540d\u6392\u5217" : (Number(info.duration || 0) > 0 ? formatSeconds(info.duration) : "-");

  els.previewPanel.hidden = false;
  els.processPanel.hidden = false;
  els.resultPanel.hidden = false;
  els.exportResult.hidden = true;
  els.exportResult.innerHTML = "";
  els.frameGrid.innerHTML = "";
  els.jobSummary.innerHTML = "";
  resetProcessPreview();
  showAnimationWorkbench();
  syncAnimationPreview();

  const mediaUrl = upload.media_url || upload.video_url;
  if (mediaType === "image" || isSequence) {
    els.videoPreview.pause();
    els.videoPreview.hidden = true;
    els.videoPreview.removeAttribute("src");
    els.videoPreview.load();
    els.mediaPreviewImage.src = mediaUrl;
    els.mediaPreviewImage.hidden = false;
  } else {
    els.mediaPreviewImage.hidden = true;
    els.mediaPreviewImage.removeAttribute("src");
    els.videoPreview.hidden = false;
    muteVideoPreview();
    els.videoPreview.src = mediaUrl;
    els.videoPreview.load();
  }
  syncSegmentBounds();
  renderSegmentControls();
  updateSegmentConfirmationUI();
  persistSession();
  scheduleWorkflowRailSync();
}

function resetProcessPreview() {
  state.processPreview = null;
  state.instantChromaPreviewActive = false;
  resetProcessPreviewView("source", false);
  resetProcessPreviewView("processed", false);
  els.previewSourceImage.hidden = true;
  els.previewProcessedImage.hidden = true;
  els.previewSourceImage.removeAttribute("src");
  els.previewProcessedImage.removeAttribute("src");
  setProcessPreviewStageActive("source", false);
  setProcessPreviewStageActive("processed", false);
  els.previewSourceEmpty.hidden = false;
  els.previewProcessedEmpty.hidden = false;
  els.previewProcessedEmpty.textContent = "等待生成预览";
  els.processPreviewTimeLabel.textContent = "\u53D6\u6837\u65F6\u95F4 -";
  els.processPreviewKeyLabel.textContent = "\u53D6\u6837\u65B9\u5F0F - / \u62A0\u56FE -";
  setCorridorPreviewState("empty");
  setBirefnetPreviewState("empty");
  updateSavePreviewButton();
}

function renderProcessPreview({ preserveView = false } = {}) {
  if (!state.processPreview) {
    resetProcessPreview();
    return;
  }

  const sourceModeLabel = formatSourceModeLabel(
    state.processPreview.ffmpeg_accel,
    state.processPreview.source_media_type || uploadMediaType()
  );
  state.instantChromaPreviewActive = false;
  if (!preserveView) {
    resetProcessPreviewPan("source");
    resetProcessPreviewPan("processed");
  }
  els.previewSourceImage.src = state.processPreview.source_url;
  els.previewProcessedImage.src = state.processPreview.processed_url;
  els.previewSourceImage.hidden = false;
  els.previewProcessedImage.hidden = false;
  setProcessPreviewStageActive("source", true);
  setProcessPreviewStageActive("processed", true);
  els.previewSourceEmpty.hidden = true;
  els.previewProcessedEmpty.hidden = true;
  const previewOptions = state.processPreview.options || {};
  const previewOutputSize = formatSizeLabel(previewOptions.output_width, previewOptions.output_height);
  const previewTimeLabel = isImageUpload()
    ? "\u5355\u5F20\u56FE\u7247\u9884\u89C8"
    : isImageSequenceUpload()
    ? `\u56FE\u7247\u5E8F\u5217\u7B2C ${state.processPreview.sample_frame || state.segment.startFrame || 1} \u5E27`
    : `\u53D6\u6837\u65F6\u95F4 ${formatSeconds(state.processPreview.sample_time || 0)}`;
  els.processPreviewTimeLabel.textContent = previewOutputSize
    ? `${previewTimeLabel} / \u8F93\u51FA ${previewOutputSize}`
    : previewTimeLabel;
  const matte = state.processPreview.matte || { mode: state.processPreview.options?.matte_mode || "chroma" };
  setCorridorPreviewState(matte.mode === "corridorkey" ? "current" : "empty");
  setBirefnetPreviewState(matte.mode === "birefnet" ? "current" : "empty");
  const matteLabel = formatMatteModeLabel(matte);
  const matteDetail = formatMatteDetail(matte);
  const previewKeyColors = Array.isArray(state.processPreview.key_colors) && state.processPreview.key_colors.length > 0
    ? state.processPreview.key_colors
    : [state.processPreview.key_color || "-"];
  const chromaDetail = matteModeUsesChromaSeed(matte.mode, matte.corridorkey_coarse_mask)
    ? previewKeyColors.length > 1
      ? ` / \u80CC\u666F\u8272\u6837 ${previewKeyColors.length} \u4E2A`
      : ` / \u80CC\u666F\u8272 ${previewKeyColors[0]}`
    : "";
  els.processPreviewKeyLabel.textContent = `${sourceModeLabel} / ${matteLabel}${matteDetail ? ` / ${matteDetail}` : ""}${chromaDetail}`;
  updateSavePreviewButton();
  persistSession();
}

function syncSegmentBounds() {
  if (isImageUpload()) {
    [els.startRange, els.endRange].forEach((element) => {
      element.step = "1";
      element.min = "1";
      element.max = "1";
    });
    [els.startInput, els.endInput].forEach((element) => {
      element.max = "1";
    });
    return;
  }
  const frameCount = getSegmentFrameCount();
  [els.startRange, els.endRange].forEach((element) => {
    element.step = "1";
    element.min = "1";
    element.max = String(frameCount);
  });
  [els.startInput, els.endInput].forEach((element) => {
    element.max = String(frameCount);
  });
}

function normalizeSegment(changedKey) {
  if (isImageUpload()) {
    state.segment.start = 0;
    state.segment.end = 0;
    state.segment.startFrame = 1;
    state.segment.endFrame = 1;
    return;
  }
  let startFrame = clampSegmentFrame(state.segment.startFrame);
  let endFrame = clampSegmentFrame(state.segment.endFrame);

  if (endFrame < startFrame) {
    if (changedKey === "start") {
      endFrame = startFrame;
    } else {
      startFrame = endFrame;
    }
  }

  state.segment.startFrame = startFrame;
  state.segment.endFrame = endFrame;
  syncSegmentTimesFromFrames();
}

function muteVideoPreview() {
  els.videoPreview.defaultMuted = true;
  els.videoPreview.muted = true;
  try {
    els.videoPreview.volume = 0;
  } catch (error) {}
}

function playVideoPreviewMuted() {
  muteVideoPreview();
  const playPromise = els.videoPreview.play();
  if (playPromise && typeof playPromise.catch === "function") {
    playPromise.catch(() => {});
  }
  startSegmentPlaybackMonitor();
}

function toggleSourceVideoPlayback(event) {
  if (!isVideoUpload() || state.keySamplingActive) {
    return;
  }
  event.preventDefault();
  if (els.videoPreview.paused) {
    playVideoPreviewMuted();
  } else {
    els.videoPreview.pause();
  }
}

function restartSegmentPlayback({ autoplay = true } = {}) {
  if (!state.upload || !isVideoUpload() || els.videoPreview.readyState < 1) {
    return;
  }
  stopSegmentPlaybackMonitor();
  const segmentStart = getSegmentPlaybackStartTime();
  els.videoPreview.currentTime = segmentStart;
  els.currentTimeLabel.textContent = `\u5f53\u524d ${formatSeconds(segmentStart)}`;
  updateVideoProgress(segmentStart);
  if (autoplay) {
    playVideoPreviewMuted();
  }
}

function getSegmentPlaybackStartTime() {
  if (!state.upload || !isVideoUpload()) {
    return 0;
  }
  const duration = Math.max(Number(currentUploadInfo().duration || 0), 0);
  const segmentStart = clamp(Number(state.segment.start || 0), 0, duration);
  const segmentEnd = clamp(Number(state.segment.end || duration), segmentStart, duration);
  const guard = clamp(getSegmentFrameStep() * 0.1, 0.001, 0.006);
  return segmentEnd > segmentStart ? Math.min(segmentEnd, segmentStart + guard) : segmentStart;
}

function getSegmentPlaybackEndTime() {
  if (!state.upload || !isVideoUpload()) {
    return 0;
  }
  const duration = Math.max(Number(currentUploadInfo().duration || 0), 0);
  const segmentStart = clamp(Number(state.segment.start || 0), 0, duration);
  const segmentEnd = clamp(Number(state.segment.end || duration), segmentStart, duration);
  const frameStep = getSegmentFrameStep();
  const guard = clamp(frameStep * 0.35, 0.004, 0.02);
  return Math.max(segmentStart, segmentEnd - guard);
}

function shouldLoopSegmentPlayback(currentTime = els.videoPreview.currentTime || 0) {
  return (
    state.upload &&
    isVideoUpload() &&
    state.segment.end > state.segment.start &&
    Number(currentTime || 0) >= getSegmentPlaybackEndTime()
  );
}

function startSegmentPlaybackMonitor() {
  stopSegmentPlaybackMonitor();
  if (!state.upload || !isVideoUpload()) {
    return;
  }
  const tick = () => {
    state.segmentPlaybackRafId = null;
    if (!state.upload || !isVideoUpload() || els.videoPreview.paused || els.videoPreview.ended) {
      return;
    }
    const current = els.videoPreview.currentTime || 0;
    els.currentTimeLabel.textContent = `\u5f53\u524d ${formatSeconds(current)}`;
    updateVideoProgress(current);
    if (shouldLoopSegmentPlayback(current)) {
      restartSegmentPlayback({ autoplay: true });
      return;
    }
    state.segmentPlaybackRafId = window.requestAnimationFrame(tick);
  };
  state.segmentPlaybackRafId = window.requestAnimationFrame(tick);
}

function stopSegmentPlaybackMonitor() {
  if (state.segmentPlaybackRafId != null) {
    window.cancelAnimationFrame(state.segmentPlaybackRafId);
    state.segmentPlaybackRafId = null;
  }
}

function updateVideoProgress(currentTime = 0) {
  if (!els.videoProgressFill) {
    return;
  }

  const setProgress = (percent) => {
    const progress = clamp(Number(percent || 0), 0, 100);
    els.videoProgressFill.style.setProperty("--progress", `${progress * 3.6}deg`);
    if (els.videoProgressLabel) {
      els.videoProgressLabel.textContent = `${Math.round(progress)}%`;
    }
    if (els.videoProgress) {
      els.videoProgress.setAttribute("aria-valuenow", String(Math.round(progress)));
    }
  };

  if (!state.upload || !isVideoUpload()) {
    setProgress(0);
    return;
  }

  const duration = Math.max(Number(currentUploadInfo().duration || 0), 0);
  const segmentStart = clamp(Number(state.segment.start || 0), 0, duration);
  const segmentEnd = clamp(Number(state.segment.end || duration), segmentStart, duration);
  const segmentLength = Math.max(segmentEnd - segmentStart, 0.01);
  const normalizedCurrent = clamp(Number(currentTime || 0), segmentStart, segmentEnd);
  const progress = ((normalizedCurrent - segmentStart) / segmentLength) * 100;
  setProgress(progress);
}

function renderSegmentControls() {
  const startFrame = clampSegmentFrame(state.segment.startFrame);
  const endFrame = clampSegmentFrame(state.segment.endFrame);
  els.startRange.value = String(startFrame);
  els.startInput.value = String(startFrame);
  els.endRange.value = String(endFrame);
  els.endInput.value = String(endFrame);
  els.segmentLength.textContent = `${Math.max(1, endFrame - startFrame + 1)} \u5E27`;
  updateVideoProgress(isVideoUpload() ? (els.videoPreview.currentTime || state.segment.start || 0) : 0);
}

function updateSegmentConfirmationUI() {
  const hasUpload = Boolean(state.upload);
  const primaryActionsLocked = !hasUpload || preprocessSmoothingInstalling;
  const isImage = isImageUpload();
  const isSequence = isImageSequenceUpload();
  const startField = els.startRange.closest(".field");
  const endField = els.endRange.closest(".field");
  const segmentSummary = els.segmentLength.closest(".segment-summary");
  if (startField) startField.hidden = isImage;
  if (endField) endField.hidden = isImage;
  if (segmentSummary) segmentSummary.hidden = isImage;
  els.videoToolbar.hidden = !isVideoUpload() || !hasUpload;
  els.videoProgress.hidden = !isVideoUpload() || !hasUpload;

  if (isImage) {
    state.segment.start = 0;
    state.segment.end = 0;
    state.segment.confirmed = true;
    els.segmentConfirmStatus.className = "segment-status image";
    els.segmentConfirmStatus.textContent = "\u5355\u5F20\u56FE\u7247\u6A21\u5F0F";
    els.segmentConfirmHint.textContent = "\u65E0\u9700\u8C03\u6574\u65F6\u95F4\u8303\u56F4\u3002\u5F53\u524D\u53C2\u6570\u4F1A\u76F4\u63A5\u4F5C\u7528\u4E8E\u8FD9 1 \u5E27\u3002";
    els.previewFrameButton.disabled = primaryActionsLocked;
    els.processButton.disabled = primaryActionsLocked;
    els.processStepShell.classList.remove("locked");
    els.processLockNote.hidden = true;
    updateVideoProgress(0);
    return;
  }

  if (isSequence) {
    state.segment.confirmed = true;
    state.segment.startFrame = clampSegmentFrame(state.segment.startFrame);
    state.segment.endFrame = clampSegmentFrame(state.segment.endFrame);
    els.segmentConfirmStatus.className = "segment-status confirmed";
    els.segmentConfirmStatus.textContent = `\u56FE\u7247\u5E8F\u5217 \u7B2C ${state.segment.startFrame} \u5E27 - \u7B2C ${state.segment.endFrame} \u5E27`;
    els.segmentConfirmHint.textContent = "\u5E8F\u5217\u4F1A\u6309\u6587\u4EF6\u540D\u987A\u5E8F\u5904\u7406\uFF0C\u53EF\u4EE5\u8C03\u6574\u8D77\u6B62\u5E27\u3002\u518D\u6B21\u62D6\u5165\u591A\u56FE\u4F1A\u66FF\u6362\u5F53\u524D\u8F93\u5165\uFF0C\u4E0D\u4F1A\u8FFD\u52A0\u3002";
    els.previewFrameButton.disabled = primaryActionsLocked;
    els.processButton.disabled = primaryActionsLocked;
    els.processStepShell.classList.remove("locked");
    els.processLockNote.hidden = true;
    updateVideoProgress(0);
    return;
  }

  if (!hasUpload) {
    state.segment.confirmed = false;
    els.segmentConfirmStatus.className = "segment-status";
    els.segmentConfirmStatus.textContent = "\u5148\u5BFC\u5165\u7D20\u6750";
    els.segmentConfirmHint.textContent = "\u8F7D\u5165\u89C6\u9891\u540E\uFF0C\u8FD9\u91CC\u4F1A\u5B9E\u65F6\u9884\u89C8\u5E76\u5FAA\u73AF\u5F53\u524D\u9009\u533A\u3002";
    els.previewFrameButton.disabled = true;
    els.processButton.disabled = true;
    els.processStepShell.classList.add("locked");
    els.processLockNote.hidden = false;
    updateVideoProgress(0);
    return;
  }

  state.segment.confirmed = true;
  els.segmentConfirmStatus.className = "segment-status confirmed";
  els.segmentConfirmStatus.textContent = `\u5F53\u524D\u9009\u533A \u7B2C ${state.segment.startFrame} \u5E27 - \u7B2C ${state.segment.endFrame} \u5E27`;
  els.segmentConfirmHint.textContent = "\u62D6\u52A8\u8D77\u70B9\u6216\u7EC8\u70B9\u540E\uFF0C\u5DE6\u4FA7\u89C6\u9891\u4F1A\u7ACB\u5373\u8DF3\u56DE\u65B0\u8D77\u70B9\u5E76\u9759\u97F3\u5FAA\u73AF\u3002";
  els.previewFrameButton.disabled = preprocessSmoothingInstalling;
  els.processButton.disabled = preprocessSmoothingInstalling;
  els.processStepShell.classList.remove("locked");
  els.processLockNote.hidden = true;
  updateVideoProgress(els.videoPreview.currentTime || state.segment.start || 0);
}

async function processVideo() {
  if (!state.upload) {
    setStatus("\u5148\u5BFC\u5165\u89C6\u9891\u3001\u56FE\u7247\u6216\u591A\u56FE\u5E8F\u5217\uFF0C\u518D\u5904\u7406\u3002", "error");
    return;
  }

  if (!validateManualChromaSamples()) {
    return;
  }

  const matteMode = currentMatteMode();
  if (!(await ensureAiModelsReady(matteMode))) {
    return;
  }
  const payload = collectProcessingPayload();

  await withBusy(els.processButton, async () => {
    stopPreviewTimer();
    const matteLabel = formatMatteModeLabel(matteMode);
    const processLead = payload.preprocess_esr_smoothing
      ? "\u6B63\u5728\u5148\u505A ESR \u5E73\u6ED1\u5904\u7406\uFF0C\u518D"
      : "\u6B63\u5728";
    setStatus(
      matteMode !== "none"
        ? `${processLead}\u8FD0\u884C ${matteLabel} \u62A0\u56FE\u3002`
        : isImageUpload()
        ? `${processLead}\u5904\u7406\u5355\u5F20\u56FE\u7247\u7684\u900F\u660E\u8FB9\u7F18\u548C\u7F29\u653E...`
        : isImageSequenceUpload()
        ? `${processLead}\u6309\u6587\u4EF6\u540D\u987A\u5E8F\u5904\u7406\u56FE\u7247\u5E8F\u5217...`
        : `${processLead}\u62BD\u5E27\u5E76\u5904\u7406\u900F\u660E\u8FB9\u7F18\uFF0C\u8FD9\u4E00\u6B65\u53EF\u80FD\u9700\u8981\u51E0\u5341\u79D2\u3002`
    );
    const data = await apiJson("/api/process", {
      method: "POST",
      body: payload,
    });
    state.job = data.job;
    state.exportResult = null;
    clearMagicPreview();
    state.selected = new Set(data.job.frames.map((frame) => frame.index));
    state.selectionOrder = data.job.frames.map((frame) => frame.index);
    setOrderedSelectionMode(false);
    state.preview.currentIndex = 0;
    renderJob();
    setStatus(
      `\u5904\u7406\u5b8c\u6210\uff0c\u5171\u5f97\u5230 ${data.job.frame_count} \u5e27\uff0c${formatSourceModeLabel(data.job.ffmpeg_accel, data.job.source_media_type)}\u3002`,
      "success"
    );
  });
}

async function previewCurrentFrame({ preserveView = false } = {}) {
  if (!state.upload) {
    setStatus("\u5148\u5BFC\u5165\u89C6\u9891\u3001\u56FE\u7247\u6216\u591A\u56FE\u5E8F\u5217\uFF0C\u518D\u9884\u89C8\u53C2\u6570\u6548\u679C\u3002", "error");
    return;
  }

  if (!validateManualChromaSamples()) {
    return;
  }

  const matteMode = currentMatteMode();
  if (!(await ensureAiModelsReady(matteMode))) {
    return;
  }
  const duration = Number(currentUploadInfo().duration || 0);
  const sampleFrame = isImageSequenceUpload() ? clampSegmentFrame(state.segment.startFrame) : 1;
  const rawCurrentTime = isImageUpload() || isImageSequenceUpload() ? 0 : Number(els.videoPreview.currentTime || state.segment.start || 0);
  const segmentStart = isImageUpload() || isImageSequenceUpload() ? 0 : getSegmentPlaybackStartTime();
  const segmentEnd = isImageUpload() || isImageSequenceUpload() ? 0 : getSegmentPlaybackEndTime();
  const sampleTime = isImageUpload() || isImageSequenceUpload()
    ? 0
    : clamp(rawCurrentTime, segmentStart, Math.max(segmentStart, segmentEnd));
  const payload = {
    ...collectProcessingPayload(),
    sample_time: sampleTime,
    sample_frame: sampleFrame,
  };

  await withBusy(els.previewFrameButton, async () => {
    const matteLabel = formatMatteModeLabel(matteMode);
    const previewLead = payload.preprocess_esr_smoothing
      ? "\u6B63\u5728\u5148\u505A ESR \u5E73\u6ED1\u5904\u7406\uFF0C\u518D"
      : "\u6B63\u5728";
    setStatus(
      matteMode !== "none"
        ? `${previewLead}\u9884\u89C8 ${matteLabel} \u62A0\u56FE\u3002`
        : isImageUpload()
        ? `${previewLead}\u5957\u7528\u53C2\u6570\u9884\u89C8\u5355\u5F20\u56FE\u7247...`
        : isImageSequenceUpload()
        ? `${previewLead}\u9884\u89C8\u56FE\u7247\u5E8F\u5217\u7B2C ${sampleFrame} \u5E27...`
        : `${previewLead}\u62BD\u53D6\u5F53\u524D\u5E27\u5E76\u5957\u7528\u53C2\u6570...`
    );
    const data = await apiJson("/api/preview-frame", {
      method: "POST",
      body: payload,
    });
    state.processPreview = data.preview;
    renderProcessPreview({ preserveView });
    setStatus(
      isImageUpload()
        ? `\u5355\u5F20\u56FE\u7247\u9884\u89C8\u5DF2\u66F4\u65B0\uFF0C${formatSourceModeLabel(data.preview.ffmpeg_accel, data.preview.source_media_type)}\u3002`
        : isImageSequenceUpload()
        ? `\u56FE\u7247\u5E8F\u5217\u7B2C ${data.preview.sample_frame || sampleFrame} \u5E27\u9884\u89C8\u5DF2\u66F4\u65B0\uFF0C${formatSourceModeLabel(data.preview.ffmpeg_accel, data.preview.source_media_type)}\u3002`
        : `\u5355\u5E27\u9884\u89C8\u5DF2\u66F4\u65B0\uFF0C\u53D6\u6837\u65F6\u95F4 ${formatSeconds(sampleTime)}\uFF0C${formatSourceModeLabel(data.preview.ffmpeg_accel, data.preview.source_media_type)}\u3002`,
      "success"
    );
  });
}

async function downloadProcessPreviewResult() {
  if (!state.processPreview?.processed_url) {
    setStatus("\u5148\u9884\u89C8\u5F53\u524D\u5E27\uFF0C\u518D\u4E0B\u8F7D\u9884\u89C8\u56FE\u3002", "error");
    return;
  }
  if (!state.processPreview?.preview_id) {
    setStatus("\u8FD9\u5F20\u9884\u89C8\u56FE\u7F3A\u5C11\u6807\u8BC6\uFF0C\u8BF7\u91CD\u65B0\u9884\u89C8\u4E00\u6B21\u3002", "error");
    return;
  }

  await withBusy(els.savePreviewButton, async () => {
    const filename = buildPreviewDownloadFilename();
    triggerFileDownload(state.processPreview.processed_url, filename);
    setStatus(`\u5DF2\u5F00\u59CB\u4E0B\u8F7D\u9884\u89C8\u56FE\uFF1A${filename}`, "success");
  });
}

function buildPreviewDownloadFilename() {
  const sourceName = stripFileExtension(state.upload?.display_name || "");
  const safeSourceName = sanitizeDownloadFilenamePart(sourceName, "sprite-preview");
  const safePreviewId = sanitizeDownloadFilenamePart(state.processPreview?.preview_id || localTimestamp(), localTimestamp());
  return `${safeSourceName}-preview-${safePreviewId}.png`;
}

function stripFileExtension(name) {
  return String(name || "").replace(/\.[^./\\]+$/, "");
}

function sanitizeDownloadFilenamePart(value, fallback) {
  const cleaned = String(value || "")
    .replace(/[<>:"/\\|?*\u0000-\u001F]+/g, "-")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^\.+/, "")
    .replace(/[.\- ]+$/g, "")
    .slice(0, 80);
  return cleaned || fallback;
}

function localTimestamp() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
    "-",
    pad(now.getHours()),
    pad(now.getMinutes()),
    pad(now.getSeconds()),
  ].join("");
}

function triggerFileDownload(url, filename) {
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function updateSavePreviewButton() {
  if (!els.savePreviewButton) {
    return;
  }

  const isSameUpload = !state.processPreview?.upload_id || state.processPreview.upload_id === state.upload?.upload_id;
  const canDownload = Boolean(
    state.upload
    && isSameUpload
    && !state.instantChromaPreviewActive
    && state.processPreview?.preview_id
    && state.processPreview?.processed_url
  );
  els.savePreviewButton.hidden = !state.upload;
  els.savePreviewButton.disabled = !canDownload;
}

function renderJob() {
  if (!state.job) {
    showAnimationWorkbench();
    return;
  }

  const options = state.job.options || {};
  const keyColor = options.key_color || "#000000";
  const matte = options.matte || { mode: options.matte_mode || (options.chroma_enabled ? "chroma" : "none") };
  const matteDetail = formatMatteDetail(matte);
  const sourceMediaType = state.job.source_media_type || uploadMediaType();
  const outputWidth = options.output_width || options.target_size || "-";
  const outputHeight = options.output_height || options.target_size || "-";
  const sourceHeight = Number(state.job.video_info?.height || 0);
  const legacyOutputScale = sourceHeight && options.target_size ? Number(options.target_size) / sourceHeight : 0;
  const outputScaleLabel = formatOutputScaleLabel(options.output_scale || legacyOutputScale);
  const isCustomAnimation = sourceMediaType === "animation";
  const isImageSequence = sourceMediaType === "image_sequence";
  const segmentLabel = isCustomAnimation
    ? "\u81EA\u5B9A\u4E49\u52A8\u753B\u5E27\u5E8F\u5217"
    : isImageSequence
    ? `\u56FE\u7247\u5E8F\u5217\uFF1A\u7B2C ${options.start_frame || 1} - ${options.end_frame || state.job.frame_count} \u5E27`
    : sourceMediaType === "image"
    ? "\u5355\u5F20\u56FE\u7247\u8F93\u5165"
    : `${formatSeconds(options.start_time || 0)} - ${formatSeconds(options.end_time || 0)}`;
  els.resultPanel.hidden = false;
  els.exportResult.hidden = true;
  const summaryCards = [
    summaryCard("\u4efb\u52a1 ID", escapeHtml(state.job.job_id)),
    summaryCard("\u8f93\u51fa\u5e27\u6570", `${state.job.frame_count} \u5e27`),
    summaryCard("\u53D6\u6837\u65B9\u5F0F", escapeHtml(formatSourceModeLabel(state.job.ffmpeg_accel, sourceMediaType))),
    summaryCard("\u62A0\u56FE\u6A21\u5F0F", escapeHtml(`${formatMatteModeLabel(matte)}${matteDetail ? ` / ${matteDetail}` : ""}`)),
    summaryCard("\u8F93\u51FA\u500D\u6570", escapeHtml(outputScaleLabel)),
    summaryCard("\u8F93\u51FA\u753B\u5E03", `${outputWidth} \u00d7 ${outputHeight}`),
    summaryCard("\u753B\u5E03\u5E03\u5C40", escapeHtml(formatCanvasModeLabel(options.canvas_mode))),
    summaryCard("\u62BD\u5E27\u95F4\u9694", isCustomAnimation || isImageSequence ? "\u6309\u6587\u4EF6\u540D\u987A\u5E8F" : sourceMediaType === "image" ? "\u5355\u5F20\u56FE\u7247" : `\u6BCF ${options.keep_every || 1} \u5E27\u4FDD\u7559\u4E00\u5F20`),
    summaryCard("\u8F93\u5165\u533A\u95F4", segmentLabel),
  ];
  if (matte.mode === "chroma") {
    summaryCards.push(`
      <div class="summary-card">
        <span class="meta-label">\u8bc6\u522b\u5230\u7684\u80cc\u666f\u8272</span>
        <strong class="swatch-row">
          <span class="swatch" style="background:${keyColor}"></span>
          <span>${escapeHtml(keyColor)}</span>
        </strong>
      </div>
    `);
  }
  els.jobSummary.innerHTML = summaryCards.join("");
  renderFrames();
  syncResultActions();
  persistSession();
  scheduleWorkflowRailSync();
}

function renderFrames() {
  if (!state.job) {
    els.frameGrid.innerHTML = "";
    renderSelectionCount();
    syncAnimationPreview();
    return;
  }

  const orderMap = state.orderedSelectionMode ? getSelectionOrderMap() : new Map();
  els.frameGrid.innerHTML = state.job.frames
    .map((frame) => {
      const checked = state.selected.has(frame.index);
      const frameNumber = String(frame.index + 1).padStart(3, "0");
      const orderNumber = orderMap.get(frame.index);
      return `
        <label class="frame-card ${checked ? "selected" : ""}" data-index="${frame.index}">
          <div class="frame-check ${orderNumber ? "ordered" : ""}">
            <input type="checkbox" data-index="${frame.index}" ${checked ? "checked" : ""}>
            ${orderNumber ? `<span class="frame-order-number">${orderNumber}</span>` : ""}
          </div>
          <img src="${frame.thumb_url}" alt="frame ${frameNumber}">
          <div class="frame-meta">
            <span>#${frameNumber}</span>
            <span>${escapeHtml(frame.original_name || frame.name)}</span>
          </div>
        </label>
      `;
    })
    .join("");
  renderSelectionCount();
  syncAnimationPreview();
  syncResultActions();
  persistSession();
}

function renderSelectionCount() {
  const total = state.job?.frame_count || 0;
  els.selectionCount.textContent = `\u5df2\u9009 ${state.selected.size} / ${total} \u5e27`;
  syncResultActions();
}

function refreshCardSelection(index, checked) {
  const card = els.frameGrid.querySelector(`.frame-card[data-index="${index}"]`);
  if (card) {
    card.classList.toggle("selected", checked);
  }
}

function setOrderedSelectionMode(enabled) {
  state.orderedSelectionMode = Boolean(enabled);
  if (els.orderedSelectionInput) {
    els.orderedSelectionInput.checked = state.orderedSelectionMode;
  }
}

function setFrameSelected(index, checked) {
  if (!state.job) {
    return;
  }
  if (checked) {
    state.selected.add(index);
    state.selectionOrder = state.selectionOrder.filter((item) => item !== index);
    state.selectionOrder.push(index);
  } else {
    state.selected.delete(index);
    state.selectionOrder = state.selectionOrder.filter((item) => item !== index);
  }
  normalizeSelectionOrder();
}

function normalizeSelectionOrder() {
  if (!state.job) {
    state.selectionOrder = [];
    return state.selectionOrder;
  }

  const available = new Set(state.job.frames.map((frame) => frame.index));
  const validSelection = new Set();
  state.selected.forEach((index) => {
    if (available.has(index)) {
      validSelection.add(index);
    }
  });
  state.selected = validSelection;

  const seen = new Set();
  const ordered = [];
  state.selectionOrder.forEach((index) => {
    if (validSelection.has(index) && !seen.has(index)) {
      ordered.push(index);
      seen.add(index);
    }
  });
  state.job.frames.forEach((frame) => {
    if (validSelection.has(frame.index) && !seen.has(frame.index)) {
      ordered.push(frame.index);
      seen.add(frame.index);
    }
  });
  state.selectionOrder = ordered;
  return state.selectionOrder;
}

function getSelectionOrderMap() {
  return new Map(normalizeSelectionOrder().map((index, order) => [index, order + 1]));
}

function selectFrames(predicate) {
  if (!state.job) return;
  state.selected = new Set(state.job.frames.filter(predicate).map((frame) => frame.index));
  state.selectionOrder = state.job.frames.filter((frame) => state.selected.has(frame.index)).map((frame) => frame.index);
  state.preview.currentIndex = 0;
  markScaleResultsStale();
  renderFrames();
}

function getSelectedFrames() {
  if (!state.job) {
    return [];
  }
  const frameMap = new Map(state.job.frames.map((frame) => [frame.index, frame]));
  const frames = state.orderedSelectionMode
    ? normalizeSelectionOrder()
        .map((index) => frameMap.get(index))
        .filter(Boolean)
    : state.job.frames.filter((frame) => state.selected.has(frame.index));
  return state.preview.isReversed ? frames.reverse() : frames;
}

function getSegmentFrameRate(upload = state.upload) {
  const fps = Number(currentUploadInfo(upload).fps || 0);
  return Number.isFinite(fps) && fps > 0 ? fps : 0;
}

function getSegmentFrameStep(upload = state.upload) {
  const fps = getSegmentFrameRate(upload);
  return fps > 0 ? 1 / fps : 0.01;
}

function getSegmentFrameCount(upload = state.upload) {
  if (isImageUpload(upload)) {
    return 1;
  }
  if (isImageSequenceUpload(upload)) {
    return Math.max(1, Math.round(Number(currentUploadInfo(upload).frame_count || 1)));
  }

  const fps = getSegmentFrameRate(upload);
  const duration = Math.max(Number(currentUploadInfo(upload).duration || 0), 0);
  if (fps <= 0 || duration <= 0) {
    return 1;
  }
  return Math.max(1, Math.round(duration * fps));
}

function clampSegmentFrame(frame, upload = state.upload) {
  return clamp(Math.round(Number(frame || 1)), 1, getSegmentFrameCount(upload));
}

function syncSegmentFramesFromTimes(upload = state.upload) {
  if (isImageUpload(upload)) {
    state.segment.startFrame = 1;
    state.segment.endFrame = 1;
    return;
  }
  if (isImageSequenceUpload(upload)) {
    state.segment.startFrame = clampSegmentFrame(state.segment.startFrame, upload);
    state.segment.endFrame = clampSegmentFrame(state.segment.endFrame, upload);
    return;
  }

  state.segment.startFrame = timeToSegmentFrame(state.segment.start, "start", upload);
  state.segment.endFrame = timeToSegmentFrame(state.segment.end, "end", upload);
}

function syncSegmentTimesFromFrames(upload = state.upload) {
  if (isImageUpload(upload)) {
    state.segment.start = 0;
    state.segment.end = 0;
    state.segment.startFrame = 1;
    state.segment.endFrame = 1;
    return;
  }
  if (isImageSequenceUpload(upload)) {
    state.segment.start = 0;
    state.segment.end = 0;
    state.segment.startFrame = clampSegmentFrame(state.segment.startFrame, upload);
    state.segment.endFrame = clampSegmentFrame(state.segment.endFrame, upload);
    return;
  }

  state.segment.startFrame = clampSegmentFrame(state.segment.startFrame, upload);
  state.segment.endFrame = clampSegmentFrame(state.segment.endFrame, upload);
  state.segment.start = segmentFrameToTime(state.segment.startFrame, "start", upload);
  state.segment.end = segmentFrameToTime(state.segment.endFrame, "end", upload);
}

function timeToSegmentFrame(value, key, upload = state.upload) {
  if (isImageUpload(upload)) {
    return 1;
  }
  if (isImageSequenceUpload(upload)) {
    return clampSegmentFrame(key === "start" ? state.segment.startFrame : state.segment.endFrame, upload);
  }

  const step = getSegmentFrameStep(upload);
  const snapped = snapSegmentTime(value, key === "start" ? "floor" : "ceil", upload);
  const rawFrame = key === "start" ? Math.round(snapped / step) + 1 : Math.round(snapped / step);
  return clampSegmentFrame(rawFrame, upload);
}

function segmentFrameToTime(frame, key, upload = state.upload) {
  if (isImageUpload(upload)) {
    return 0;
  }
  if (isImageSequenceUpload(upload)) {
    return 0;
  }

  const clampedFrame = clampSegmentFrame(frame, upload);
  const step = getSegmentFrameStep(upload);
  const rawTime = key === "start" ? (clampedFrame - 1) * step : clampedFrame * step;
  return snapSegmentTime(rawTime, key === "start" ? "floor" : "ceil", upload);
}

function getSegmentFrameValue(key, upload = state.upload) {
  if (isImageSequenceUpload(upload)) {
    return clampSegmentFrame(key === "start" ? state.segment.startFrame : state.segment.endFrame, upload);
  }
  return timeToSegmentFrame(key === "start" ? state.segment.start : state.segment.end, key, upload);
}

function getSelectedSegmentFrameCount(upload = state.upload) {
  if (isImageUpload(upload)) {
    return 1;
  }
  if (isImageSequenceUpload(upload)) {
    const startFrame = clampSegmentFrame(state.segment.startFrame, upload);
    const endFrame = clampSegmentFrame(state.segment.endFrame, upload);
    return Math.max(1, endFrame - startFrame + 1);
  }
  const startFrame = getSegmentFrameValue("start", upload);
  const endFrame = getSegmentFrameValue("end", upload);
  return Math.max(1, endFrame - startFrame + 1);
}

function formatSegmentStep(upload = state.upload) {
  return getSegmentFrameStep(upload).toFixed(8).replace(/0+$/u, "").replace(/\.$/u, "");
}

function snapSegmentTime(value, mode = "round", upload = state.upload) {
  if (isImageUpload(upload) || isImageSequenceUpload(upload)) {
    return 0;
  }

  const duration = Math.max(Number(currentUploadInfo(upload).duration || 0), 0);
  const step = Math.max(getSegmentFrameStep(upload), 1e-6);
  const clampedValue = clamp(Number(value || 0), 0, duration);
  const framePosition = clampedValue / step;

  let frameIndex = Math.round(framePosition);
  if (mode === "floor") {
    frameIndex = Math.floor(framePosition + 1e-9);
  } else if (mode === "ceil") {
    frameIndex = Math.ceil(framePosition - 1e-9);
  }

  const snapped = clamp(frameIndex * step, 0, duration);
  return Number(snapped.toFixed(8));
}

function normalizePreviewInterval() {
  const value = Number(els.previewIntervalInput.value || 100);
  const normalized = clamp(Math.round(value), 20, 5000);
  els.previewIntervalInput.value = String(normalized);
  return normalized;
}

function normalizeHexColor(value, fallback = "#F6FBF6") {
  const raw = String(value || "").trim().toUpperCase();
  if (/^#[0-9A-F]{6}$/.test(raw)) {
    return raw;
  }
  return fallback;
}

function normalizeProcessPreviewBackgroundMode(value) {
  return value === "color" ? "color" : "checkerboard";
}

function updateProcessPreviewBackground(mode, color, shouldPersist = false) {
  const normalizedMode = normalizeProcessPreviewBackgroundMode(mode);
  const normalizedColor = normalizeHexColor(color, state.processPreviewBackground.color);
  state.processPreviewBackground.mode = normalizedMode;
  state.processPreviewBackground.color = normalizedColor;

  els.processPreviewBackgroundModeInput.value = normalizedMode;
  els.processPreviewBackgroundInput.value = normalizedColor;
  els.processPreviewBackgroundLabel.textContent = normalizedColor;
  els.processPreviewBackgroundColorRow.hidden = normalizedMode !== "color";

  if (els.previewProcessedStage) {
    els.previewProcessedStage.style.setProperty("--process-preview-bg-color", normalizedColor);
    els.previewProcessedStage.classList.toggle("checkerboard-stage", normalizedMode === "checkerboard");
    els.previewProcessedStage.classList.toggle("solid-preview-stage", normalizedMode === "color");
  }

  if (shouldPersist) {
    persistSession();
  }
}

function setPreviewStageBackground(color) {
  const normalized = normalizeHexColor(color, state.preview.background);
  [
    els.animationPreviewCanvas,
    ...MAGIC_VARIANT_CONFIGS.map((config) => els[config.canvasId]),
  ].forEach((canvas) => {
    const stage = canvas?.closest(".animation-stage");
    if (!stage) {
      return;
    }
    stage.style.setProperty("--preview-bg-color", normalized);
  });
}

function updatePreviewBackground(color, shouldPersist = false) {
  const normalized = normalizeHexColor(color);
  state.preview.background = normalized;
  els.previewBackgroundInput.value = normalized;
  els.previewBackgroundLabel.textContent = normalized;
  setPreviewStageBackground(normalized);
  if (shouldPersist) {
    persistSession();
  }
}

function resetPreviewState() {
  stopPreviewTimer();
  state.preview.currentIndex = 0;
  state.preview.isPlaying = true;
  state.preview.renderToken += 1;
  state.preview.imageCache.clear();
}

function stopPreviewTimer() {
  state.preview.warmupToken += 1;
  if (state.preview.rafId !== null) {
    window.cancelAnimationFrame(state.preview.rafId);
    state.preview.rafId = null;
  }
}

function restartPreviewTimer() {
  stopPreviewTimer();
  const selectedFrames = getSelectedFrames();
  if (!state.preview.isPlaying || selectedFrames.length <= 1) {
    updatePreviewControls(selectedFrames.length);
    return;
  }

  const warmupToken = state.preview.warmupToken;
  const startLoop = () => {
    if (warmupToken !== state.preview.warmupToken || !state.preview.isPlaying) {
      return;
    }

    let lastAdvanceAt = performance.now();
    const tick = (now) => {
      if (warmupToken !== state.preview.warmupToken) {
        return;
      }

      const frames = getSelectedFrames();
      const frameCount = frames.length;
      if (!state.preview.isPlaying || frameCount <= 1) {
        stopPreviewTimer();
        updatePreviewControls(frameCount);
        return;
      }

      if (state.preview.currentIndex >= frameCount) {
        state.preview.currentIndex = 0;
        drawPreviewFrameFromCache(frames[0], frameCount);
      }

      const intervalMs = normalizePreviewInterval();
      const elapsed = now - lastAdvanceAt;
      if (elapsed >= intervalMs) {
        const steps = Math.max(1, Math.floor(elapsed / intervalMs));
        lastAdvanceAt += steps * intervalMs;
        state.preview.currentIndex = (state.preview.currentIndex + steps) % frameCount;
        drawPreviewFrameFromCache(frames[state.preview.currentIndex], frameCount);
      }

      state.preview.rafId = window.requestAnimationFrame(tick);
    };

    state.preview.rafId = window.requestAnimationFrame(tick);
  };

  if (selectedFrames.every((frame) => getCachedPreviewImage(frame.url))) {
    startLoop();
    updatePreviewControls(selectedFrames.length);
    return;
  }

  warmPreviewFrames(selectedFrames)
    .then(() => {
      startLoop();
    })
    .catch((error) => {
      if (warmupToken !== state.preview.warmupToken) {
        return;
      }
      setStatus(error.message || String(error), "error");
    });
  updatePreviewControls(selectedFrames.length);
}

function togglePreviewPlayback() {
  const selectedFrames = getSelectedFrames();
  if (selectedFrames.length === 0) {
    return;
  }
  state.preview.isPlaying = !state.preview.isPlaying;
  if (state.preview.isPlaying) {
    restartPreviewTimer();
  } else {
    stopPreviewTimer();
    updatePreviewControls(selectedFrames.length);
  }
  persistSession();
}

function restartPreviewPlayback() {
  state.preview.currentIndex = 0;
  syncAnimationPreview();
  persistSession();
}

function updatePreviewControls(selectedCount) {
  const hasFrames = selectedCount > 0;
  const canAnimate = selectedCount > 1;
  const currentIndex = hasFrames ? Math.min(state.preview.currentIndex, selectedCount - 1) : 0;
  const progressPercent = hasFrames ? ((currentIndex + 1) / selectedCount) * 100 : 0;
  els.previewPlayPauseButton.disabled = !canAnimate;
  els.previewRestartButton.disabled = !hasFrames;
  els.previewReverseInput.disabled = !hasFrames;
  els.previewProgressFill.style.width = `${progressPercent}%`;
  els.previewProgressLabel.textContent = hasFrames
    ? `${currentIndex + 1} / ${selectedCount}`
    : "0 / 0";
  els.previewPlayPauseButton.textContent = canAnimate
    ? (state.preview.isPlaying ? "\u6682\u505c\u9884\u89c8" : "\u64ad\u653e\u9884\u89c8")
    : "\u5355\u5E27\u9884\u89C8";
  els.previewSelectedCount.textContent = `\u5df2\u52a0\u8f7d ${selectedCount} \u5e27`;
}

async function loadPreviewImage(url) {
  const cached = state.preview.imageCache.get(url);
  if (cached) {
    return cached instanceof HTMLImageElement ? Promise.resolve(cached) : cached;
  }

  const promise = new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = "async";
    image.onload = () => {
      state.preview.imageCache.set(url, image);
      resolve(image);
    };
    image.onerror = () => {
      state.preview.imageCache.delete(url);
      reject(new Error(`\u9884\u89c8\u5E27\u52A0\u8F7D\u5931\u8D25: ${url}`));
    };
    image.src = url;
  });

  state.preview.imageCache.set(url, promise);
  return promise;
}

function getCachedPreviewImage(url) {
  const cached = state.preview.imageCache.get(url);
  return cached instanceof HTMLImageElement ? cached : null;
}

function warmPreviewFrames(frames) {
  return Promise.all(frames.map((frame) => loadPreviewImage(frame.url)));
}

function paintFrameOnCanvas(canvas, image) {
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = state.preview.background;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = false;

  const baseScale = Math.min(canvas.width / image.naturalWidth, canvas.height / image.naturalHeight);
  const scale = baseScale;
  const drawWidth = image.naturalWidth * scale;
  const drawHeight = image.naturalHeight * scale;
  const drawX = Math.round((canvas.width - drawWidth) / 2);
  const drawY = Math.round((canvas.height - drawHeight) / 2);
  ctx.drawImage(image, drawX, drawY, drawWidth, drawHeight);
}

function drawPreviewPlaceholder() {
  const canvas = els.animationPreviewCanvas;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = state.preview.background;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  els.previewEmptyState.hidden = false;
  els.previewFrameLabel.textContent = "\u5F53\u524D -";
  if (state.magicPreview) {
    drawMagicPlaceholder();
  }
}

function renderPreviewFrameImage(image, frame, selectedCount) {
  const canvas = els.animationPreviewCanvas;
  paintFrameOnCanvas(canvas, image);
  els.previewEmptyState.hidden = true;
  els.previewFrameLabel.textContent = `\u5F53\u524D #${String(frame.index + 1).padStart(3, "0")}`;
  updatePreviewControls(selectedCount);
  syncMagicPreviewFrame(frame, selectedCount, state.preview.currentIndex);
}

function magicVariantElements(config) {
  return {
    panel: els[config.panelId],
    canvas: els[config.canvasId],
    empty: els[config.emptyId],
    frameLabel: els[config.frameLabelId],
    count: els[config.countId],
    progressFill: els[config.progressFillId],
    progressLabel: els[config.progressLabelId],
    sizeLabel: els[config.sizeLabelId],
    exportButton: els[config.exportButtonId],
  };
}

function magicVariantData(key) {
  if (!state.magicPreview) {
    return null;
  }
  if (state.magicPreview.variants?.[key]) {
    return state.magicPreview.variants[key];
  }
  return key === "half" ? state.magicPreview : null;
}

function drawMagicVariantPlaceholder(config, message = "等待缩放处理") {
  const ui = magicVariantElements(config);
  if (!ui.canvas) {
    return;
  }
  const variant = magicVariantData(config.key);
  const canvas = ui.canvas;
  delete canvas.dataset.hasRenderedFrame;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = state.preview.background;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ui.empty.textContent = message;
  ui.empty.hidden = false;
  ui.frameLabel.textContent = `${config.label} -`;
  ui.count.textContent = variant
    ? `\u5DF2\u751F\u6210 ${variant.frame_count || 0} \u5E27`
    : "\u5DF2\u751F\u6210 0 \u5E27";
  ui.progressFill.style.width = "0%";
  ui.progressLabel.textContent = "0 / 0";
  ui.sizeLabel.textContent = formatMagicOutputSize(variant);
  ui.exportButton.disabled = !variant?.frames?.length;
}

function drawMagicPlaceholder(message = "等待缩放处理") {
  MAGIC_VARIANT_CONFIGS.forEach((config) => drawMagicVariantPlaceholder(config, message));
}

function updateMagicVariantControls(config, selectedCount) {
  const ui = magicVariantElements(config);
  const variant = magicVariantData(config.key);
  const hasFrames = selectedCount > 0;
  const currentIndex = hasFrames ? Math.min(state.preview.currentIndex, selectedCount - 1) : 0;
  const progressPercent = hasFrames ? ((currentIndex + 1) / selectedCount) * 100 : 0;
  ui.progressFill.style.width = `${progressPercent}%`;
  ui.progressLabel.textContent = hasFrames
    ? `${currentIndex + 1} / ${selectedCount}`
    : "0 / 0";
  ui.count.textContent = `\u5DF2\u751F\u6210 ${variant?.frame_count || 0} \u5E27`;
  ui.sizeLabel.textContent = formatMagicOutputSize(variant);
  ui.exportButton.disabled = !variant?.frames?.length || Boolean(state.magicPreview?.stale);
}

function updateMagicPreviewControls(selectedCount) {
  MAGIC_VARIANT_CONFIGS.forEach((config) => updateMagicVariantControls(config, selectedCount));
}

function formatMagicOutputSize(magicPreview) {
  const width = Number(magicPreview?.max_width || magicPreview?.frames?.[0]?.width || 0);
  const height = Number(magicPreview?.max_height || magicPreview?.frames?.[0]?.height || 0);
  return width > 0 && height > 0 ? `${width} \u00d7 ${height}` : "-";
}

function magicFrameForSelectedFrame(sourceFrame, selectedPosition, variantKey = "half") {
  const variant = magicVariantData(variantKey);
  if (!variant?.frames || !sourceFrame) {
    return null;
  }
  return variant.frames.find((frame) => Number(frame.source_index) === Number(sourceFrame.index)) || null;
}

function renderMagicPreviewFrameImage(config, image, magicFrame, selectedCount) {
  const ui = magicVariantElements(config);
  paintFrameOnCanvas(ui.canvas, image);
  ui.canvas.dataset.hasRenderedFrame = "true";
  ui.empty.hidden = true;
  ui.frameLabel.textContent = `${config.label} · 源 #${String(Number(magicFrame.source_index || 0) + 1).padStart(3, "0")}`;
  updateMagicVariantControls(config, selectedCount);
}

async function syncMagicVariantPreviewFrame(config, sourceFrame, selectedCount, selectedPosition) {
  const ui = magicVariantElements(config);
  const variant = magicVariantData(config.key);
  if (!variant?.frames?.length || !ui.panel || ui.panel.hidden) {
    return;
  }

  const preview = state.magicPreview;
  preview.renderTokens = preview.renderTokens || {};
  const token = Number(preview.renderTokens[config.key] || 0) + 1;
  preview.renderTokens[config.key] = token;

  const magicFrame = magicFrameForSelectedFrame(sourceFrame, selectedPosition, config.key);
  if (!magicFrame) {
    if (ui.canvas.dataset.hasRenderedFrame === "true") {
      ui.empty.hidden = true;
      ui.frameLabel.textContent = `${config.label} · 新帧待更新`;
    } else {
      drawMagicVariantPlaceholder(config, "新帧待更新");
    }
    updateMagicVariantControls(config, selectedCount);
    return;
  }

  const cached = getCachedPreviewImage(magicFrame.url);
  if (cached) {
    renderMagicPreviewFrameImage(config, cached, magicFrame, selectedCount);
    return;
  }

  try {
    const image = await loadPreviewImage(magicFrame.url);
    if (state.magicPreview !== preview || token !== preview.renderTokens?.[config.key]) {
      return;
    }
    renderMagicPreviewFrameImage(config, image, magicFrame, selectedCount);
  } catch (error) {
    if (state.magicPreview !== preview || token !== preview.renderTokens?.[config.key]) {
      return;
    }
    drawMagicVariantPlaceholder(config, "加载缩放版本失败");
    updateMagicVariantControls(config, selectedCount);
    setStatus(error.message || String(error), "error");
  }
}

function syncMagicPreviewFrame(sourceFrame, selectedCount, selectedPosition) {
  MAGIC_VARIANT_CONFIGS.forEach((config) => {
    void syncMagicVariantPreviewFrame(config, sourceFrame, selectedCount, selectedPosition);
  });
}

function showMagicPreview() {
  const hasAnyVariant = MAGIC_VARIANT_CONFIGS.some((config) => magicVariantData(config.key)?.frames?.length);
  if (!hasAnyVariant) {
    clearMagicPreview();
    return;
  }
  els.comparisonTitle.textContent = "动画版本对比";
  MAGIC_VARIANT_CONFIGS.forEach((config) => {
    const ui = magicVariantElements(config);
    const variant = magicVariantData(config.key);
    ui.panel.hidden = !variant?.frames?.length;
    const description = ui.panel?.querySelector(".variant-card-heading span");
    if (description) {
      description.textContent = config.key === "full"
        ? (state.magicPreview.use_realesrgan ? "ESR ×4 后缩回原尺寸" : "未使用 ESR，保持原尺寸")
        : `${state.magicPreview.use_realesrgan ? "ESR 后" : "直接"}${state.magicPreview.resize_mode_label || magicResizeModeLabel()}缩小`;
    }
    drawMagicVariantPlaceholder(config);
    updateMagicVariantControls(config, getSelectedFrames().length);
    if (variant?.frames?.length) {
      void warmPreviewFrames(variant.frames).catch(() => {});
    }
  });
  state.magicPreview.stale = false;
  els.scaleResultsState.textContent = `已处理 ${state.magicPreview.frame_count || 0} 帧 · ${state.magicPreview.use_realesrgan ? "含 Real-ESRGAN" : "未使用 Real-ESRGAN"} · ${state.magicPreview.resize_mode_label || magicResizeModeLabel()}缩放`;
  els.magicButton.textContent = "更新缩放处理";
  syncAnimationPreview(false);
}

function clearMagicPreview() {
  state.magicPreview = null;
  els.comparisonTitle.textContent = "有效帧预览";
  MAGIC_VARIANT_CONFIGS.forEach((config) => {
    const ui = magicVariantElements(config);
    if (ui.panel) {
      ui.panel.hidden = true;
    }
  });
  if (els.scaleResultsState) els.scaleResultsState.textContent = "未处理版本";
  if (els.magicButton) els.magicButton.textContent = "开始缩放处理";
  drawMagicPlaceholder();
}

async function drawPreviewFrame(frame, selectedCount) {
  if (!frame) {
    drawPreviewPlaceholder();
    updatePreviewControls(selectedCount);
    return;
  }

  const token = ++state.preview.renderToken;
  try {
    const image = await loadPreviewImage(frame.url);
    if (token !== state.preview.renderToken) {
      return;
    }
    renderPreviewFrameImage(image, frame, selectedCount);
  } catch (error) {
    drawPreviewPlaceholder();
    setStatus(error.message || String(error), "error");
  }
}

function drawPreviewFrameFromCache(frame, selectedCount) {
  if (!frame) {
    drawPreviewPlaceholder();
    updatePreviewControls(selectedCount);
    return;
  }

  state.preview.renderToken += 1;
  const image = getCachedPreviewImage(frame.url);
  if (!image) {
    void drawPreviewFrame(frame, selectedCount);
    return;
  }

  renderPreviewFrameImage(image, frame, selectedCount);
}

function syncAnimationPreview(shouldRestartTimer = true) {
  const selectedFrames = getSelectedFrames();
  const selectedCount = selectedFrames.length;

  if (selectedCount === 0) {
    stopPreviewTimer();
    state.preview.currentIndex = 0;
    updatePreviewControls(0);
    drawPreviewPlaceholder();
    return;
  }

  if (state.preview.currentIndex >= selectedCount) {
    state.preview.currentIndex = 0;
  }

  const currentFrame = selectedFrames[state.preview.currentIndex];
  drawPreviewFrameFromCache(currentFrame, selectedCount);
  void warmPreviewFrames(selectedFrames);
  if (shouldRestartTimer) {
    restartPreviewTimer();
  }
}

function currentScaleRequestDescriptor() {
  return {
    job_id: state.job?.job_id || "",
    selected_indices: getSelectedFrames().map((frame) => frame.index),
    resize_mode: normalizeMagicResizeMode(state.magicResizeMode),
    use_realesrgan: Boolean(state.magicUseRealesrgan),
    variant_keys: MAGIC_VARIANT_CONFIGS
      .map((config) => config.key)
      .filter((key) => state.magicVariantKeys.has(key)),
  };
}

function scaleRequestStillCurrent(request) {
  const current = currentScaleRequestDescriptor();
  return (
    current.job_id === request.job_id &&
    current.resize_mode === request.resize_mode &&
    current.use_realesrgan === request.use_realesrgan &&
    current.selected_indices.join(",") === request.selected_indices.join(",") &&
    current.variant_keys.join(",") === request.variant_keys.join(",")
  );
}

async function runMagicPreview() {
  if (state.magicInFlight) {
    setStatus("缩放正在处理，先等当前这轮结束。");
    return;
  }
  if (!state.job) {
    setStatus("还没有可以缩放处理的帧。", "error");
    return;
  }
  if (state.selected.size === 0) {
    setStatus("至少选择一帧再开始缩放处理。", "error");
    syncResultActions();
    return;
  }

  state.magicInFlight = true;
  syncResultActions();
  try {
    await withBusy(els.magicButton, async () => {
      const request = currentScaleRequestDescriptor();
      const selectedFrames = getSelectedFrames();
      const resizeMode = request.resize_mode;
      const resizeModeLabel = magicResizeModeLabel(resizeMode);
      const useRealesrgan = request.use_realesrgan;
      const variantKeys = request.variant_keys;
      const variantLabels = MAGIC_VARIANT_CONFIGS
        .filter((config) => state.magicVariantKeys.has(config.key))
        .map((config) => config.label)
        .join("、");
      const processLabel = useRealesrgan
        ? `Real-ESRGAN 超分后${resizeModeLabel}缩小`
        : `跳过 Real-ESRGAN，直接${resizeModeLabel}缩小`;
      setStatus(`正在处理 ${selectedFrames.length} 帧：${processLabel}，输出 ${variantLabels}...`);
      const data = await apiJson("/api/magic-preview", {
        method: "POST",
        body: request,
      });
      if (state.job?.job_id !== request.job_id) {
        setStatus("缩放处理已完成，但当前任务已经切换；旧结果只保留在缓存中，没有覆盖当前任务。", "error");
        return;
      }
      state.magicPreview = data.magic;
      showMagicPreview();
      if (!scaleRequestStillCurrent(request)) {
        markScaleResultsStale("处理期间帧或参数发生变化；本轮结果已缓存，点击“更新缩放处理”只补算差异。");
        setStatus("本轮缩放结果已缓存，但帧或参数在处理中发生了变化；请点击“更新缩放处理”。", "error");
        return;
      }
      const generatedCount = Number(data.magic.generated_count || 0);
      const generatedVariantCount = Number(data.magic.generated_variant_count || 0);
      const reusedVariantCount = Number(data.magic.reused_variant_count || 0);
      const esrReusedCount = Number(data.magic.esr_reused_count || 0);
      const cacheLabel = generatedCount === 0
        ? "，全部复用缓存，没有重新处理"
        : reusedVariantCount > 0
        ? `，新处理 ${generatedCount} 帧 / ${generatedVariantCount} 个版本，复用 ${reusedVariantCount} 个已有版本`
        : `，新处理 ${generatedCount} 帧 / ${generatedVariantCount} 个版本`;
      const esrCacheLabel = esrReusedCount > 0 ? `，其中 ${esrReusedCount} 帧复用 ESR 中间结果` : "";
      const outputProcessLabel = data.magic.use_realesrgan === false
        ? `未使用 Real-ESRGAN，${data.magic.resize_mode_label || resizeModeLabel}缩小`
        : `Real-ESRGAN 超分后${data.magic.resize_mode_label || resizeModeLabel}缩小`;
      setStatus(`缩放处理完成：${outputProcessLabel}，${data.magic.frame_count} 帧，输出 ${variantLabels}${cacheLabel}${esrCacheLabel}。`, "success");
    });
  } finally {
    state.magicInFlight = false;
    syncResultActions();
  }
}

async function exportMagicFrames(variantKey = "half", button = els.exportMagicFramesButton, exportFormat = "frames") {
  if (!state.magicPreview?.magic_id) {
    setStatus("先生成缩放版本，再导出。", "error");
    return;
  }
  if (state.magicPreview.stale) {
    setStatus("帧或缩放参数已经变化，请先更新缩放处理；系统只会补算差异。", "error");
    return;
  }
  const config = MAGIC_VARIANT_CONFIGS.find((item) => item.key === variantKey) || MAGIC_VARIANT_CONFIGS[0];
  const variant = magicVariantData(config.key);
  if (!variant?.frames?.length) {
    setStatus(`${config.label} \u8FD8\u6CA1\u6709\u53EF\u5BFC\u51FA\u7684\u5E27\u3002`, "error");
    return;
  }

  await withBusy(button, async () => {
    const labels = { frames: "Frames", sprite_sheet: "Sprite Sheet", mov: "透明 MOV", gif: "GIF" };
    const exportLabel = labels[exportFormat] || exportFormat;
    setStatus(`正在导出 ${config.label} 的 ${exportLabel}...`);
    const data = await apiJson("/api/export-magic-frames", {
      method: "POST",
      body: {
        magic_id: state.magicPreview.magic_id,
        variant_key: config.key,
        video_duration_ms: Number(els.previewIntervalInput.value || 100),
        export_format: exportFormat,
      },
    });
    const frameCount = Number(data.export?.frame_count || 0);
    const outputFolder = exportFormat === "frames"
      ? data.export.frames_dir
      : exportFormat === "sprite_sheet"
      ? data.export.sheet_dir
      : data.export.output_dir;
    const opened = await openPath(outputFolder);
    setStatus(
      `${config.label} ${exportLabel} 已导出，共 ${frameCount} 帧${opened ? "，已打开文件夹" : ""}。`,
      "success"
    );
  });
}

function toggleExportOptions() {
  if (!state.job) {
    setStatus("\u8fd8\u6ca1\u6709\u53ef\u5bfc\u51fa\u7684\u5904\u7406\u7ed3\u679c\u3002", "error");
    return;
  }
  if (state.selected.size === 0) {
    setStatus("\u81f3\u5c11\u9009\u4e00\u5e27\u518d\u5bfc\u51fa\u3002", "error");
    syncResultActions();
    return;
  }

  const shouldExpand = els.exportOptions.hidden;
  els.exportOptions.hidden = !shouldExpand;
  els.exportButton.setAttribute("aria-expanded", String(shouldExpand));
  els.exportButton.textContent = shouldExpand ? "收起直接导出" : "直接导出";
  if (shouldExpand) {
    els.scaleProcessingControls.hidden = true;
    els.scaleProcessToggleButton.setAttribute("aria-expanded", "false");
    els.scaleProcessToggleButton.textContent = "缩放处理";
    setStatus("\u8BF7\u9009\u62E9\u8981\u751F\u6210\u7684\u5BFC\u51FA\u683C\u5F0F\u3002");
  }
}

async function exportSelectedFormat(exportFormat, button) {
  if (!state.job || state.selected.size === 0) {
    setStatus("\u81f3\u5c11\u9009\u4e00\u5e27\u518d\u5bfc\u51fa\u3002", "error");
    syncResultActions();
    return;
  }

  const labels = {
    frames: "Frames",
    sprite_sheet: "Spritesheet",
    mov: "\u900F\u660E MOV",
    gif: "GIF",
  };
  const label = labels[exportFormat] || exportFormat;
  await withBusy(button, async () => {
    const directionLabel = state.preview.isReversed ? "\u5012\u5E8F" : "";
    setStatus(`\u6B63\u5728${directionLabel}\u5BFC\u51FA ${label}...`);
    const selectedFrames = getSelectedFrames();
    const data = await apiJson("/api/export", {
      method: "POST",
      body: {
        job_id: state.job.job_id,
        selected_indices: selectedFrames.map((frame) => frame.index),
        video_duration_ms: Number(els.previewIntervalInput.value || 100),
        export_format: exportFormat,
      },
    });
    const outputFolder = exportFormat === "frames"
      ? data.export.frames_dir
      : exportFormat === "sprite_sheet"
      ? data.export.sheet_dir
      : data.export.output_dir;
    state.exportResult = null;
    renderExportResult();
    persistSession();
    const opened = await openPath(outputFolder);
    if (opened) {
      setStatus(`${label} \u5BFC\u51FA\u5B8C\u6210\uFF0C\u5DF2\u6253\u5F00\u6587\u4EF6\u5939\u3002`, "success");
    }
  });
}

function renderExportResult() {
  if (!state.exportResult || state.exportResult.frames_dir || state.exportResult.sheet_dir) {
    els.exportResult.hidden = true;
    els.exportResult.innerHTML = "";
    return;
  }

  els.exportResult.hidden = false;
  const fileLinks = [
    ["MOV", state.exportResult.mov_url || state.exportResult.video_url, state.exportResult.mov_name || state.exportResult.video_name],
    ["GIF", state.exportResult.gif_url, state.exportResult.gif_name],
  ]
    .filter(([, url]) => Boolean(url))
    .map(([label, url, name]) => `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${label}: ${escapeHtml(name || url)}</a>`)
    .join("");
  const exportedContents = [
    state.exportResult.mov_url || state.exportResult.video_url ? "\u900F\u660E MOV" : "",
    state.exportResult.gif_url ? "GIF" : "",
  ]
    .filter(Boolean)
    .join(" / ");

  els.exportResult.innerHTML = `
    <div class="result-summary">
      ${summaryCard("\u5bfc\u51fa\u5e27\u6570", `${state.exportResult.frame_count} \u5e27`)}
      ${summaryCard("\u5bfc\u51fa\u5185\u5bb9", escapeHtml(exportedContents || "\u5DF2\u5BFC\u51FA"))}
    </div>
    <div class="link-list">
      ${fileLinks}
    </div>
  `;
  persistSession();
}

function summaryCard(label, value) {
  return `
    <div class="summary-card">
      <span class="meta-label">${label}</span>
      <strong>${value}</strong>
    </div>
  `;
}

function formatFfmpegAccelLabel(ffmpegAccel) {
  if (!ffmpegAccel || typeof ffmpegAccel !== "object") {
    return "CPU";
  }

  const usedMode = String(ffmpegAccel.used_mode || "cpu").toLowerCase();
  const selectedMode = ffmpegAccel.selected_mode ? String(ffmpegAccel.selected_mode).toLowerCase() : "";
  const requestedMode = String(ffmpegAccel.requested_mode || "auto").toLowerCase();

  if (usedMode !== "cpu") {
    return `GPU (${usedMode})`;
  }
  if (ffmpegAccel.fallback_to_cpu && selectedMode) {
    return `CPU (${selectedMode} fallback)`;
  }
  if (requestedMode === "cpu") {
    return "CPU (manual)";
  }
  return "CPU";
}

function updateChromaVisibility() {
  const matteMode = currentMatteMode();
  syncCorridorScreenFromMatteMode(matteMode);
  const chromaEnabled = matteMode !== "none";
  const isChroma = matteMode === "chroma";
  const isAi = chromaEnabled && matteModeUsesBiRefNet(matteMode);
  const isLuma = chromaEnabled && matteModeUsesLuma(matteMode);
  const isCorridor = chromaEnabled && matteModeUsesCorridorKey(matteMode);
  const corridorUsesChroma = isCorridor && els.corridorCoarseMaskInput.value === "chroma";
  const usesAiLivePreview = isAi || isCorridor;
  const usesSpillControls = chromaEnabled;
  const usesKeyColorControls = isChroma;
  const isManual = els.keyModeInput.value === "manual";
  syncChromaToleranceLabel();
  els.corridorEnabledInput.checked = isCorridor;
  els.aiLivePreviewOption.hidden = !usesAiLivePreview;
  els.matteModeInput.disabled = false;
  els.keyModeInput.closest(".field").style.display = usesKeyColorControls ? "" : "none";
  els.manualColorField.style.display = usesKeyColorControls && isManual ? "" : "none";
  document.querySelectorAll(".matte-target-group").forEach((node) => {
    node.style.display = usesKeyColorControls || isLuma || isCorridor ? "" : "none";
  });
  document.querySelectorAll(".chroma-only").forEach((node) => {
    node.style.display = isChroma ? "" : "none";
  });
  document.querySelectorAll(".chroma-guide-only").forEach((node) => {
    node.style.display = isChroma || corridorUsesChroma ? "" : "none";
  });
  els.thresholdInput.setAttribute(
    "aria-label",
    corridorUsesChroma ? "CorridorKey 继承的 Chroma 容差" : "Chroma 背景色容差"
  );
  document.querySelectorAll(".spill-matte-only").forEach((node) => {
    node.style.display = usesSpillControls ? "" : "none";
  });
  document.querySelectorAll(".ai-matte-only").forEach((node) => {
    node.style.display = isAi ? "" : "none";
  });
  document.querySelectorAll(".luma-matte-only").forEach((node) => {
    node.style.display = isLuma ? "" : "none";
  });
  document.querySelectorAll(".corridor-capable-only").forEach((node) => {
    node.style.display = "none";
  });
  document.querySelectorAll(".corridor-key-only").forEach((node) => {
    node.style.display = isCorridor ? "block" : "none";
  });
  document.querySelectorAll(".corridor-seed-only").forEach((node) => {
    node.style.display = isCorridor ? "grid" : "none";
  });
  syncCorridorControlState();
  syncBirefnetControlState();
  if (isCorridor && els.corridorPreviewState.dataset.state !== "stale") {
    const previewMode = state.processPreview?.matte?.mode || state.processPreview?.options?.matte_mode;
    setCorridorPreviewState(previewMode === "corridorkey" ? "current" : "empty");
  }
  if (isAi && els.birefnetPreviewState.dataset.state !== "stale") {
    const previewMode = state.processPreview?.matte?.mode || state.processPreview?.options?.matte_mode;
    setBirefnetPreviewState(previewMode === "birefnet" ? "current" : "empty");
  }
  if ((!usesKeyColorControls || !isManual) && state.keySamplingActive) {
    setKeySamplingActive(false, { announce: false });
  }
}

function syncCorridorControlState() {
  const despill = clamp(Number(els.corridorDespillInput.value || 0), 0, 1);
  const refiner = clamp(Number(els.corridorRefinerInput.value || 0), 0, 3);
  const coarseMaskLabel = els.corridorCoarseMaskInput.value === "birefnet" ? "BiRefNet 粗遮罩" : "Chroma 粗遮罩";
  els.corridorDespillInput.value = String(despill);
  els.corridorRefinerInput.value = String(refiner);
  els.corridorDespillValueLabel.textContent = despill.toFixed(2);
  els.corridorRefinerValueLabel.textContent = refiner.toFixed(2);
  els.corridorDespeckleSizeInput.disabled = !els.corridorDespeckleEnabledInput.checked;
  els.corridorGarbagePixelsInput.disabled = !els.corridorGarbageEnabledInput.checked;
  const despeckleSummary = els.corridorDespeckleEnabledInput.checked
    ? `散点 ${Number(els.corridorDespeckleSizeInput.value || 0)}`
    : "散点关";
  const garbageSummary = els.corridorGarbageEnabledInput.checked
    ? `遮罩 ${Number(els.corridorGarbagePixelsInput.value || 0)} px`
    : "遮罩关";
  els.corridorSettingsSummaryValue.textContent =
    `${coarseMaskLabel} · 去溢色 ${despill.toFixed(2)} · 细化 ${refiner.toFixed(2)} · ${despeckleSummary} · ${garbageSummary}`;
}

function syncBirefnetControlState() {
  const edgeShrink = clamp(Number(els.birefnetEdgeShrinkInput.value || 0), 0, 8);
  els.birefnetEdgeShrinkInput.value = String(edgeShrink);
  els.birefnetEdgeShrinkValueLabel.textContent = `${edgeShrink} px`;
}

function setBirefnetPreviewState(nextState) {
  if (!els.birefnetPreviewState) {
    return;
  }
  const labels = {
    empty: "\u5c1a\u672a\u9884\u89c8",
    stale: "\u53c2\u6570\u5df2\u4fee\u6539",
    loading: "\u6b63\u5728\u81ea\u52a8\u9884\u89c8",
    current: "\u5f53\u524d\u53c2\u6570\u5df2\u9884\u89c8",
  };
  const normalized = labels[nextState] ? nextState : "empty";
  els.birefnetPreviewState.dataset.state = normalized;
  els.birefnetPreviewState.textContent = labels[normalized];
}

function markBirefnetPreviewStale() {
  if (currentMatteMode() !== "birefnet") {
    return;
  }
  const previewMode = state.processPreview?.matte?.mode || state.processPreview?.options?.matte_mode;
  setBirefnetPreviewState(previewMode === "birefnet" ? "stale" : "empty");
}

function scheduleBirefnetLivePreview(delay = 220) {
  if (!aiLivePreviewEnabled() || currentMatteMode() !== "birefnet" || !state.upload || preprocessSmoothingInstalling) {
    return;
  }
  window.clearTimeout(birefnetPreviewTimerId);
  birefnetPreviewTimerId = window.setTimeout(runBirefnetLivePreview, Math.max(0, delay));
}

async function runBirefnetLivePreview() {
  birefnetPreviewTimerId = null;
  if (!aiLivePreviewEnabled() || currentMatteMode() !== "birefnet" || !state.upload) {
    birefnetPreviewPending = false;
    return;
  }
  if (birefnetPreviewInFlight || els.previewFrameButton.disabled) {
    birefnetPreviewPending = true;
    scheduleBirefnetLivePreview(160);
    return;
  }

  birefnetPreviewInFlight = true;
  birefnetPreviewPending = false;
  setBirefnetPreviewState("loading");
  try {
    await previewCurrentFrame({ preserveView: true });
  } finally {
    birefnetPreviewInFlight = false;
    if (birefnetPreviewPending) {
      scheduleBirefnetLivePreview(0);
    } else if (els.birefnetPreviewState.dataset.state === "loading") {
      markBirefnetPreviewStale();
    }
  }
}

function setCorridorPreviewState(nextState) {
  if (!els.corridorPreviewState) {
    return;
  }
  const labels = {
    empty: "尚未预览",
    stale: "参数已修改",
    loading: "正在自动预览",
    current: "当前参数已预览",
  };
  const normalized = labels[nextState] ? nextState : "empty";
  els.corridorPreviewState.dataset.state = normalized;
  els.corridorPreviewState.textContent = labels[normalized];
}

function markCorridorPreviewStale() {
  if (!matteModeUsesCorridorKey(currentMatteMode())) {
    return;
  }
  const previewMode = state.processPreview?.matte?.mode || state.processPreview?.options?.matte_mode;
  setCorridorPreviewState(previewMode === "corridorkey" ? "stale" : "empty");
}

function scheduleCorridorLivePreview(delay = 220) {
  if (!aiLivePreviewEnabled() || !matteModeUsesCorridorKey(currentMatteMode()) || !state.upload || preprocessSmoothingInstalling) {
    return;
  }
  window.clearTimeout(corridorPreviewTimerId);
  corridorPreviewTimerId = window.setTimeout(runCorridorLivePreview, Math.max(0, delay));
}

async function runCorridorLivePreview() {
  corridorPreviewTimerId = null;
  if (!aiLivePreviewEnabled() || !matteModeUsesCorridorKey(currentMatteMode()) || !state.upload) {
    corridorPreviewPending = false;
    return;
  }
  if (corridorPreviewInFlight || els.previewFrameButton.disabled) {
    corridorPreviewPending = true;
    scheduleCorridorLivePreview(160);
    return;
  }

  corridorPreviewInFlight = true;
  corridorPreviewPending = false;
  setCorridorPreviewState("loading");
  try {
    await previewCurrentFrame({ preserveView: true });
  } finally {
    corridorPreviewInFlight = false;
    if (corridorPreviewPending) {
      scheduleCorridorLivePreview(0);
    } else if (els.corridorPreviewState.dataset.state === "loading") {
      markCorridorPreviewStale();
    }
  }
}

function syncChromaToleranceLabel() {
  const value = clamp(Number(els.thresholdInput.value || 0), 0, 180);
  els.thresholdInput.value = String(value);
  els.thresholdValueLabel.textContent = String(value);
}

function rememberMatteThreshold(mode) {
  const storageMode = matteThresholdStorageMode(mode);
  if (!(storageMode in MATTE_THRESHOLD_DEFAULTS)) {
    return;
  }
  state.matteThresholds[storageMode] = clamp(Number(els.thresholdInput.value || 0), 0, 180);
}

function applyMatteThreshold(mode) {
  const storageMode = matteThresholdStorageMode(mode);
  if (!(storageMode in MATTE_THRESHOLD_DEFAULTS)) {
    return;
  }
  const value = state.matteThresholds[storageMode] ?? MATTE_THRESHOLD_DEFAULTS[storageMode];
  els.thresholdInput.value = String(clamp(Number(value), 0, 180));
  syncChromaToleranceLabel();
}

function handleMatteToleranceInput() {
  syncChromaToleranceLabel();
  const matteMode = currentMatteMode();
  rememberMatteThreshold(matteMode);
  if (matteModeUsesCorridorKey(matteMode)) {
    markCorridorPreviewStale();
    scheduleCorridorLivePreview(220);
    return;
  }
  requestChromaPreview();
}

function requestChromaPreview() {
  syncChromaToleranceLabel();
  if (
    currentMatteMode() !== "chroma"
    || !state.upload
    || preprocessSmoothingInstalling
    || (els.keyModeInput.value === "manual" && state.manualKeyColors.length === 0)
  ) {
    return;
  }
  window.cancelAnimationFrame(chromaPreviewRafId);
  chromaPreviewRafId = window.requestAnimationFrame(renderInstantChromaPreview);
}

function instantChromaSourceElement() {
  if (!els.previewSourceImage.hidden && els.previewSourceImage.complete && els.previewSourceImage.naturalWidth) {
    return els.previewSourceImage;
  }
  if (!els.videoPreview.hidden && els.videoPreview.readyState >= 2 && els.videoPreview.videoWidth) {
    return els.videoPreview;
  }
  if (!els.mediaPreviewImage.hidden && els.mediaPreviewImage.complete && els.mediaPreviewImage.naturalWidth) {
    return els.mediaPreviewImage;
  }
  return null;
}

function hexColorToRgb(color) {
  const normalized = normalizeHexColor(color, "");
  if (!normalized) {
    return null;
  }
  return [
    Number.parseInt(normalized.slice(1, 3), 16),
    Number.parseInt(normalized.slice(3, 5), 16),
    Number.parseInt(normalized.slice(5, 7), 16),
  ];
}

function detectInstantChromaKeyColor(pixels, width, height) {
  const buckets = new Map();
  const step = Math.max(1, Math.floor(Math.min(width, height) / 80));
  const addPixel = (x, y) => {
    const index = ((y * width) + x) * 4;
    if (pixels[index + 3] === 0) {
      return;
    }
    const red = pixels[index];
    const green = pixels[index + 1];
    const blue = pixels[index + 2];
    const bucketKey = `${Math.round(red / 24)},${Math.round(green / 24)},${Math.round(blue / 24)}`;
    const bucket = buckets.get(bucketKey) || { count: 0, red: 0, green: 0, blue: 0 };
    bucket.count += 1;
    bucket.red += red;
    bucket.green += green;
    bucket.blue += blue;
    buckets.set(bucketKey, bucket);
  };
  for (let x = 0; x < width; x += step) {
    addPixel(x, 0);
    addPixel(x, height - 1);
  }
  for (let y = step; y < height - step; y += step) {
    addPixel(0, y);
    addPixel(width - 1, y);
  }

  const best = [...buckets.values()].sort((first, second) => second.count - first.count)[0];
  return best
    ? [Math.round(best.red / best.count), Math.round(best.green / best.count), Math.round(best.blue / best.count)]
    : [0, 255, 0];
}

function instantChromaKeyColors(pixels, width, height) {
  if (els.keyModeInput.value === "manual") {
    return state.manualKeyColors.map(hexColorToRgb).filter(Boolean);
  }
  const previewMode = state.processPreview?.matte?.mode || state.processPreview?.options?.matte_mode;
  const previewColors = previewMode === "chroma"
    ? state.processPreview?.key_colors || [state.processPreview?.key_color]
    : [];
  const cachedColors = previewColors.map(hexColorToRgb).filter(Boolean);
  return cachedColors.length > 0 ? cachedColors : [detectInstantChromaKeyColor(pixels, width, height)];
}

function erodeInstantChromaAlpha(pixels, width, height, radius) {
  if (radius <= 0) {
    return;
  }
  const alpha = new Uint8ClampedArray(width * height);
  for (let pixelIndex = 0; pixelIndex < alpha.length; pixelIndex += 1) {
    alpha[pixelIndex] = pixels[(pixelIndex * 4) + 3];
  }
  for (let y = 0; y < height; y += 1) {
    const minY = Math.max(0, y - radius);
    const maxY = Math.min(height - 1, y + radius);
    for (let x = 0; x < width; x += 1) {
      const minX = Math.max(0, x - radius);
      const maxX = Math.min(width - 1, x + radius);
      let minimum = 255;
      for (let sampleY = minY; sampleY <= maxY && minimum > 0; sampleY += 1) {
        for (let sampleX = minX; sampleX <= maxX; sampleX += 1) {
          minimum = Math.min(minimum, alpha[(sampleY * width) + sampleX]);
          if (minimum === 0) break;
        }
      }
      pixels[(((y * width) + x) * 4) + 3] = minimum;
    }
  }
}

function renderInstantChromaPreview() {
  chromaPreviewRafId = null;
  const source = instantChromaSourceElement();
  if (!source) {
    return;
  }

  const sourceWidth = source instanceof HTMLVideoElement ? source.videoWidth : source.naturalWidth;
  const sourceHeight = source instanceof HTMLVideoElement ? source.videoHeight : source.naturalHeight;
  if (!sourceWidth || !sourceHeight) {
    return;
  }

  const processedStage = els.previewProcessedImage.closest(".image-preview-stage");
  const longEdge = clamp(Math.round(Math.max(processedStage.clientWidth * 2, 640)), 640, 960);
  const scale = Math.min(1, longEdge / Math.max(sourceWidth, sourceHeight));
  const width = Math.max(1, Math.round(sourceWidth * scale));
  const height = Math.max(1, Math.round(sourceHeight * scale));
  chromaPreviewCanvas ||= document.createElement("canvas");
  chromaPreviewCanvas.width = width;
  chromaPreviewCanvas.height = height;
  const context = chromaPreviewCanvas.getContext("2d", { willReadFrequently: true });
  context.clearRect(0, 0, width, height);
  context.drawImage(source, 0, 0, width, height);

  const shouldPopulateSource = els.previewSourceImage.hidden || !els.previewSourceImage.getAttribute("src");
  const sourceDataUrl = shouldPopulateSource ? chromaPreviewCanvas.toDataURL("image/png") : "";
  const imageData = context.getImageData(0, 0, width, height);
  const pixels = imageData.data;
  let sourceHasTransparency = false;
  for (let index = 3; index < pixels.length; index += 4) {
    if (pixels[index] < 255) {
      sourceHasTransparency = true;
      break;
    }
  }
  const keyColors = instantChromaKeyColors(pixels, width, height);
  if (keyColors.length === 0) {
    return;
  }

  const threshold = Number(els.thresholdInput.value || 0);
  const softness = Math.max(0, Number(els.softnessInput.value || 0));
  const maxDistance = softness > 0 ? threshold + softness : Math.max(threshold, 1);
  const despillStrength = Math.max(0, Number(els.despillInput.value || 0));
  for (let index = 0; index < pixels.length; index += 4) {
    const red = pixels[index];
    const green = pixels[index + 1];
    const blue = pixels[index + 2];
    const sourceAlpha = pixels[index + 3];
    let nearestSquared = Number.POSITIVE_INFINITY;
    for (const [keyRed, keyGreen, keyBlue] of keyColors) {
      const distanceSquared = ((red - keyRed) ** 2) + ((green - keyGreen) ** 2) + ((blue - keyBlue) ** 2);
      if (distanceSquared < nearestSquared) nearestSquared = distanceSquared;
    }
    const distance = Math.sqrt(nearestSquared);
    const chromaAlpha = distance <= threshold
      ? 0
      : softness <= 0 || distance >= maxDistance
        ? 255
        : Math.floor(((distance - threshold) / softness) * 255);
    const alpha = sourceHasTransparency
      ? Math.round((sourceAlpha * chromaAlpha) / 255)
      : chromaAlpha;
    const spill = Math.max(0, green - Math.max(red, blue));
    const closeness = Math.max(0, 1 - Math.min(distance / maxDistance, 1));
    const reduction = Math.floor(spill * despillStrength * Math.max(closeness, 1 - (alpha / 255)));
    pixels[index + 1] = Math.max(0, green - reduction);
    pixels[index + 3] = alpha;
  }
  erodeInstantChromaAlpha(pixels, width, height, Math.max(0, Math.round(Number(els.haloInput.value || 0))));
  context.putImageData(imageData, 0, 0);

  if (shouldPopulateSource) {
    els.previewSourceImage.src = sourceDataUrl;
    els.previewSourceImage.hidden = false;
    els.previewSourceEmpty.hidden = true;
    setProcessPreviewStageActive("source", true);
  }
  els.previewProcessedImage.src = chromaPreviewCanvas.toDataURL("image/png");
  els.previewProcessedImage.hidden = false;
  els.previewProcessedEmpty.hidden = true;
  setProcessPreviewStageActive("processed", true);
  state.instantChromaPreviewActive = true;
  const sampleLabel = keyColors.length > 1 ? `背景色样 ${keyColors.length} 个` : "背景色样 1 个";
  els.processPreviewKeyLabel.textContent = `浏览器即时预览 / Chroma / ${sampleLabel}`;
  updateSavePreviewButton();
}

function clearInstantChromaPreviewForEmptySamples() {
  if (currentMatteMode() !== "chroma" || els.keyModeInput.value !== "manual") {
    return;
  }
  window.cancelAnimationFrame(chromaPreviewRafId);
  chromaPreviewRafId = null;
  els.previewProcessedImage.hidden = true;
  els.previewProcessedEmpty.textContent = "添加背景色样后即时预览";
  els.previewProcessedEmpty.hidden = false;
  state.instantChromaPreviewActive = true;
  els.processPreviewKeyLabel.textContent = "浏览器即时预览 / Chroma / 尚未添加背景色样";
  updateSavePreviewButton();
}

function validateManualChromaSamples() {
  if (
    matteModeUsesChromaSeed(currentMatteMode())
    && els.keyModeInput.value === "manual"
    && state.manualKeyColors.length === 0
  ) {
    setStatus("手动指定背景色时，请先在 Chroma 中从画面或色板添加至少一个色样。", "error");
    return false;
  }
  return true;
}

function normalizeManualKeyColorList(colors) {
  const normalized = [];
  (Array.isArray(colors) ? colors : []).forEach((color) => {
    const value = normalizeHexColor(color, "");
    if (value && !normalized.includes(value) && normalized.length < MAX_MANUAL_KEY_COLORS) {
      normalized.push(value);
    }
  });
  return normalized;
}

function setManualKeyColors(colors, { persist = true } = {}) {
  state.manualKeyColors = normalizeManualKeyColorList(colors);
  syncManualColorLabel();
  renderManualKeySamples();
  if (persist) {
    persistSession();
  }
}

function addPaletteKeyColor() {
  const color = normalizeHexColor(els.manualKeyInput.value, "#00FF00");
  if (addManualKeyColor(color)) {
    setStatus(`已添加色板颜色 ${color}。`, "success");
    requestChromaPreview();
  }
}

function colorDistance(first, second) {
  const values = [first, second].map((color) => [
    Number.parseInt(color.slice(1, 3), 16),
    Number.parseInt(color.slice(3, 5), 16),
    Number.parseInt(color.slice(5, 7), 16),
  ]);
  return Math.sqrt(
    ((values[0][0] - values[1][0]) ** 2)
    + ((values[0][1] - values[1][1]) ** 2)
    + ((values[0][2] - values[1][2]) ** 2)
  );
}

function addManualKeyColor(color) {
  const normalized = normalizeHexColor(color, "");
  if (!normalized) {
    return false;
  }
  if (state.keySamplingReplacePrimary) {
    state.keySamplingReplacePrimary = false;
    setManualKeyColors([normalized]);
    return true;
  }
  if (state.manualKeyColors.some((sample) => colorDistance(sample, normalized) < KEY_SAMPLE_DUPLICATE_DISTANCE)) {
    setStatus(`这个颜色与已有色样太接近：${normalized}`);
    return false;
  }
  if (state.manualKeyColors.length >= MAX_MANUAL_KEY_COLORS) {
    setStatus(`最多保留 ${MAX_MANUAL_KEY_COLORS} 个背景色样。`, "error");
    return false;
  }
  setManualKeyColors([...state.manualKeyColors, normalized]);
  return true;
}

function renderManualKeySamples() {
  if (!els.manualKeySamples) {
    return;
  }
  els.manualKeySamples.innerHTML = state.manualKeyColors.length > 0
    ? state.manualKeyColors.map((color, index) => `
    <button
      class="key-sample-chip"
      type="button"
      data-key-sample-index="${index}"
      style="--sample-color: ${color}"
      title="删除色样 ${color}"
      aria-label="删除背景色样 ${color}"
    >${color}</button>
  `).join("")
    : '<span class="manual-key-empty">尚未添加背景色样</span>';
  els.manualKeySampleCount.textContent = `${state.manualKeyColors.length} / ${MAX_MANUAL_KEY_COLORS}`;
  els.clearExtraKeySamplesButton.disabled = state.manualKeyColors.length === 0;
}

function handleManualKeySampleClick(event) {
  const button = event.target.closest("[data-key-sample-index]");
  if (!button) {
    return;
  }
  const index = Number(button.dataset.keySampleIndex);
  if (!Number.isInteger(index) || index < 0 || index >= state.manualKeyColors.length) {
    return;
  }
  const [removedColor] = state.manualKeyColors.splice(index, 1);
  state.keySampleMarkers = state.keySampleMarkers.filter((marker) => marker.color !== removedColor);
  setManualKeyColors(state.manualKeyColors);
  renderKeySampleMarkers();
  setStatus(
    state.manualKeyColors.length > 0
      ? `已删除背景色样 ${removedColor}。`
      : `已删除最后一个背景色样；添加颜色后再预览或处理。`,
    "success"
  );
  if (state.manualKeyColors.length > 0) {
    requestChromaPreview();
  } else {
    clearInstantChromaPreviewForEmptySamples();
  }
}

function clearExtraManualKeyColors() {
  state.keySampleMarkers = [];
  setManualKeyColors([]);
  renderKeySampleMarkers();
  clearInstantChromaPreviewForEmptySamples();
  setStatus("已清空全部背景色样。", "success");
}

function clearKeySampleMarkers() {
  state.keySampleMarkers = [];
  renderKeySampleMarkers();
}

function renderKeySampleMarkers() {
  if (!els.keySampleMarkers) {
    return;
  }
  els.keySampleMarkers.innerHTML = state.keySampleMarkers.map((marker) => `
    <span
      class="key-sample-marker"
      style="left: ${marker.x}%; top: ${marker.y}%; --sample-color: ${marker.color}"
    ></span>
  `).join("");
}

function setKeySamplingActive(active, { announce = true } = {}) {
  const shouldActivate = Boolean(active);
  if (shouldActivate) {
    if (!state.upload) {
      if (announce) setStatus("先导入素材，再从画面添加背景色。", "error");
      return;
    }
    if (currentMatteMode() !== "chroma") {
      if (announce) setStatus("当前处理方式不使用背景色样。", "error");
      return;
    }
    state.keySamplingReplacePrimary = els.keyModeInput.value !== "manual";
    els.keyModeInput.value = "manual";
    updateChromaVisibility();
    if (isVideoUpload()) {
      els.videoPreview.pause();
    }
  } else {
    state.keySamplingReplacePrimary = false;
    clearKeySampleMarkers();
  }

  state.keySamplingActive = shouldActivate;
  els.videoWrap.classList.toggle("is-key-sampling", shouldActivate);
  els.keySamplingOverlay.hidden = !shouldActivate;
  els.keySamplingToggleButton.textContent = shouldActivate ? "结束取色" : "从画面添加";
  els.keySamplingToggleButton.classList.toggle("active", shouldActivate);
  if (announce) {
    setStatus(
      shouldActivate
        ? "取色已开启：可连续点击源画面的不同背景区域，按 Esc 结束。"
        : `取色结束，已保留 ${state.manualKeyColors.length} 个背景色样。`,
      shouldActivate ? undefined : "success"
    );
  }
  persistSession();
}

function sourceElementForKeySampling() {
  if (!els.videoPreview.hidden && els.videoPreview.readyState >= 2) {
    return els.videoPreview;
  }
  if (!els.mediaPreviewImage.hidden && els.mediaPreviewImage.complete) {
    return els.mediaPreviewImage;
  }
  return null;
}

function sampleSourceColor(source, event) {
  const rect = source.getBoundingClientRect();
  const sourceWidth = source instanceof HTMLVideoElement ? source.videoWidth : source.naturalWidth;
  const sourceHeight = source instanceof HTMLVideoElement ? source.videoHeight : source.naturalHeight;
  if (!sourceWidth || !sourceHeight || !rect.width || !rect.height) {
    return null;
  }

  const scale = Math.min(rect.width / sourceWidth, rect.height / sourceHeight);
  const renderedWidth = sourceWidth * scale;
  const renderedHeight = sourceHeight * scale;
  const renderedLeft = rect.left + ((rect.width - renderedWidth) / 2);
  const renderedTop = rect.top + ((rect.height - renderedHeight) / 2);
  const localX = event.clientX - renderedLeft;
  const localY = event.clientY - renderedTop;
  if (localX < 0 || localY < 0 || localX > renderedWidth || localY > renderedHeight) {
    return null;
  }

  const centerX = clamp(Math.round(localX / scale), 0, sourceWidth - 1);
  const centerY = clamp(Math.round(localY / scale), 0, sourceHeight - 1);
  const sampleWidth = Math.min(5, sourceWidth);
  const sampleHeight = Math.min(5, sourceHeight);
  const sampleX = clamp(centerX - Math.floor(sampleWidth / 2), 0, sourceWidth - sampleWidth);
  const sampleY = clamp(centerY - Math.floor(sampleHeight / 2), 0, sourceHeight - sampleHeight);
  const canvas = document.createElement("canvas");
  canvas.width = sampleWidth;
  canvas.height = sampleHeight;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(source, sampleX, sampleY, sampleWidth, sampleHeight, 0, 0, sampleWidth, sampleHeight);
  const pixels = context.getImageData(0, 0, sampleWidth, sampleHeight).data;
  const channels = [[], [], []];
  for (let index = 0; index < pixels.length; index += 4) {
    if (pixels[index + 3] === 0) {
      continue;
    }
    channels[0].push(pixels[index]);
    channels[1].push(pixels[index + 1]);
    channels[2].push(pixels[index + 2]);
  }
  if (channels[0].length === 0) {
    return null;
  }
  const median = (values) => values.sort((a, b) => a - b)[Math.floor(values.length / 2)];
  const color = `#${channels.map((values) => median(values).toString(16).padStart(2, "0")).join("")}`.toUpperCase();
  const wrapRect = els.videoWrap.getBoundingClientRect();
  return {
    color,
    marker: {
      color,
      x: ((event.clientX - wrapRect.left) / wrapRect.width) * 100,
      y: ((event.clientY - wrapRect.top) / wrapRect.height) * 100,
    },
  };
}

function handleSourceKeySampleClick(event) {
  if (!state.keySamplingActive) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  const source = sourceElementForKeySampling();
  if (!source) {
    setStatus("源画面尚未加载完成，请稍后再取色。", "error");
    return;
  }
  try {
    const sample = sampleSourceColor(source, event);
    if (!sample) {
      setStatus("请点击画面内容，不要点击两侧留空区域。", "error");
      return;
    }
    if (addManualKeyColor(sample.color)) {
      state.keySampleMarkers.push(sample.marker);
      renderKeySampleMarkers();
      setStatus(`已添加色样 ${sample.color}，当前共 ${state.manualKeyColors.length} 个。`, "success");
      requestChromaPreview();
    }
  } catch (error) {
    setStatus(`取色失败：${error.message}`, "error");
  }
}

function syncManualColorLabel() {
  els.manualKeyLabel.textContent = normalizeHexColor(els.manualKeyInput.value, "#00FF00");
}

async function openPath(path) {
  try {
    await apiJson("/api/open-path", {
      method: "POST",
      body: { path },
    });
    return true;
  } catch (error) {
    setStatus(`\u6253\u5f00\u76ee\u5f55\u5931\u8d25\uff1a${error.message}`, "error");
    return false;
  }
}

async function apiJson(url, options = {}) {
  const fetchOptions = { ...options };
  if (fetchOptions.body && !(fetchOptions.body instanceof FormData)) {
    fetchOptions.headers = {
      "Content-Type": "application/json",
      ...(fetchOptions.headers || {}),
    };
    fetchOptions.body = JSON.stringify(fetchOptions.body);
  }

  let response;
  try {
    response = await fetch(url, fetchOptions);
  } catch (error) {
    throw new Error(`\u8BF7\u6C42\u5931\u8D25\uFF1A${error.message || String(error)}\u3002\u8BF7\u786E\u8BA4 Sprite Video Lab \u540E\u7AEF\u6B63\u5728\u8FD0\u884C\uFF0C\u5E76\u5DF2\u91CD\u542F\u5230\u6700\u65B0\u7248\u672C\u3002`);
  }

  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    const detail = (await response.text()).replace(/\s+/g, " ").trim().slice(0, 180);
    throw new Error(`\u63A5\u53E3\u672A\u8FD4\u56DE JSON\uFF08HTTP ${response.status}\uFF09\u3002\u8BF7\u91CD\u542F Sprite Video Lab \u540E\u7AEF\u540E\u518D\u8BD5\u3002${detail ? ` ${detail}` : ""}`);
  }

  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

async function withBusy(button, task) {
  button.disabled = true;
  try {
    await task();
  } catch (error) {
    setStatus(error.message || String(error), "error");
  } finally {
    button.disabled = false;
    if (button === els.previewFrameButton || button === els.processButton) {
      updateSegmentConfirmationUI();
    }
  }
}

function setStatus(message, tone = "") {
  els.appStatus.textContent = message;
  els.appStatus.className = `status-message${tone ? ` ${tone}` : ""}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function formatSeconds(value) {
  return `${Number(value || 0).toFixed(2)}s`;
}
