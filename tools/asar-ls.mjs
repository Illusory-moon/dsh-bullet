// usage: node asar-ls.mjs <asar> <regex> [limit]
// 列出 asar 内匹配的条目路径（不含内容），用于定位宿主源码文件。
import { openSync, readSync } from 'node:fs';

// Electron 的 fs 被 asar 打了补丁：不关掉会把它当成 asar 内部路径去解析。
process.noAsar = true;

const [asarPath, pattern = '.', limitArg] = process.argv.slice(2);
const limit = Number(limitArg || 200);
const re = new RegExp(pattern, 'i');

const fd = openSync(asarPath, 'r');
const head = Buffer.alloc(16);
readSync(fd, head, 0, 16, 0);
const headerSize = head.readUInt32LE(4);
const stringSize = head.readUInt32LE(12);
const headerBuf = Buffer.alloc(stringSize);
readSync(fd, headerBuf, 0, stringSize, 16);
const header = JSON.parse(headerBuf.toString('utf8').replace(/\uFFFD/g, '?'));

const out = [];
function walk(node, prefix) {
  if (out.length >= limit) return;
  for (const [name, child] of Object.entries(node.files || {})) {
    const p = prefix ? `${prefix}/${name}` : name;
    if (child.files) walk(child, p);
    else if (re.test(p)) out.push(`${String(child.size).padStart(9)}  ${p}`);
  }
}
walk(header, '');
console.log(out.join('\n'));
console.log(`-- ${out.length} entries (limit ${limit})`);
