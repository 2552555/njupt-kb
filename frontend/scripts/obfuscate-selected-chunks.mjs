#!/usr/bin/env node
/**
 * 选择性 JavaScript 混淆（补充规范 C03，可选增强）。
 *
 * 只处理 Next.js 产物中**自有业务模块**的 chunk，默认不碰：
 * - React/Next 运行时与第三方包（framework/main/polyfills/webpack/vendor 命名）；
 * - 认证、支付、预览相关页面（按文件名排除，避免影响敏感链路与调试）。
 *
 * 保守策略（文档 §4.2 的约束）：
 * - 只做标识符重命名（mangled），关闭属性重命名、控制流平坦化、字符串数组、
 *   死代码注入、self-defending、debug protection、domain lock；
 * - 不使用 eval/new Function 类变换，不为混淆放宽 CSP；
 * - 处理前备份，任一文件失败或语法校验失败即整体回滚；
 * - 记录工具版本、配置、种子与体积变化，便于复现与回退。
 *
 * 用法：
 *   node scripts/obfuscate-selected-chunks.mjs            # 处理 .next/static/chunks
 *   node scripts/obfuscate-selected-chunks.mjs --dry-run  # 只打印将处理的文件
 */

import { createHash } from 'node:crypto';
import { cp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import JavaScriptObfuscator from 'javascript-obfuscator';

const ROOT = process.cwd();
const CHUNK_DIR = path.join(ROOT, '.next', 'static', 'chunks');
const BACKUP_DIR = path.join(ROOT, '.next', 'obfuscation-backup');
const REPORT_PATH = path.join(ROOT, '.next', 'obfuscation-report.json');
const DRY_RUN = process.argv.includes('--dry-run');

// 与文档 §4.2 一致：只重命名标识符，不引入 eval、不做重度变换。
const OPTIONS = {
  compact: true,
  identifierNamesGenerator: 'mangled',
  renameGlobals: false,
  renameProperties: false,
  stringArray: false,
  stringArrayThreshold: 0,
  controlFlowFlattening: false,
  deadCodeInjection: false,
  selfDefending: false,
  debugProtection: false,
  disableConsoleOutput: false,
  simplify: false,
  transformObjectKeys: false,
  numbersToExpressions: false,
  splitStrings: false,
  unicodeEscapeSequence: false,
  // 固定种子：同一构建可复现，便于定位与回退。
  seed: 20261018,
};

// 不混淆的 chunk：运行时、第三方、以及认证/支付/预览等敏感链路。
const SKIP_PATTERNS = [
  /^framework-/,
  /^main-/,
  /^polyfills-/,
  /^webpack-/,
  /^vendor-/,
  /^node_modules/,
  /\/(auth|login|register|reset-password|verify-email)\//,
  /\/(payments?|settlements?|payouts?)\//,
  /\/(preview|upload)\//,
];

// 只处理能确认是自有业务代码的 chunk（Turbopack/Webpack 会在文件名里保留源码路径）。
const INCLUDE_PATTERNS = [/\/(app|components|lib|stores)\//];

function shouldProcess(relative) {
  const normalized = relative.split(path.sep).join('/');
  if (SKIP_PATTERNS.some((re) => re.test(normalized))) return false;
  return INCLUDE_PATTERNS.some((re) => re.test(normalized));
}

async function walk(dir, base = dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await walk(full, base)));
    } else if (entry.name.endsWith('.js')) {
      files.push(path.relative(base, full));
    }
  }
  return files;
}

function sha256(text) {
  return createHash('sha256').update(text).digest('hex');
}

function syntaxOk(file) {
  try {
    execFileSync(process.execPath, ['--check', file], { stdio: 'pipe' });
    return true;
  } catch {
    return false;
  }
}

async function main() {
  if (!existsSync(CHUNK_DIR)) {
    console.error(`未找到构建产物：${CHUNK_DIR}。请先执行 next build。`);
    process.exit(1);
  }

  const all = await walk(CHUNK_DIR);
  const targets = all.filter(shouldProcess).sort();

  console.log(`chunk 总数 ${all.length}，待处理 ${targets.length}`);
  if (DRY_RUN) {
    for (const file of targets) console.log(`  ${file}`);
    return;
  }
  if (targets.length === 0) {
    console.warn('没有匹配到自有业务 chunk：本次不做任何修改。');
    return;
  }

  await rm(BACKUP_DIR, { recursive: true, force: true });
  await mkdir(BACKUP_DIR, { recursive: true });

  const entries = [];
  const originals = new Map();
  let totalBefore = 0;
  let totalAfter = 0;

  try {
    for (const relative of targets) {
      const source = path.join(CHUNK_DIR, relative);
      const code = await readFile(source, 'utf8');
      originals.set(relative, code);
      totalBefore += Buffer.byteLength(code);

      const backup = path.join(BACKUP_DIR, relative);
      await mkdir(path.dirname(backup), { recursive: true });
      await cp(source, backup);

      const result = JavaScriptObfuscator.obfuscate(code, OPTIONS).getObfuscatedCode();
      await writeFile(source, result, 'utf8');
      totalAfter += Buffer.byteLength(result);

      if (!syntaxOk(source)) {
        throw new Error(`混淆后语法校验失败：${relative}`);
      }
      entries.push({
        file: relative,
        bytes_before: Buffer.byteLength(code),
        bytes_after: Buffer.byteLength(result),
        sha256_before: sha256(code),
        sha256_after: sha256(result),
      });
    }
  } catch (error) {
    console.error(`混淆失败，正在回滚：${error.message}`);
    for (const [relative, code] of originals) {
      await writeFile(path.join(CHUNK_DIR, relative), code, 'utf8');
    }
    process.exit(1);
  }

  const growth = totalBefore === 0 ? 0 : (totalAfter - totalBefore) / totalBefore;
  const report = {
    generated_at: new Date().toISOString(),
    tool: 'javascript-obfuscator',
    tool_version: '4.1.1',
    options: OPTIONS,
    files: entries,
    bytes_before: totalBefore,
    bytes_after: totalAfter,
    growth_ratio: Number(growth.toFixed(4)),
    // 文档建议以 10% 体积增幅为讨论阈值；超过只警告，不自动判定失败。
    growth_threshold_exceeded: growth > 0.1,
    backup_dir: path.relative(ROOT, BACKUP_DIR),
  };
  await writeFile(REPORT_PATH, JSON.stringify(report, null, 2), 'utf8');

  console.log(
    `已混淆 ${entries.length} 个 chunk，体积 ${totalBefore} -> ${totalAfter}（${(growth * 100).toFixed(1)}%）`,
  );
  if (growth > 0.1) {
    console.warn('体积增幅超过 10%：按文档建议复核收益，必要时关闭本次混淆。');
  }
  console.log(`报告：${path.relative(ROOT, REPORT_PATH)}；备份：${report.backup_dir}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
