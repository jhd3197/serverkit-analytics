// Canonical copy-to-clipboard helper (local copy of the host's
// frontend/src/utils/clipboard.js — a runtime-ESM bundle cannot import host
// internals, and the logic is tiny and dependency-free).
//
// Wraps navigator.clipboard.writeText with a legacy execCommand fallback for
// insecure contexts / older browsers. Returns true on success, false on
// failure (never throws).

export async function copyToClipboard(text) {
    const value = String(text ?? '');

    // Preferred path: async Clipboard API (requires a secure context).
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(value);
            return true;
        } catch {
            // Fall through to the legacy path below.
        }
    }

    // Legacy fallback: a hidden textarea + execCommand('copy').
    if (typeof document === 'undefined') return false;
    try {
        const textarea = document.createElement('textarea');
        textarea.value = value;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'absolute';
        textarea.style.left = '-9999px';
        document.body.appendChild(textarea);
        textarea.select();
        const ok = document.execCommand('copy');
        document.body.removeChild(textarea);
        return ok;
    } catch {
        return false;
    }
}

export default copyToClipboard;
