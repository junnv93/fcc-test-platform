import { createElement } from 'react';

import type { HTMLAttributes, ReactNode } from 'react';

type CardElement = 'article' | 'section' | 'div';
type CardVariant = 'surface' | 'summary' | 'action' | 'data';

export type CardProps = Omit<HTMLAttributes<HTMLElement>, 'title' | 'children'> & {
  readonly as?: CardElement;
  readonly variant?: CardVariant;
  readonly title?: ReactNode;
  readonly titleId?: string;
  readonly actions?: ReactNode;
  readonly children?: ReactNode;
  readonly testId?: string;
};

/** A surface primitive for summaries, actions and data groups. */
export function Card({
  as = 'section',
  variant = 'surface',
  title,
  titleId,
  actions,
  testId,
  className,
  children,
  ...props
}: CardProps): JSX.Element {
  const classes = ['card', `card--${variant}`, className ?? ''].filter(Boolean).join(' ');
  const labelledBy =
    title !== undefined && titleId !== undefined ? titleId : props['aria-labelledby'];

  return createElement(
    as,
    {
      ...props,
      className: classes,
      ...(labelledBy !== undefined ? { 'aria-labelledby': labelledBy } : {}),
      'data-testid': testId ?? 'card',
    },
    title !== undefined || actions !== undefined
      ? createElement(
          'header',
          { className: 'card__header' },
          title !== undefined
            ? createElement('h2', { className: 'card__title', id: titleId }, title)
            : null,
          actions !== undefined
            ? createElement('div', { className: 'card__actions' }, actions)
            : null,
        )
      : null,
    createElement('div', { className: 'card__body' }, children),
  );
}

export default Card;
