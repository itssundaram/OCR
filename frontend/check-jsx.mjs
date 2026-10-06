import { parse } from '@babel/parser';
import fs from 'fs';

const files = process.argv.slice(2);
let failed = 0;
for (const file of files) {
  const code = fs.readFileSync(file, 'utf8');
  try {
    parse(code, { sourceType: 'module', plugins: ['jsx'] });
  } catch (e) {
    failed++;
    console.log(`FAIL: ${file}`);
    console.log(`  ${e.message}`);
  }
}
if (failed === 0) {
  console.log(`OK: all ${files.length} files parsed cleanly`);
} else {
  console.log(`${failed} file(s) failed to parse`);
  process.exit(1);
}
