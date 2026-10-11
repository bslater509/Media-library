export function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    const colors = {
      success: 'bg-emerald-900/90 border-emerald-700 text-emerald-200',
      error: 'bg-red-900/90 border-red-700 text-red-200',
      info: 'bg-slate-900/90 border-slate-700 text-slate-200',
    };
    toast.className = `p-3 rounded-xl border text-xs font-medium backdrop-blur-md shadow-2xl flex items-center gap-2 pointer-events-auto transition-all duration-300 transform translate-y-2 opacity-0 ${colors[type] || colors.info}`;
    toast.innerHTML = `<span>${type === 'success' ? '✓' : type === 'error' ? '⚠️' : 'ℹ️'}</span><span class="truncate">${message}</span>`;
    els.toastContainer.appendChild(toast);

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
