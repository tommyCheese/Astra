import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const sourceFiles = [
  'src/App.tsx',
  'src/UsageDashboard.tsx',
  'src/SharedConversationPage.tsx',
];
const source = sourceFiles.map((file) => readFileSync(resolve(file), 'utf8')).join('\n');
const dictionary = readFileSync(resolve('src/i18n.tsx'), 'utf8');
const translations = new Set([...dictionary.matchAll(/(?:^|[,\n]\s*)'((?:\\'|[^'])*)'\s*:/g)].map((match) => match[1].replace(/\\'/g, "'")));
const usedKeys = [...source.matchAll(/\bt\(\s*'((?:\\'|[^'])*)'\s*\)/g)]
  .map((match) => match[1].replace(/\\'/g, "'"))
  .filter((key) => /[\u4e00-\u9fff]/.test(key));
const missing = [...new Set(usedKeys)].filter((key) => !translations.has(key)).sort();

if (missing.length) {
  console.error(`Missing English translations (${missing.length}):\n${missing.join('\n')}`);
  process.exitCode = 1;
} else {
  console.log(`i18n coverage: ${new Set(usedKeys).size} literal Chinese keys translated`);
}
