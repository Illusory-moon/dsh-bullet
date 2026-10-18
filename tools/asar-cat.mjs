// usage: node asar-cat.mjs <asar> <entryPath> [maxBytes]
// 按路径取出 asar 内的单个文件内容（用于读宿主源码）。
import { openSync, readSync } from 'node:fs';

process.noAsar = true;

const [asarPath, entryPath, maxArg] = process.argv.slice(2);
const maxBytes = Number(maxArg || 400000);

const fd = openSync(asarPath, 'r');
const head = Buffer.alloc(16);
readSync(fd, head, 0, 16, 0);
const headerSize = head.readUInt32LE(4);
const stringSize = head.readUInt32LE(12);
const headerBuf = Buffer.alloc(stringSize);
readSync(fd, headerBuf, 0, stringSize, 16);
const header = JSON.parse(headerBuf.toString('utf8').replace(/\uFFFD/g, '?'));
const dataStart = 8 + headerSize;

let node = header;
for (const part of entryPath.split('/').filter(Boolean)) {
  if (!node.files || !node.files[part]) {
    console.error(`not found: ${part} (under ${entryPath})`);
    process.exit(1);
  }
  node = node.files[part];
}
if (node.files) {
  console.error('that entry is a directory');
  process.exit(1);
}

const size = Math.min(Number(node.size), maxBytes);
const buf = Buffer.alloc(size);
readSync(fd, buf, 0, size, dataStart + Number(node.offset));
process.stdout.write(buf);
if (Number(node.size) > size) console.error(`\n-- truncated at ${size}/${node.size} bytes`);
