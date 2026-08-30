import { useEffect, useState } from 'react';

import { useT } from '@/i18n';
import { applyDensity, type Density, readStoredDensity, storeDensity } from '@/shared/density';

import { Button } from './Button';

export function DensityToggle(): JSX.Element {
  const { t } = useT();
  const [density, setDensity] = useState<Density>(() => readStoredDensity());

  // Re-assert the attribute/storage from React state. The pre-bundle boot
  // script (public/density-boot.js) already applied it for the first paint;
  // this keeps the DOM in step after hydration and on subsequent toggles.
  useEffect(() => {
    applyDensity(density);
    storeDensity(density);
  }, [density]);

  function chooseDensity(nextDensity: Density): void {
    applyDensity(nextDensity);
    storeDensity(nextDensity);
    setDensity(nextDensity);
  }

  return (
    <div className="density-toggle" role="group" aria-label={t('ui.densityToggle.ariaLabel')}>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        // Glove rung (§M7.3): density is adjusted mid-session at the bench.
        className="density-toggle__option touch-target touch-target--glove"
        aria-pressed={density === 'comfortable'}
        onClick={() => chooseDensity('comfortable')}
      >
        {t('ui.densityToggle.comfortable')}
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        // Glove rung (§M7.3): density is adjusted mid-session at the bench.
        className="density-toggle__option touch-target touch-target--glove"
        aria-pressed={density === 'compact'}
        onClick={() => chooseDensity('compact')}
      >
        {t('ui.densityToggle.compact')}
      </Button>
    </div>
  );
}
