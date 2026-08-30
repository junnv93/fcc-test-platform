import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const REQUIRED_OIDC_TESTS = Object.freeze({
  'auth-flow.spec.ts': Object.freeze([
    'real Keycloak login/callback protects preview routes across the evidence matrix',
    'real Keycloak logout clears the browser session',
  ]),
  'oidc-conformance.spec.ts': Object.freeze([
    'discovery document conforms to OIDC Core 1.0 § 4',
    'jwks_uri returns at least one signing key',
  ]),
});

const REQUIRED_STATS = Object.freeze(['expected', 'skipped', 'unexpected', 'flaky']);

function isRecord(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asNonNegativeInteger(value, label) {
  if (!Number.isInteger(value) || value < 0) {
    throw new Error(`OIDC report stats.${label} is missing or malformed`);
  }
  return value;
}

function normalizePath(value) {
  return value.replaceAll('\\', '/');
}

const TEST_OUTCOMES = new Set(['expected', 'skipped', 'unexpected', 'flaky']);
const RESULT_STATUSES = new Set(['passed', 'failed', 'timedOut', 'skipped', 'interrupted']);

function invalidNode(label, detail) {
  throw new Error(`OIDC report ${label} is malformed: ${detail}`);
}

function validateResult(result, label) {
  if (!isRecord(result)) invalidNode(label, 'result must be an object');
  if (!RESULT_STATUSES.has(result.status)) {
    invalidNode(label, 'result.status is missing or unknown');
  }
}

function validateTest(test, label) {
  if (!isRecord(test)) invalidNode(label, 'test must be an object');
  if (!TEST_OUTCOMES.has(test.status)) {
    invalidNode(label, 'test.status is missing or unknown');
  }
  if (!Array.isArray(test.results) || test.results.length !== 1) {
    invalidNode(label, 'test.results must contain exactly one result under --retries=0');
  }
  validateResult(test.results[0], label);
}

function collectSpecs(suites, inheritedFile = '') {
  if (!Array.isArray(suites)) invalidNode('suite list', 'suites must be an array');
  const records = [];
  for (const [suiteIndex, suite] of suites.entries()) {
    const suiteLabel = `suite[${suiteIndex}]`;
    if (!isRecord(suite)) invalidNode(suiteLabel, 'suite must be an object');
    if (suite.file !== undefined && (typeof suite.file !== 'string' || suite.file.length === 0)) {
      invalidNode(suiteLabel, 'suite.file must be a string when present');
    }
    if (suite.title !== undefined && typeof suite.title !== 'string') {
      invalidNode(suiteLabel, 'suite.title must be a string when present');
    }
    if (suite.specs !== undefined && !Array.isArray(suite.specs)) {
      invalidNode(suiteLabel, 'suite.specs must be an array when present');
    }
    if (suite.suites !== undefined && !Array.isArray(suite.suites)) {
      invalidNode(suiteLabel, 'suite.suites must be an array when present');
    }
    const file =
      typeof suite.file === 'string' && suite.file.length > 0
        ? normalizePath(suite.file)
        : inheritedFile;
    if (file.length === 0) invalidNode(suiteLabel, 'suite has no file path');
    const suiteSpecs = suite.specs ?? [];
    const childSuites = suite.suites ?? [];
    if (suiteSpecs.length === 0 && childSuites.length === 0) {
      invalidNode(suiteLabel, 'suite contains no specs or child suites');
    }
    for (const [specIndex, spec] of suiteSpecs.entries()) {
      const specLabel = `${suiteLabel}.spec[${specIndex}]`;
      if (!isRecord(spec)) invalidNode(specLabel, 'spec must be an object');
      if (typeof spec.title !== 'string' || spec.title.length === 0) {
        invalidNode(specLabel, 'spec.title must be a non-empty string');
      }
      if (spec.file !== undefined && (typeof spec.file !== 'string' || spec.file.length === 0)) {
        invalidNode(specLabel, 'spec.file must be a string when present');
      }
      if (!Array.isArray(spec.tests) || spec.tests.length === 0) {
        invalidNode(specLabel, 'spec.tests must be a non-empty array');
      }
      const specFile =
        typeof spec.file === 'string' && spec.file.length > 0 ? normalizePath(spec.file) : file;
      if (specFile.length === 0) invalidNode(specLabel, 'spec has no file path');
      for (const [testIndex, test] of spec.tests.entries()) {
        validateTest(test, `${specLabel}.test[${testIndex}]`);
      }
      records.push({
        file: specFile,
        title: spec.title,
        tests: spec.tests,
      });
    }
    records.push(...collectSpecs(childSuites, file));
  }
  return records;
}

function testPassed(test) {
  return test.status === 'expected' && test.results[0].status === 'passed';
}

function matchingSpec(specs, filename, title) {
  return specs.find(
    (spec) =>
      (spec.file === filename || spec.file.endsWith(`/${filename}`)) &&
      spec.title === title &&
      spec.tests.some(testPassed),
  );
}

/**
 * Validate the Playwright JSON report used by the Keycloak CI lane.
 *
 * This is intentionally stricter than Playwright's aggregate exit status:
 * both named spec files and each required behaviour must be present, and no
 * test may be skipped, retried/flaky, or unexpected. A conformance-only report
 * therefore cannot satisfy the real login/callback gate.
 */
export function validateOidcE2eReport(report) {
  if (!isRecord(report)) throw new Error('OIDC report is not a JSON object');
  const suites = report.suites;
  if (!Array.isArray(suites) || suites.length === 0) {
    throw new Error('OIDC report has no suites');
  }
  if (!Array.isArray(report.errors)) throw new Error('OIDC report errors array is missing');
  if (report.errors.length > 0) throw new Error('OIDC report contains top-level errors');

  const stats = report.stats;
  if (!isRecord(stats)) throw new Error('OIDC report stats object is missing');
  const normalizedStats = Object.fromEntries(
    REQUIRED_STATS.map((key) => [key, asNonNegativeInteger(stats[key], key)]),
  );
  if (normalizedStats.skipped !== 0) throw new Error('OIDC report contains skipped tests');
  if (normalizedStats.unexpected !== 0) {
    throw new Error('OIDC report contains unexpected tests');
  }
  if (normalizedStats.flaky !== 0) throw new Error('OIDC report contains flaky tests');

  const specs = collectSpecs(suites);
  if (specs.length === 0) throw new Error('OIDC report contains no executable specs');
  const allTests = specs.flatMap((spec) => spec.tests);
  if (allTests.length === 0) throw new Error('OIDC report contains no executable tests');
  const collectedStats = Object.fromEntries(REQUIRED_STATS.map((key) => [key, 0]));
  for (const test of allTests) collectedStats[test.status] += 1;
  for (const key of REQUIRED_STATS) {
    if (normalizedStats[key] !== collectedStats[key]) {
      throw new Error(
        `OIDC report stats.${key}=${normalizedStats[key]} disagrees with collected ${key} count ${collectedStats[key]}`,
      );
    }
  }
  if (normalizedStats.skipped !== 0) throw new Error('OIDC report contains skipped tests');
  if (normalizedStats.unexpected !== 0) {
    throw new Error('OIDC report contains unexpected tests');
  }
  if (normalizedStats.flaky !== 0) throw new Error('OIDC report contains flaky tests');
  if (normalizedStats.expected !== allTests.length) {
    throw new Error(
      `OIDC report stats.expected=${normalizedStats.expected} does not equal collected test count ${allTests.length} under --retries=0`,
    );
  }
  const nonPassing = allTests.filter((test) => !testPassed(test));
  if (nonPassing.length > 0) {
    throw new Error(`OIDC report contains ${nonPassing.length} non-passing test result(s)`);
  }

  const requiredCount = Object.values(REQUIRED_OIDC_TESTS).reduce(
    (count, titles) => count + titles.length,
    0,
  );
  if (normalizedStats.expected !== requiredCount) {
    throw new Error(
      `OIDC report expected exactly ${requiredCount} required tests but recorded ${normalizedStats.expected}`,
    );
  }

  const missing = [];
  for (const [filename, titles] of Object.entries(REQUIRED_OIDC_TESTS)) {
    const filePresent = specs.some(
      (spec) => spec.file === filename || spec.file.endsWith(`/${filename}`),
    );
    if (!filePresent) missing.push(`${filename} (spec file)`);
    for (const title of titles) {
      if (!matchingSpec(specs, filename, title)) missing.push(`${filename}: ${title}`);
    }
  }
  if (missing.length > 0) {
    throw new Error(`OIDC report is missing required coverage: ${missing.join('; ')}`);
  }

  return Object.freeze({
    expected: normalizedStats.expected,
    skipped: normalizedStats.skipped,
    unexpected: normalizedStats.unexpected,
    flaky: normalizedStats.flaky,
    specFiles: Object.freeze([
      ...new Set(specs.map((spec) => spec.file).filter((file) => file.length > 0)),
    ]),
    requiredTests: requiredCount,
  });
}

async function main() {
  const reportPath = process.argv[2];
  if (!reportPath) throw new Error('Usage: node scripts/assert-oidc-e2e-report.mjs <report.json>');
  let report;
  try {
    report = JSON.parse(await readFile(resolve(reportPath), 'utf8'));
  } catch (error) {
    throw new Error(
      `Unable to read or parse Playwright JSON report: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  const summary = validateOidcE2eReport(report);
  console.log(`OIDC E2E report valid: ${JSON.stringify(summary)}`);
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : null;
if (invokedPath === import.meta.url) {
  main().catch((error) => {
    console.error(`OIDC E2E report validation failed: ${error.message}`);
    process.exitCode = 1;
  });
}
