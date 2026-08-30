import { test } from '@playwright/test';

import { openLiveLane } from './helpers/live-stack-fixture';

// This lane handles generated credentials. It must never leave a trace,
// screenshot, video, or HTML report behind, including on a failed assertion.
test.use({ trace: 'off', screenshot: 'off', video: 'off' });

test('local auth performs first login, forced password change, and protected dashboard access', async ({
  page,
  context,
}) => {
  await openLiveLane('local-auth', page, context);
});
