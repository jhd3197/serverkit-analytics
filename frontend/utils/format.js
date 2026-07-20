// Small, dependency-free formatting helpers for the analytics dashboard.
// Kept isolated (no host imports) so the plugin bundle stays self-contained.

export const formatInt = (n) => Number(n ?? 0).toLocaleString();

export const formatMs = (ms) => (ms == null ? '—' : `${Math.round(ms)} ms`);

export const formatPct = (n) => `${Number(n ?? 0).toFixed(1)}%`;

// A 'YYYY-MM-DD' rollup date → a short "Jul 18" label. Parsed as local midnight
// (append time) so the label never drifts a day across time zones.
export const formatDay = (iso) => {
    if (!iso) return '';
    const d = new Date(`${iso}T00:00:00`);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
};

// An ISO timestamp → a wall-clock "14:03:21" label for the realtime feed.
export const formatClock = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
};

// Referrer/path dimension value → a readable label ("Direct / none" for empties).
export const labelOrDirect = (value) => (value && value.trim() ? value : 'Direct / none');
