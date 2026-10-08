/** Digest of the frontend sources, shared by the bundler and its verifier. */
const fs = require('fs'), path = require('path'), crypto = require('crypto');

function sourceDigest(base) {
  const files = [];
  const walk = dir => {
    for (const name of fs.readdirSync(dir).sort()) {
      const file = path.join(dir, name), stat = fs.statSync(file);
      if (stat.isDirectory()) walk(file);
      else if (/\.(?:ts|tsx|css)$/.test(name)) files.push(file);
    }
  };
  walk(path.join(base, 'src'));
  files.push(path.join(base, 'package-lock.json'));
  const hash = crypto.createHash('sha256');
  for (const file of files.sort()) {
    hash.update(path.relative(base, file).split(path.sep).join('/'));
    hash.update('\0');
    // Line endings depend on the checkout (CRLF on Windows, LF in CI); the digest must not.
    hash.update(fs.readFileSync(file).toString('latin1').replace(/\r\n/g, '\n'), 'latin1');
    hash.update('\0');
  }
  return hash.digest('hex');
}

module.exports = { sourceDigest };
