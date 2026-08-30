import { describe, expect, it } from 'vitest';

import { validateOidcE2eReport } from './assert-oidc-e2e-report.mjs';

const REQUIRED_TESTS = {
  'auth-flow.spec.ts': [
    'real Keycloak login/callback protects preview routes across the evidence matrix',
    'real Keycloak logout clears the browser session',
  ],
  'oidc-conformance.spec.ts': [
    'discovery document conforms to OIDC Core 1.0 § 4',
    'jwks_uri returns at least one signing key',
  ],
};

function completeReport() {
  return {
    config: { workers: 1 },
    stats: { expected: 4, skipped: 0, unexpected: 0, flaky: 0 },
    errors: [],
    suites: Object.entries(REQUIRED_TESTS).map(([filename, titles]) => ({
      title: filename,
      file: `tests/e2e/${filename}`,
      specs: titles.map((title) => ({
        title,
        ok: true,
        tests: [{ status: 'expected', results: [{ status: 'passed' }] }],
      })),
    })),
  };
}

function authSuite(report) {
  return report.suites.find((suite) => suite.file.endsWith('auth-flow.spec.ts'));
}

function conformanceSuite(report) {
  return report.suites.find((suite) => suite.file.endsWith('oidc-conformance.spec.ts'));
}

const INVALID_REPORT_MUTATIONS = [
  [
    'missing auth-flow spec',
    (report) => {
      report.suites = [conformanceSuite(report)];
    },
  ],
  [
    'conformance-only execution',
    (report) => {
      report.suites = [conformanceSuite(report)];
      report.stats.expected = 2;
    },
  ],
  [
    'missing required protected-route behavior',
    (report) => {
      authSuite(report).specs = authSuite(report).specs.slice(1);
    },
  ],
  [
    'skipped test',
    (report) => {
      const test = authSuite(report).specs[0].tests[0];
      test.status = 'skipped';
      test.results = [{ status: 'skipped' }];
      report.stats.skipped = 1;
    },
  ],
  [
    'flaky test',
    (report) => {
      const test = authSuite(report).specs[0].tests[0];
      test.status = 'flaky';
      test.results = [{ status: 'failed' }, { status: 'passed' }];
      report.stats.flaky = 1;
    },
  ],
  [
    'unexpected test',
    (report) => {
      const test = authSuite(report).specs[0].tests[0];
      test.status = 'unexpected';
      test.results = [{ status: 'failed' }];
      report.stats.unexpected = 1;
    },
  ],
  ['malformed report', () => null],
  [
    'malformed nested suite node',
    (report) => {
      authSuite(report).suites = [null];
    },
  ],
  [
    'malformed nested spec node',
    (report) => {
      authSuite(report).specs = [null];
    },
  ],
  [
    'malformed nested test node',
    (report) => {
      authSuite(report).specs[0].tests = [null];
    },
  ],
  [
    'malformed nested result node',
    (report) => {
      authSuite(report).specs[0].tests[0].results = [null];
    },
  ],
  [
    'inconsistent aggregate stats',
    (report) => {
      report.stats.expected = 5;
    },
  ],
  [
    'empty report',
    (report) => {
      report.suites = [];
      report.stats.expected = 0;
    },
  ],
];

describe('assert-oidc-e2e-report', () => {
  it('accepts one complete report with both specs and all required tests', () => {
    const summary = validateOidcE2eReport(completeReport());
    expect(summary.expected).toBe(4);
    expect(summary.requiredTests).toBe(4);
    expect(summary.skipped).toBe(0);
    expect(summary.unexpected).toBe(0);
    expect(summary.flaky).toBe(0);
    expect(summary.specFiles).toEqual(
      expect.arrayContaining(['tests/e2e/auth-flow.spec.ts', 'tests/e2e/oidc-conformance.spec.ts']),
    );
  });

  for (const [label, mutate] of INVALID_REPORT_MUTATIONS) {
    it(`rejects mutation: ${label}`, () => {
      const report = completeReport();
      const mutationResult = mutate(report);
      const candidate = mutationResult === undefined ? report : mutationResult;
      expect(() => validateOidcE2eReport(candidate)).toThrow();
    });
  }
});
