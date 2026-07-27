import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const sourceFiles = [
  'src/App.tsx',
  'src/CloseButton.tsx',
  'src/GraphPaneWindowActions.tsx',
  'src/TrustedExecutionGraph.tsx',
  'src/UsageDashboard.tsx',
  'src/SharedConversationPage.tsx',
  'src/SkillWorkbench.tsx',
  'src/processStream.ts',
];
const source = sourceFiles.map((file) => readFileSync(resolve(file), 'utf8')).join('\n');
const dictionary = readFileSync(resolve('src/i18n.tsx'), 'utf8');
const translationKeys = [...dictionary.matchAll(/'((?:\\'|[^'])*)'\s*:/g)].map((match) => match[1].replace(/\\'/g, "'"));
const translations = new Set(translationKeys);
const duplicateTranslations = [...new Set(translationKeys.filter((key, index) => translationKeys.indexOf(key) !== index))].sort();
const usedKeys = [...source.matchAll(/\bt\(\s*'((?:\\'|[^'])*)'\s*\)/g)]
  .map((match) => match[1].replace(/\\'/g, "'"))
  .filter((key) => /[\u4e00-\u9fff]/.test(key));
const missing = [...new Set(usedKeys)].filter((key) => !translations.has(key)).sort();
const semanticKeyFiles = ['src/TrustedExecutionGraph.tsx', 'src/processStream.ts'];
const semanticKeys = semanticKeyFiles.flatMap((file) => {
  const content = readFileSync(resolve(file), 'utf8');
  return [...content.matchAll(/'([^'\n]*[\u4e00-\u9fff][^'\n]*)'/g)].map((match) => match[1]);
});
const missingSemanticKeys = [...new Set(semanticKeys)].filter((key) => !translations.has(key)).sort();
const bannedUiPhrases = [
  '前端估算',
  '持久化统计',
  '正在读取持久化',
  '状态版本',
  '内部运行时异常',
  '完整计划生成并持久化',
];
const leakedImplementationCopy = bannedUiPhrases.filter((phrase) => source.includes(phrase));
const rawCloseGlyphFiles = sourceFiles.filter((file) => />×<\/button>/.test(readFileSync(resolve(file), 'utf8')));

if (missing.length || missingSemanticKeys.length || duplicateTranslations.length || leakedImplementationCopy.length || rawCloseGlyphFiles.length) {
  if (missing.length) {
  console.error(`Missing English translations (${missing.length}):\n${missing.join('\n')}`);
  }
  if (missingSemanticKeys.length) {
    console.error(`Missing translations for graph/process labels (${missingSemanticKeys.length}):\n${missingSemanticKeys.join('\n')}`);
  }
  if (leakedImplementationCopy.length) {
    console.error(`Implementation-detail copy exposed in UI:\n${leakedImplementationCopy.join('\n')}`);
  }
  if (duplicateTranslations.length) {
    console.error(`Duplicate English translation keys:\n${duplicateTranslations.join('\n')}`);
  }
  if (rawCloseGlyphFiles.length) {
    console.error(`Use the shared CloseButton instead of a raw × glyph:\n${rawCloseGlyphFiles.join('\n')}`);
  }
  process.exitCode = 1;
} else {
  console.log(`i18n coverage: ${new Set(usedKeys).size} literal Chinese keys translated; shared UI guardrails passed`);
}
