import { useEffect, useState } from 'react';

/**
 * Browser online/offline status hook — shared SSOT (FE-P8, 2026-06-13).
 *
 * Central writes (claim acquire/release, membership assign/revoke) are forbidden
 * while offline: the central ledger is unreachable, so a write would either fail
 * or — worse — give a false sense of a committed change. Both the coverage
 * dashboard and the membership roster gate their write controls on this hook, so
 * it lives here instead of being re-declared per route.
 */
export function useOnlineStatus(): boolean {
  const [online, setOnline] = useState(() =>
    typeof navigator === 'undefined' ? true : navigator.onLine,
  );
  useEffect(() => {
    const update = (): void => setOnline(navigator.onLine);
    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    return () => {
      window.removeEventListener('online', update);
      window.removeEventListener('offline', update);
    };
  }, []);
  return online;
}
