// Cached DOM element references for the sorter page.
// Split out of the former monolithic sorter index.js.

// DOM Elements
const videoPlayer = document.getElementById("mainVideoPlayer");
const preloadVideoPlayer = document.getElementById("preloadVideoPlayer");
const videoContainer = document.getElementById("videoContainer");

// Duplicate Alert UI Elements
const duplicateBanner = document.getElementById("duplicateBanner");
const autoTrashDuplicateToggle = document.getElementById("autoTrashDuplicateToggle");
const duplicateBadgeType = document.getElementById("duplicateBadgeType");
const duplicateMatchInfo = document.getElementById("duplicateMatchInfo");
const toggleDuplicateCompareBtn = document.getElementById("toggleDuplicateCompareBtn");
const duplicateCompareText = document.getElementById("duplicateCompareText");
const trashIncomingDuplicateBtn = document.getElementById("trashIncomingDuplicateBtn");
const dismissDuplicateBtn = document.getElementById("dismissDuplicateBtn");
const duplicateCompareContainer = document.getElementById("duplicateCompareContainer");
const compareIncomingName = document.getElementById("compareIncomingName");
const compareIncomingSize = document.getElementById("compareIncomingSize");
const compareExistingName = document.getElementById("compareExistingName");
const compareExistingSize = document.getElementById("compareExistingSize");
const compareExistingVideo = document.getElementById("compareExistingVideo");
const emptyVideoState = document.getElementById("emptyVideoState");
const currentVideoTitle = document.getElementById("currentVideoTitle");
const currentVideoSize = document.getElementById("currentVideoSize");
const currentVideoIndex = document.getElementById("currentVideoIndex");
const streamTypeBadge = document.getElementById("streamTypeBadge");
const transcodeStatusBadge = document.getElementById("transcodeStatusBadge");
const toggleStreamModeBtn = document.getElementById("toggleStreamModeBtn");
const currentSourceDisplay = document.getElementById("currentSourceDisplay");
const destPathDisplay = document.getElementById("destPathDisplay");
const lanIpBadge = document.getElementById("lanIpBadge");
const queueCountBadge = document.getElementById("queueCountBadge");
const undoBtn = document.getElementById("undoBtn");
const shortcutsBtn = document.getElementById("shortcutsBtn");
const prevBtn = document.getElementById("prevBtn");
const skipBtn = document.getElementById("skipBtn");
const renameBtn = document.getElementById("renameBtn");
const renameHeaderBtn = document.getElementById("renameHeaderBtn");
const deleteBtn = document.getElementById("deleteBtn");
const favoriteBtn = document.getElementById("favoriteBtn");
const favoriteToolbarIcon = document.getElementById("favoriteToolbarIcon");
const favoriteToolbarText = document.getElementById("favoriteToolbarText");
const favoriteHeaderBtn = document.getElementById("favoriteHeaderBtn");
const favoriteHeaderIcon = document.getElementById("favoriteHeaderIcon");
const folderSearchInput = document.getElementById("folderSearchInput");
const clearSearchBtn = document.getElementById("clearSearchBtn");
const folderListBox = document.getElementById("folderListBox");
const folderMatchCount = document.getElementById("folderMatchCount");
const primaryMoveBtn = document.getElementById("primaryMoveBtn");
const primaryMoveBtnText = document.getElementById("primaryMoveBtnText");
const recentFoldersContainer = document.getElementById("recentFoldersContainer");
const newFolderInput = document.getElementById("newFolderInput");
const createAndMoveBtn = document.getElementById("createAndMoveBtn");
const refreshDestsBtn = document.getElementById("refreshDestsBtn");
const movingSpinnerOverlay = document.getElementById("movingSpinnerOverlay");
const movingStatusText = document.getElementById("movingStatusText");

// Background Tasks UI (Transfers & Pre-Transcoding)
const transferBadgeBtn = document.getElementById("transferBadgeBtn");
const transferIcon = document.getElementById("transferIcon");
const transferSpinner = document.getElementById("transferSpinner");
const transferDropdown = document.getElementById("transferDropdown");
const transferCountText = document.getElementById("transferCountText");
const transferDropdownSummary = document.getElementById("transferDropdownSummary");
const activeTransferBox = document.getElementById("activeTransferBox");
const activeTransferFilename = document.getElementById("activeTransferFilename");
const activeTransferPercent = document.getElementById("activeTransferPercent");
const activeTransferProgressBar = document.getElementById("activeTransferProgressBar");
const activeTransferTarget = document.getElementById("activeTransferTarget");
const activeTransferSize = document.getElementById("activeTransferSize");
const queuedTransfersList = document.getElementById("queuedTransfersList");
const transferHistoryList = document.getElementById("transferHistoryList");

// Pre-Transcoder UI
const transcodeSummaryText = document.getElementById("transcodeSummaryText");
const toggleTranscodePauseBtn = document.getElementById("toggleTranscodePauseBtn");
const activeTranscodeBox = document.getElementById("activeTranscodeBox");
const activeTranscodeMethodBadge = document.getElementById("activeTranscodeMethodBadge");
const activeTranscodeFilename = document.getElementById("activeTranscodeFilename");
const activeTranscodePercent = document.getElementById("activeTranscodePercent");
const activeTranscodeProgressBar = document.getElementById("activeTranscodeProgressBar");
const activeTranscodeSpeed = document.getElementById("activeTranscodeSpeed");
const activeTranscodeEta = document.getElementById("activeTranscodeEta");
const queuedTranscodesContainer = document.getElementById("queuedTranscodesContainer");
const queuedTranscodesList = document.getElementById("queuedTranscodesList");

// Thumbnail Generator UI
const thumbSummaryText = document.getElementById("thumbSummaryText");
const toggleThumbPauseBtn = document.getElementById("toggleThumbPauseBtn");
const thumbProgressBox = document.getElementById("thumbProgressBox");
const thumbCurrentFile = document.getElementById("thumbCurrentFile");
const thumbPercentText = document.getElementById("thumbPercentText");
const thumbProgressBar = document.getElementById("thumbProgressBar");
const thumbCountsText = document.getElementById("thumbCountsText");
const thumbStatusLabel = document.getElementById("thumbStatusLabel");

// Duplicate Hash Verifier UI
const hashSummaryText = document.getElementById("hashSummaryText");
const toggleHashPauseBtn = document.getElementById("toggleHashPauseBtn");
const hashProgressBox = document.getElementById("hashProgressBox");
const hashCurrentFile = document.getElementById("hashCurrentFile");
const hashPercentText = document.getElementById("hashPercentText");
const hashProgressBar = document.getElementById("hashProgressBar");
const hashCountsText = document.getElementById("hashCountsText");
const hashStatusLabel = document.getElementById("hashStatusLabel");

// Media Metadata Probe UI
const metaWorkerSummaryText = document.getElementById("metaWorkerSummaryText");
const metaWorkerCurrentFile = document.getElementById("metaWorkerCurrentFile");
const metaWorkerPercentText = document.getElementById("metaWorkerPercentText");
const metaWorkerProgressBar = document.getElementById("metaWorkerProgressBar");
const metaWorkerCountsText = document.getElementById("metaWorkerCountsText");
const metaWorkerStatusLabel = document.getElementById("metaWorkerStatusLabel");

// Visual Hash Indexer UI
const vhashSummaryText = document.getElementById("vhashSummaryText");
const vhashCurrentFile = document.getElementById("vhashCurrentFile");
const vhashPercentText = document.getElementById("vhashPercentText");
const vhashProgressBar = document.getElementById("vhashProgressBar");
const vhashCountsText = document.getElementById("vhashCountsText");
const vhashStatusLabel = document.getElementById("vhashStatusLabel");

// Face Recognition / People UI (opt-in; dormant until FACE_RECOGNITION_ENABLED)
const faceWorkerSummaryText = document.getElementById("faceWorkerSummaryText");
const faceWorkerCurrentFile = document.getElementById("faceWorkerCurrentFile");
const faceWorkerPercentText = document.getElementById("faceWorkerPercentText");
const faceWorkerProgressBar = document.getElementById("faceWorkerProgressBar");
const faceWorkerCountsText = document.getElementById("faceWorkerCountsText");
const faceWorkerStatusLabel = document.getElementById("faceWorkerStatusLabel");
const faceWorkerPeopleText = document.getElementById("faceWorkerPeopleText");
const faceWorkerCacheText = document.getElementById("faceWorkerCacheText");
const faceWorkerHintText = document.getElementById("faceWorkerHintText");

// WebP Optimizer UI
const webpSummaryText = document.getElementById("webpSummaryText");
const toggleWebpPauseBtn = document.getElementById("toggleWebpPauseBtn");
const webpProgressBox = document.getElementById("webpProgressBox");
const webpCurrentFile = document.getElementById("webpCurrentFile");
const webpPercentText = document.getElementById("webpPercentText");
const webpProgressBar = document.getElementById("webpProgressBar");
const webpCountsText = document.getElementById("webpCountsText");
const webpSavedText = document.getElementById("webpSavedText");

// Indexer UI
const indexSummaryText = document.getElementById("indexSummaryText");
const triggerIndexSyncDropdownBtn = document.getElementById("triggerIndexSyncDropdownBtn");
const indexSyncDropdownIcon = document.getElementById("indexSyncDropdownIcon");
const indexCurrentFolder = document.getElementById("indexCurrentFolder");
const indexPercentText = document.getElementById("indexPercentText");
const indexProgressBar = document.getElementById("indexProgressBar");
const indexCountsText = document.getElementById("indexCountsText");
const indexFilesText = document.getElementById("indexFilesText");
const indexStaleWarning = document.getElementById("indexStaleWarning");
const indexStaleText = document.getElementById("indexStaleText");
const indexForcePruneBtn = document.getElementById("indexForcePruneBtn");

// Delete Empty Folders UI
const cleanDbBtn = document.getElementById("cleanDbBtn");
const deleteEmptyFoldersBtn = document.getElementById("deleteEmptyFoldersBtn");
const deleteEmptyFoldersBtnText = document.getElementById("deleteEmptyFoldersBtnText");
const emptyFoldersModal = document.getElementById("emptyFoldersModal");
const closeEmptyFoldersModalBtn = document.getElementById("closeEmptyFoldersModalBtn");
const cancelEmptyFoldersModalBtn = document.getElementById("cancelEmptyFoldersModalBtn");
const confirmEmptyFoldersBtn = document.getElementById("confirmEmptyFoldersBtn");
const confirmEmptyFoldersBtnText = document.getElementById("confirmEmptyFoldersBtnText");
const emptyFoldersCandidateCount = document.getElementById("emptyFoldersCandidateCount");
const emptyFoldersLeafCount = document.getElementById("emptyFoldersLeafCount");
const emptyFoldersSelectAllToggle = document.getElementById("emptyFoldersSelectAllToggle");
const emptyFoldersSelectedBadge = document.getElementById("emptyFoldersSelectedBadge");
const emptyFoldersFilterInput = document.getElementById("emptyFoldersFilterInput");
const emptyFoldersCandidatesList = document.getElementById("emptyFoldersCandidatesList");

// Mobile Thumb Bar Buttons
const mobilePrevBtn = document.getElementById("mobilePrevBtn");
const mobileSkipBtn = document.getElementById("mobileSkipBtn");
const mobileUndoBtn = document.getElementById("mobileUndoBtn");
const mobileFavoriteBtn = document.getElementById("mobileFavoriteBtn");
const mobileFavoriteIcon = document.getElementById("mobileFavoriteIcon");
const mobileRenameBtn = document.getElementById("mobileRenameBtn");
const mobileDeleteBtn = document.getElementById("mobileDeleteBtn");

// Gesture Indicators
const seekLeftIndicator = document.getElementById("seekLeftIndicator");
const seekRightIndicator = document.getElementById("seekRightIndicator");

// Modals
const browserModal = document.getElementById("browserModal");
const openFolderBrowserBtn = document.getElementById("openFolderBrowserBtn");
const emptyStateBrowseBtn = document.getElementById("emptyStateBrowseBtn");
const closeBrowserModalBtn = document.getElementById("closeBrowserModalBtn");
const cancelBrowserBtn = document.getElementById("cancelBrowserBtn");
const browserPathInput = document.getElementById("browserPathInput");
const browserGoBtn = document.getElementById("browserGoBtn");
const browserUpBtn = document.getElementById("browserUpBtn");
const browserFolderList = document.getElementById("browserFolderList");
const browserVideoCountIndicator = document.getElementById("browserVideoCountIndicator");
const selectCurrentFolderBtn = document.getElementById("selectCurrentFolderBtn");

const deleteModal = document.getElementById("deleteModal");
const deleteFilenameDisplay = document.getElementById("deleteFilenameDisplay");
const cancelDeleteBtn = document.getElementById("cancelDeleteBtn");
const confirmDeleteBtn = document.getElementById("confirmDeleteBtn");

const renameModal = document.getElementById("renameModal");
const renameInput = document.getElementById("renameInput");
const cancelRenameBtn = document.getElementById("cancelRenameBtn");
const confirmRenameBtn = document.getElementById("confirmRenameBtn");

const shortcutsModal = document.getElementById("shortcutsModal");
const closeShortcutsBtn = document.getElementById("closeShortcutsBtn");
const toastContainer = document.getElementById("toastContainer");

export { activeTranscodeBox, activeTranscodeEta, activeTranscodeFilename, activeTranscodeMethodBadge, activeTranscodePercent, activeTranscodeProgressBar, activeTranscodeSpeed, activeTransferBox, activeTransferFilename, activeTransferPercent, activeTransferProgressBar, activeTransferSize, activeTransferTarget, autoTrashDuplicateToggle, browserFolderList, browserGoBtn, browserModal, browserPathInput, browserUpBtn, browserVideoCountIndicator, cancelBrowserBtn, cancelDeleteBtn, cancelEmptyFoldersModalBtn, cancelRenameBtn, cleanDbBtn, clearSearchBtn, closeBrowserModalBtn, closeEmptyFoldersModalBtn, closeShortcutsBtn, compareExistingName, compareExistingSize, compareExistingVideo, compareIncomingName, compareIncomingSize, confirmDeleteBtn, confirmEmptyFoldersBtn, confirmEmptyFoldersBtnText, confirmRenameBtn, createAndMoveBtn, currentSourceDisplay, currentVideoIndex, currentVideoSize, currentVideoTitle, deleteBtn, deleteEmptyFoldersBtn, deleteEmptyFoldersBtnText, deleteFilenameDisplay, deleteModal, destPathDisplay, dismissDuplicateBtn, duplicateBadgeType, duplicateBanner, duplicateCompareContainer, duplicateCompareText, duplicateMatchInfo, emptyFoldersCandidateCount, emptyFoldersCandidatesList, emptyFoldersFilterInput, emptyFoldersLeafCount, emptyFoldersModal, emptyFoldersSelectAllToggle, emptyFoldersSelectedBadge, emptyStateBrowseBtn, emptyVideoState, favoriteBtn, favoriteHeaderBtn, favoriteHeaderIcon, favoriteToolbarIcon, favoriteToolbarText, folderListBox, folderMatchCount, folderSearchInput, hashCountsText, hashCurrentFile, hashPercentText, hashProgressBar, hashProgressBox, hashStatusLabel, hashSummaryText, indexCountsText, indexCurrentFolder, indexFilesText, indexForcePruneBtn, indexPercentText, indexProgressBar, indexStaleText, indexStaleWarning, indexSummaryText, indexSyncDropdownIcon, lanIpBadge, metaWorkerCountsText, metaWorkerCurrentFile, metaWorkerPercentText, metaWorkerProgressBar, metaWorkerStatusLabel, metaWorkerSummaryText, mobileDeleteBtn, mobileFavoriteBtn, mobileFavoriteIcon, mobilePrevBtn, mobileRenameBtn, mobileSkipBtn, mobileUndoBtn, movingSpinnerOverlay, movingStatusText, newFolderInput, openFolderBrowserBtn, preloadVideoPlayer, prevBtn, primaryMoveBtn, primaryMoveBtnText, queueCountBadge, queuedTranscodesContainer, queuedTranscodesList, queuedTransfersList, recentFoldersContainer, refreshDestsBtn, renameBtn, renameHeaderBtn, renameInput, renameModal, seekLeftIndicator, seekRightIndicator, selectCurrentFolderBtn, shortcutsBtn, shortcutsModal, skipBtn, streamTypeBadge, thumbCountsText, thumbCurrentFile, thumbPercentText, thumbProgressBar, thumbProgressBox, thumbStatusLabel, thumbSummaryText, toastContainer, toggleDuplicateCompareBtn, toggleHashPauseBtn, toggleStreamModeBtn, toggleThumbPauseBtn, toggleTranscodePauseBtn, toggleWebpPauseBtn, transcodeStatusBadge, transcodeSummaryText, transferBadgeBtn, transferCountText, transferDropdown, transferDropdownSummary, transferHistoryList, transferIcon, transferSpinner, trashIncomingDuplicateBtn, triggerIndexSyncDropdownBtn, undoBtn, vhashCountsText, vhashCurrentFile, vhashPercentText, vhashProgressBar, vhashStatusLabel, vhashSummaryText, videoContainer, videoPlayer, webpCountsText, webpCurrentFile, webpPercentText, webpProgressBar, webpProgressBox, webpSavedText, webpSummaryText };
export { faceWorkerCacheText, faceWorkerCountsText, faceWorkerCurrentFile, faceWorkerHintText, faceWorkerPeopleText, faceWorkerPercentText, faceWorkerProgressBar, faceWorkerStatusLabel, faceWorkerSummaryText };
