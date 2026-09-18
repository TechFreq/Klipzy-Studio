const path = require('node:path');
const PROTOCOL = 'klipzy-settings-v1';
function compatibleBackend(identity, expectedPython, root, platform = process.platform) {
  const normalize = value => {
    const resolved = (platform === 'win32' ? path.win32 : path).resolve(value || '');
    return platform === 'win32' ? resolved.toLowerCase() : resolved;
  };
  // A bare system command does not identify an environment reliably.
  return Boolean(identity && identity.protocol === PROTOCOL && identity.python && identity.root
    && (platform === 'win32' ? path.win32 : path).isAbsolute(expectedPython)
    && normalize(identity.python) === normalize(expectedPython)
    && normalize(identity.root) === normalize(root));
}
module.exports = { compatibleBackend, PROTOCOL };
