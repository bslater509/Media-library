export function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    const colors = {
      success: 'bg-emerald-900/90 border-emerald-700 text-emerald-200',
      error: 'bg-red-900/90 border-red-700 text-red-200',
      info: 'bg-slate-900/90 border-slate-700 text-slate-200',
    };
    toast.className = `p-3 rounded-xl border text-xs font-medium backdrop-blur-md shadow-2xl flex items-center gap-2 pointer-events-auto transition-all duration-300 transform translate-y-2 opacity-0 ${colors[type] || colors.info}`;
    // Each toast is its own live region: errors announce assertively, everything
    // else politely (role=status carries an implicit aria-live=polite).
    toast.setAttribute('role', type === 'error' ? 'alert' : 'status');

    const iconSpan = document.createElement('span');
    iconSpan.setAttribute('aria-hidden', 'true');
    iconSpan.textContent = type === 'success' ? '✓' : type === 'error' ? '⚠️' : 'ℹ️';

    // Built with textContent, never innerHTML: toast messages carry
    // user-controlled names (filenames, folder names, download titles) that
    // must never be parsed as markup.
    const textSpan = document.createElement('span');
    textSpan.className = 'truncate';
    textSpan.textContent = message == null ? '' : String(message);

    toast.append(iconSpan, textSpan);
    const container = document.getElementById('toastContainer') || document.body;
    container.appendChild(toast);

    requestAnimationFrame(() => {
      toast.classList.remove('translate-y-2', 'opacity-0');
    });

    setTimeout(() => {
      toast.classList.add('translate-y-2', 'opacity-0');
      setTimeout(() => toast.remove(), 300);
    }, 3500);
  }

export function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

export function safeAttrEscape(str) {
    if (!str) return '';
    return encodeURIComponent(str).replace(/'/g, '%27');
  }


export function formatBytes(bytes, decimals = 2) {
    if (!+bytes) return '0 B';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}

export function formatDuration(seconds) {
    const s = Number(seconds);
    if (!s || s <= 0 || isNaN(s)) return '00:00';
    const total = Math.floor(s);
    const hrs = Math.floor(total / 3600);
    const mins = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (hrs > 0) {
        return `${hrs}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    }
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

export function getResolutionLabel(w, h) {
    const width = Number(w);
    const height = Number(h);
    if (!width || !height || width <= 0 || height <= 0) return '';
    const maxDim = Math.max(width, height);
    const minDim = Math.min(width, height);
    if (minDim >= 2160 || maxDim >= 3840) return '4K';
    if (minDim >= 1440 || maxDim >= 2560) return '2K';
    if (minDim >= 1080 || maxDim >= 1920) return '1080p';
    if (minDim >= 720 || maxDim >= 1280) return '720p';
    if (minDim >= 480 || maxDim >= 854) return '480p';
    return '';
}

export function getCodecLabel(codec) {
    const c = String(codec || '').trim().toLowerCase();
    if (!c) return '';
    if (c === 'h264' || c === 'avc' || c === 'avc1' || c === 'h.264') return 'H.264';
    if (c === 'hevc' || c === 'h265' || c === 'h.265') return 'H.265';
    if (c === 'mpeg4' || c === 'mp4v') return 'MPEG-4';
    if (c === 'mpeg2video') return 'MPEG-2';
    return c.toUpperCase();
}

export function getAspectRatio(w, h) {
    const width = Number(w);
    const height = Number(h);
    if (!width || !height || width <= 0 || height <= 0) return '';
    const ratio = width / height;
    if (Math.abs(ratio - 16 / 9) < 0.05) return '16:9';
    if (Math.abs(ratio - 9 / 16) < 0.05) return '9:16';
    if (Math.abs(ratio - 4 / 3) < 0.05) return '4:3';
    if (Math.abs(ratio - 3 / 4) < 0.05) return '3:4';
    if (Math.abs(ratio - 3 / 2) < 0.05) return '3:2';
    if (Math.abs(ratio - 2 / 3) < 0.05) return '2:3';
    if (Math.abs(ratio - 21 / 9) < 0.05) return '21:9';
    if (Math.abs(ratio - 1) < 0.03) return '1:1';
    const gcd = (a, b) => (b === 0 ? a : gcd(b, a % b));
    const divisor = gcd(Math.round(width), Math.round(height));
    const rw = Math.round(width) / divisor;
    const rh = Math.round(height) / divisor;
    if (rw > 30 || rh > 30) return `${ratio.toFixed(2)}:1`;
    return `${rw}:${rh}`;
}

// Decide whether a video tile should currently show its looping animated
// preview. Desktop pointers (hover-capable + fine) surface the animation only
// while the tile is hovered, independent of the global animated-previews
// toggle, so a hover always previews. Touch devices keep the scroll-into-view
// behaviour, which the toggle still gates to save battery and bandwidth.
export function shouldPlayAnimatedThumb({
    isVideo = false,
    hasHoverPointer = false,
    isHovered = false,
    isVisible = false,
    animatedThumbs = false,
} = {}) {
    if (!isVideo) return false;
    if (hasHoverPointer) return Boolean(isHovered);
    return Boolean(animatedThumbs && isVisible);
}

export function formatTimestamp(timestamp) {
    if (!timestamp) return 'Unknown';
    try {
        const ms = timestamp > 1e11 ? timestamp : timestamp * 1000;
        const d = new Date(ms);
        if (isNaN(d.getTime())) return 'Unknown';
        return d.toLocaleDateString(undefined, {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    } catch (e) {
        return 'Unknown';
    }
}

export function formatBitrate(bytes, durationSeconds) {
    const b = Number(bytes);
    const d = Number(durationSeconds);
    if (!b || !d || b <= 0 || d <= 0) return '';
    const bps = (b * 8) / d;
    if (bps >= 1000000) {
        return `${(bps / 1000000).toFixed(1)} Mbps`;
    }
    return `${Math.round(bps / 1000)} kbps`;
}
