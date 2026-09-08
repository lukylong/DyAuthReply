import { test } from 'node:test';
import assert from 'node:assert/strict';
import { hasUnavailableLease } from './accountLease.ts';
test('lease denial takes precedence without altering platform credentials', () => {
  for (const ownership of ['lost', 'expired']) {
    const account = { credential_state: 'sendable', runtime_state: { ownership } };
    assert.equal(hasUnavailableLease(account), true);
    assert.equal(account.credential_state, 'sendable');
  }
});
test('owned and legacy accounts are not mislabeled', () => {
  assert.equal(hasUnavailableLease({ runtime_state: { ownership: 'owned' } }), false);
  assert.equal(hasUnavailableLease({}), false);
});
