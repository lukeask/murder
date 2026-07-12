import { useEffect } from 'react';
import { ensureKittyMurderMarkerCleanup, setKittyUserVar } from '../terminal/kittyUserVar.js';

/** Set kitty's `murder_tui=1` user var while the Ink app is mounted (for `--when-focus-on` mappings). */
export function useKittyMurderMarker(): void {
  useEffect(() => {
    ensureKittyMurderMarkerCleanup();
    setKittyUserVar('murder_tui', '1');

    return () => {
      setKittyUserVar('murder_tui', null);
    };
  }, []);
}
