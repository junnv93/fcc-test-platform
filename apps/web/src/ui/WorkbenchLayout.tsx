import type { ReactNode } from 'react';

export interface WorkbenchLayoutProps {
  /** Slot mode: render the labeled main region from a single React node. */
  readonly main?: ReactNode;
  readonly rail?: ReactNode;
  /** Compatibility composition for routes being migrated to explicit slots. */
  readonly children?: ReactNode;
  readonly mainLabel?: string;
  readonly railLabel?: string;
  readonly className?: string;
  readonly testId?: string;
  readonly hasSelection?: boolean;
  /** Override legacy composition's inferred rail when the rail is conditional. */
  readonly hasRail?: boolean;
}

/** Shared main/rail geometry for operator workbenches. */
export function WorkbenchLayout({
  main,
  rail,
  children,
  mainLabel,
  railLabel,
  className,
  testId = 'workbench-layout',
  hasSelection = false,
  hasRail: hasRailOverride,
}: WorkbenchLayoutProps): JSX.Element {
  const mainContent = main ?? children;
  const hasRail = hasRailOverride ?? rail !== undefined;
  const classes = ['workbench-layout', hasRail ? 'workbench-layout--has-rail' : '', className ?? '']
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes} data-has-selection={hasSelection || undefined} data-testid={testId}>
      <main className="workbench-layout__main" aria-label={mainLabel}>
        {mainContent}
      </main>
      {rail !== undefined && (
        <aside className="workbench-layout__rail" aria-label={railLabel}>
          {rail}
        </aside>
      )}
    </div>
  );
}

export default WorkbenchLayout;
