// Runs in the page MAIN world at document_start so the opaque device blob is
// observed before creator passport requests encrypt it into x-tt-session-dtrait.
(() => {
  if (window.__dyauthreplyDtraitHookInstalled) return;
  const originalStringify = JSON.stringify;
  const publish = (value, path) => {
    window.__dyauthreplyLatestDtraitBlob = String(value || '');
    window.__dyauthreplyLatestDtraitPath = String(path || '');
    const root = document.documentElement;
    if (root) {
      root.setAttribute('data-dyauthreply-dtrait-blob', window.__dyauthreplyLatestDtraitBlob);
      root.setAttribute('data-dyauthreply-dtrait-path', window.__dyauthreplyLatestDtraitPath);
    }
  };
  const wrappedStringify = function wrappedStringify(value, replacer, space) {
    try {
      if (
        value
        && typeof value === 'object'
        && typeof value.dtrait === 'string'
        && value.dtrait.length > 20
        && typeof value.path === 'string'
      ) {
        publish(value.dtrait, value.path);
      }
    } catch {
      // The hook must remain transparent to the creator application.
    }
    return Reflect.apply(originalStringify, this, arguments);
  };
  try {
    Object.defineProperty(wrappedStringify, 'toString', {
      value: () => originalStringify.toString(),
    });
  } catch {
    // Cosmetic function masking is optional.
  }
  JSON.stringify = wrappedStringify;
  window.__dyauthreplyDtraitHookInstalled = true;
})();
