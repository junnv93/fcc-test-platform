import { Link } from 'react-router-dom';

import { useT } from '@/i18n';

export function NotFoundRoute(): JSX.Element {
  const { t } = useT();
  return (
    <section className="not-found" aria-labelledby="not-found-heading">
      <h1 id="not-found-heading">{t('routes.notFound.title')}</h1>
      <p>{t('routes.notFound.body')}</p>
      <Link to="/">{t('routes.notFound.backLink')}</Link>
    </section>
  );
}

export default NotFoundRoute;
