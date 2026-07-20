// Local stand-ins for the host components this dashboard needs that are NOT on
// the serverkit-sdk surface (EmptyState, Button, Modal, ConfirmDialog, Input,
// Label). They render the same host design-system classes (.btn-*,
// .empty-state, .skeleton, .ui-dialog-*, .sk-modal*, .sk-confirm*, .ui-input,
// .ui-label) the core components emit, so the page looks identical without
// importing host internals — which a runtime-ESM bundle cannot do.
import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Inbox, X, AlertTriangle, AlertCircle, Info } from 'lucide-react';

// Mirrors frontend/src/components/ui/button.jsx's variant/size -> class map.
const VARIANT_CLASSES = {
    default: 'btn-primary',
    primary: 'btn-primary',
    destructive: 'btn-danger',
    danger: 'btn-danger',
    outline: 'btn-secondary',
    secondary: 'btn-soft',
    ghost: 'btn-ghost',
    link: 'btn-link',
};
const SIZE_CLASSES = { sm: 'btn-sm', icon: 'btn-icon' };

export function Button({ variant = 'default', size, className = '', children, ...props }) {
    const classes = [
        'btn',
        VARIANT_CLASSES[variant] || 'btn-primary',
        SIZE_CLASSES[size] || '',
        className,
    ].filter(Boolean).join(' ');
    return (
        <button type="button" className={classes} {...props}>
            {children}
        </button>
    );
}

// Mirrors frontend/src/components/ui/input.jsx (class .ui-input).
export function Input({ className = '', type, ...props }) {
    return (
        <input
            type={type}
            data-slot="input"
            className={['ui-input', className].filter(Boolean).join(' ')}
            {...props}
        />
    );
}

// Mirrors frontend/src/components/ui/label.jsx (Radix Root renders a <label>).
export function Label({ className = '', children, ...props }) {
    return (
        <label
            data-slot="label"
            className={['ui-label', className].filter(Boolean).join(' ')}
            {...props}
        >
            {children}
        </label>
    );
}

// Mirrors the host Skeleton's rendered markup (span.skeleton.skeleton--*).
function Skeleton({ variant = 'line', width }) {
    return (
        <span
            className={`skeleton skeleton--${variant}`}
            style={width != null ? { width: typeof width === 'number' ? `${width}px` : width } : undefined}
            aria-hidden="true"
        />
    );
}

// Mirrors frontend/src/components/EmptyState.jsx's rendered markup so the host
// .empty-state / .skeleton-panel styles apply unchanged.
export function EmptyState({
    icon: Icon = Inbox,
    title = 'No items found',
    description = '',
    action = null,
    size = 'default',
    loading = false,
}) {
    if (loading) {
        return (
            <div
                className={`empty-state empty-state--${size} empty-state--loading`}
                role="status"
                aria-busy="true"
                aria-label={title || 'Loading'}
            >
                <div className="skeleton-panel">
                    <div className="skeleton-panel__head">
                        <Skeleton variant="avatar" />
                        <div className="skeleton-panel__head-text">
                            <Skeleton variant="title" width="42%" />
                            <Skeleton variant="line" width="26%" />
                        </div>
                    </div>
                    <div className="skeleton-panel__cards">
                        <Skeleton variant="card" />
                        <Skeleton variant="card" />
                        <Skeleton variant="card" />
                    </div>
                    <div className="skeleton-panel__rows">
                        <Skeleton variant="line" width="100%" />
                        <Skeleton variant="line" width="92%" />
                        <Skeleton variant="line" width="76%" />
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className={`empty-state empty-state--${size}`}>
            <div className="empty-state__icon">
                <Icon size={size === 'lg' ? 64 : 48} />
            </div>
            <h3 className="empty-state__title">{title}</h3>
            {description && (
                <p className="empty-state__description">{description}</p>
            )}
            {action && (
                <div className="empty-state__action">{action}</div>
            )}
        </div>
    );
}

// Shared portal scaffolding. The host dialogs (frontend/src/components/ui/
// dialog.jsx + alert-dialog.jsx) render via Radix into <body> with the overlay
// (.ui-dialog-overlay) + content (.ui-dialog-content) pair; these stand-ins
// reproduce that markup plus Escape / overlay-click dismissal. (Radix's focus
// trapping is the only host-only behavior not mirrored.)
function DialogShell({ onClose, contentClassName, label, children }) {
    useEffect(() => {
        const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [onClose]);

    return createPortal(
        <>
            <div className="ui-dialog-overlay" onClick={onClose} />
            <div
                className={contentClassName}
                role="dialog"
                aria-modal="true"
                aria-label={label}
            >
                {children}
            </div>
        </>,
        document.body,
    );
}

// Mirrors frontend/src/components/Modal.jsx's rendered markup (Radix Dialog →
// .ui-dialog-content.sk-modal.sk-modal--{size}, header/body/footer slots and
// the .ui-dialog-close button).
export function Modal({
    open,
    onClose,
    title,
    children,
    footer,
    className = '',
    size = 'md',
}) {
    if (!open) return null;
    return (
        <DialogShell
            onClose={onClose}
            label={title || 'Dialog'}
            contentClassName={['ui-dialog-content', 'sk-modal', `sk-modal--${size}`, className].filter(Boolean).join(' ')}
        >
            {title && (
                <div className="sk-modal__header">
                    <h2 className="ui-dialog-title">{title}</h2>
                </div>
            )}

            <div className="sk-modal__body">{children}</div>

            {footer && <div className="sk-modal__footer">{footer}</div>}

            <button type="button" className="ui-dialog-close" onClick={onClose}>
                <X />
                <span className="sr-only">Close</span>
            </button>
        </DialogShell>
    );
}

const CONFIRM_ICONS = { danger: AlertTriangle, warning: AlertCircle, info: Info };

// Mirrors frontend/src/components/ConfirmDialog.jsx's rendered markup (Radix
// AlertDialog → .ui-dialog-content.sk-confirm, .sk-confirm__head/__icon/
// __body/__footer). Only the props serverkit-analytics uses are supported
// (no requireConfirmation typing gate).
export function ConfirmDialog({
    isOpen,
    title,
    message,
    details,
    confirmText = 'Confirm',
    cancelText = 'Cancel',
    variant = 'danger',
    onConfirm,
    onCancel,
}) {
    if (!isOpen) return null;
    const Icon = CONFIRM_ICONS[variant] || AlertTriangle;
    return (
        <DialogShell
            onClose={onCancel}
            label={title || 'Confirm'}
            contentClassName="ui-dialog-content sk-confirm"
        >
            <div className="ui-dialog-header">
                <div className="sk-confirm__head">
                    <div className={`sk-confirm__icon sk-confirm__icon--${variant}`}>
                        <Icon size={24} />
                    </div>
                    <div className="sk-confirm__body">
                        <h2 className="ui-dialog-title">{title}</h2>
                        {message && <p className="ui-dialog-description">{message}</p>}
                        {details && <p className="sk-confirm__details">{details}</p>}
                    </div>
                </div>
            </div>
            <div className="ui-dialog-footer sk-confirm__footer">
                <Button variant="outline" onClick={onCancel}>{cancelText}</Button>
                <Button
                    variant={variant === 'danger' ? 'destructive' : 'primary'}
                    onClick={onConfirm}
                >
                    {confirmText}
                </Button>
            </div>
        </DialogShell>
    );
}
