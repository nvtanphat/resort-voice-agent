/** Fails when web/guest.js or web/staff.js was not built from the current frontend sources. */
const fs = require('fs'), path = require('path');
const { sourceDigest } = require('./source_digest.cjs');

const expected = sourceDigest(__dirname);
let failed = false;
for (const name of ['guest.js', 'staff.js']) {
  const file = path.join(__dirname, '..', 'web', name);
  const head = fs.readFileSync(file, 'utf8').slice(0, 200);
  const match = /source-sha256:([0-9a-f]{64})/.exec(head);
  if (!match || match[1] !== expected) {
    console.error(`${name}: bundle digest ${match ? match[1] : 'missing'} != sources ${expected}; run "npm run build" in frontend/`);
    failed = true;
  }
}
if (failed) process.exit(1);
console.log(`bundle digests match sources (${expected.slice(0, 12)})`);
