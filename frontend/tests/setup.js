import { beforeEach } from 'vitest';

beforeEach(() => {
  // Reset window and document state before each test
  if (typeof document !== 'undefined') {
    document.body.innerHTML = '';
  }
  if (typeof localStorage !== 'undefined') {
    localStorage.clear();
  }
});
