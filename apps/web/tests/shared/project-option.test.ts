import { describe, expect, it } from 'vitest';

import { formatProjectOptionLabel, PROJECT_OPTION_SEPARATOR } from '@/shared/project-option';

/**
 * project-option label formatter unit test (project-picker-ssot, 2026-06-26).
 * The label is the operator-facing handle for a project — model name, suffixed
 * with the management number when assigned. A blank/absent management number must
 * collapse to the model name alone (no dangling separator).
 */
describe('formatProjectOptionLabel', () => {
  it('joins model name and management number with the shared separator', () => {
    expect(formatProjectOptionLabel({ model_name: 'SM-S921U', management_number: 'M-001' })).toBe(
      `SM-S921U${PROJECT_OPTION_SEPARATOR}M-001`,
    );
  });

  it('falls back to the model name when no management number is assigned', () => {
    expect(formatProjectOptionLabel({ model_name: 'SM-S921U', management_number: null })).toBe(
      'SM-S921U',
    );
    expect(formatProjectOptionLabel({ model_name: 'SM-S921U' })).toBe('SM-S921U');
  });

  it('trims surrounding whitespace and drops a blank management number', () => {
    expect(formatProjectOptionLabel({ model_name: '  SM-S921U  ', management_number: '   ' })).toBe(
      'SM-S921U',
    );
  });
});
