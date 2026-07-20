import { useCallback, useRef, useState } from 'react';
import { useToast } from 'serverkit-sdk';
import { copyToClipboard } from '../utils/clipboard.js';

// Copy-to-clipboard hook with toast feedback and a transient `copied` flag.
// Local copy of the host's frontend/src/hooks/useClipboard.js, with the toast
// context coming from the serverkit-sdk surface instead of a host deep import.
//
//   const { copy, copied } = useClipboard();
//   <button onClick={() => copy(apiKey)}>Copy</button>
export function useClipboard({
    successMessage = 'Copied to clipboard',
    errorMessage = 'Failed to copy',
    resetDelay = 2000,
} = {}) {
    const toast = useToast();
    const [copied, setCopied] = useState(false);
    const timer = useRef(null);

    const copy = useCallback(
        async (text, message) => {
            const ok = await copyToClipboard(text);
            if (ok) {
                setCopied(true);
                if (message !== null) toast.success(message ?? successMessage);
                if (timer.current) clearTimeout(timer.current);
                timer.current = setTimeout(() => setCopied(false), resetDelay);
            } else {
                toast.error(errorMessage);
            }
            return ok;
        },
        [toast, successMessage, errorMessage, resetDelay]
    );

    return { copy, copied };
}

export default useClipboard;
