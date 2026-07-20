// Shared date-range picker for the report tabs. Rendered as a small segmented
// button group styled by our own `.analytics-range` rules (isolated SCSS).
const RANGES = [
    { value: '1d', label: '24h' },
    { value: '7d', label: '7d' },
    { value: '14d', label: '14d' },
    { value: '30d', label: '30d' },
    { value: '90d', label: '90d' },
];

export default function RangePicker({ value, onChange }) {
    return (
        <div className="analytics-range" role="tablist" aria-label="Date range">
            {RANGES.map((r) => (
                <button
                    key={r.value}
                    type="button"
                    role="tab"
                    aria-selected={value === r.value}
                    className={`analytics-range__btn${value === r.value ? ' is-active' : ''}`}
                    onClick={() => onChange(r.value)}
                >
                    {r.label}
                </button>
            ))}
        </div>
    );
}
