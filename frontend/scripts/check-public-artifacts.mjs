/** Inspect only files shipped to browsers; never print matching secret material. */
import { readdir, readFile, stat } from 'node:fs/promises';
import path from 'node:path';

const root = process.cwd();
const roots = ['.next/static', 'public'];
const secretPatterns = [
  /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
  /\bAKIA[0-9A-Z]{16}\b/,
  /\bsk-(?:proj-|svcacct-)[A-Za-z0-9_-]{20,}/,
  /\b(?:postgres(?:ql)?|redis):\/\/[^\s"']+:[^\s"']+@/,
  /\b(?:DATABASE_URL|SMTP_PASSWORD|S3_SECRET_ACCESS_KEY|MFA_ENCRYPTION_KEY|AI_API_KEY)\b/,
];
const findings = [];
let checked = 0;

async function scan(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    const relative = path.relative(root, file);
    if (entry.isSymbolicLink()) {
      findings.push(`${relative}: symbolic link in public artifacts`);
    } else if (entry.isDirectory()) {
      await scan(file);
    } else {
      checked += 1;
      if (/\.map$|^\.env(?:\.|$)|\.(?:pem|key|db|sqlite3?)$/i.test(entry.name)) {
        findings.push(`${relative}: disallowed public file`);
      }
      if (/\.(?:js|mjs|css|html|json|txt|svg)$/i.test(entry.name)) {
        const content = await readFile(file, 'utf8');
        if (secretPatterns.some(pattern => pattern.test(content)) || /sourceMappingURL=data:/i.test(content)) {
          findings.push(`${relative}: server credential marker or inline source map`);
        }
      }
    }
  }
}

for (const relative of roots) {
  const directory = path.join(root, relative);
  try { await stat(directory); } catch (error) {
    if (error.code === 'ENOENT' && relative === 'public') continue;
    throw error;
  }
  await scan(directory);
}
if (findings.length) {
  console.error(findings.join('\n'));
  process.exitCode = 1;
} else {
  console.log(`Public artifact check passed (${checked} files).`);
}
