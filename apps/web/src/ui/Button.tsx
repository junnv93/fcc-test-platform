import { forwardRef } from 'react';

import { useT } from '@/i18n';

import type { ButtonHTMLAttributes, ReactNode } from 'react';

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';
export type ButtonSize = 'sm' | 'md' | 'lg';

type ButtonBaseProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  'aria-busy' | 'aria-label' | 'aria-labelledby' | 'children'
> & {
  readonly variant?: ButtonVariant;
  readonly size?: ButtonSize;
  readonly leading?: ReactNode;
  readonly trailing?: ReactNode;
  readonly loading?: boolean;
  readonly loadingLabel?: string;
};

type IconOnlyAccessibleName =
  | { readonly 'aria-label': string; readonly 'aria-labelledby'?: never }
  | { readonly 'aria-label'?: never; readonly 'aria-labelledby': string };

export type ButtonProps =
  | (ButtonBaseProps & {
      readonly iconOnly?: false | undefined;
      readonly children: ReactNode;
      readonly 'aria-label'?: string | undefined;
      readonly 'aria-labelledby'?: string | undefined;
    })
  | (ButtonBaseProps &
      IconOnlyAccessibleName & {
        readonly iconOnly: true;
        readonly children: ReactNode;
      });

function hasAccessibleName(
  ariaLabel: string | undefined,
  ariaLabelledBy: string | undefined,
): boolean {
  return Boolean(
    (ariaLabel !== undefined && ariaLabel.trim() !== '') ||
      (ariaLabelledBy !== undefined && ariaLabelledBy.trim() !== ''),
  );
}

function ButtonLoadingStatus({ label }: { readonly label?: string | undefined }): JSX.Element {
  const { t } = useT();
  return (
    <span className="button__loading-status" role="status" aria-live="polite">
      <span className="button__spinner" aria-hidden="true" />
      {label ?? t('common.loading')}
    </span>
  );
}

/**
 * Button is the only owner of production button styling and behavior.
 *
 * Native button attributes intentionally remain on the element: callers keep
 * form association, `type`, handlers, test ids and permission guards while
 * the primitive supplies one visual/action hierarchy. Icon-only controls must
 * provide an accessible name, and loading preserves the original label while
 * exposing a polite progress status and blocking repeated activation.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    leading,
    trailing,
    loading = false,
    loadingLabel,
    iconOnly = false,
    children,
    className,
    disabled,
    'aria-label': ariaLabel,
    'aria-labelledby': ariaLabelledBy,
    ...buttonProps
  },
  ref,
): JSX.Element {
  if (iconOnly && !hasAccessibleName(ariaLabel, ariaLabelledBy)) {
    throw new Error('Button iconOnly requires aria-label or aria-labelledby');
  }

  const classes = [
    'button',
    `button--${variant}`,
    `button--${size}`,
    iconOnly ? 'button--icon-only' : '',
    loading ? 'button--loading' : '',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <button
      {...buttonProps}
      ref={ref}
      className={classes}
      disabled={loading ? true : disabled}
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
      aria-busy={loading || undefined}
    >
      {leading !== undefined && (
        <span className="button__leading" aria-hidden={iconOnly}>
          {leading}
        </span>
      )}
      <span className="button__label">{children}</span>
      {trailing !== undefined && (
        <span className="button__trailing" aria-hidden={iconOnly}>
          {trailing}
        </span>
      )}
      {loading && <ButtonLoadingStatus label={loadingLabel} />}
    </button>
  );
});

Button.displayName = 'Button';

export default Button;
