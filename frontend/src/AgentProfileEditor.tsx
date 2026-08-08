import Editor from '@monaco-editor/react';
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import type { AgentProfileDocuments } from './api';
import { CloseButton } from './CloseButton';
import { useI18n } from './i18n';

export type AgentProfileDocumentName = keyof AgentProfileDocuments;

const agentProfileFiles: Array<{ name: AgentProfileDocumentName; filename: string; description: string }> = [
  { name: 'identity', filename: 'IDENTITY.md', description: '身份、使命、目标与边界' },
  { name: 'soul', filename: 'SOUL.md', description: '人格、沟通方式与协作原则' },
  { name: 'memory', filename: 'MEMORY.md', description: '记忆写入、召回与遗忘治理' },
  { name: 'autodream', filename: 'AUTODREAM.md', description: '后台记忆整理治理协议' },
];

function MarkdownFileIcon() {
  return <svg className="skill-ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <rect x="3.5" y="5.5" width="17" height="13" rx="2" />
    <path d="M6.5 15v-6l2.5 3 2.5-3v6M16 9v6m-2-2 2 2 2-2" />
  </svg>;
}

export function AgentProfileEditor({
  documents,
  defaultDocuments,
  initialDocument,
  dirty,
  busy,
  message,
  error,
  onChange,
  onSave,
  onClose,
}: {
  documents: AgentProfileDocuments;
  defaultDocuments: AgentProfileDocuments;
  initialDocument: AgentProfileDocumentName;
  dirty: boolean;
  busy: boolean;
  message: string;
  error: boolean;
  onChange: (name: AgentProfileDocumentName, value: string) => void;
  onSave: () => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [activeName, setActiveName] = useState(initialDocument);
  const [preview, setPreview] = useState(true);
  const activeFile = agentProfileFiles.find((file) => file.name === activeName) ?? agentProfileFiles[0];

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [busy, onClose]);

  return createPortal(<div className="skill-modal-layer editor-layer agent-profile-editor-layer">
    <section className="skill-editor-dialog" role="dialog" aria-modal="true" aria-labelledby="agent-profile-editor-title">
      <header className="skill-editor-titlebar">
        <div><span className="skill-dialog-icon">A</span><strong id="agent-profile-editor-title">{t('Astra 身份文档')}</strong><small>{dirty ? t('有未保存修改') : t('配置已同步')}</small></div>
        <div className="skill-editor-window-actions">
          <button type="button" disabled={busy} onClick={onClose}>{t('返回设置')}</button>
          <CloseButton label={t('关闭身份文档编辑器')} onClick={onClose} />
        </div>
      </header>
      <div className="skill-editor-shell">
        <aside className="skill-explorer">
          <header><strong>{t('身份文档')}</strong><span>{t('{count} 个文件').replace('{count}', String(agentProfileFiles.length))}</span></header>
          <div className="agent-profile-editor-files" role="tree">
            {agentProfileFiles.map((file) => <button
              className={`skill-tree-row file ${activeName === file.name ? 'active' : ''}`}
              role="treeitem"
              type="button"
              key={file.name}
              onClick={() => setActiveName(file.name)}
            >
              <span className="skill-file-icon kind-instructions"><MarkdownFileIcon /></span>
              <span>{file.filename}</span>
            </button>)}
          </div>
          <div className="skill-explorer-meta"><span>{t(activeFile.description)}</span><span>Agent Profile · Markdown</span></div>
        </aside>
        <main className="skill-editor-pane agent-profile-editor-pane">
          <div className="skill-commandbar">
            <button className="primary" type="button" disabled={!dirty || busy} onClick={onSave}>{t(busy ? '正在保存…' : '保存 Agent Profile')}</button>
            <button type="button" disabled={busy || documents[activeName] === defaultDocuments[activeName]} onClick={() => {
              if (window.confirm(t('恢复 {name} 的内置默认内容？').replace('{name}', activeFile.filename))) onChange(activeName, defaultDocuments[activeName]);
            }}>{t('恢复内置默认')}</button>
            <span className={`skill-save-state ${error ? 'state-error' : dirty ? 'state-dirty' : 'state-saved'}`}>{error ? t('保存 Agent Profile 失败') : dirty ? t('有未保存修改') : t('配置已同步')}</span>
          </div>
          <div className="skill-tabs">
            <div className="skill-tab active"><button type="button">{activeFile.filename}</button></div>
            <button type="button" aria-pressed={preview} onClick={() => setPreview((value) => !value)}>{t('预览')}</button>
          </div>
          <div className={`skill-editor-grid ${preview ? 'with-preview' : ''}`}>
            <Editor
              theme={document.documentElement.dataset.theme === 'dark' ? 'vs-dark' : 'light'}
              height="100%"
              path={`agent-profile://${activeFile.filename}`}
              language="markdown"
              value={documents[activeName]}
              onChange={(value) => onChange(activeName, value ?? '')}
              options={{ readOnly: busy, minimap: { enabled: false }, wordWrap: 'on', automaticLayout: true, fontSize: 13, tabSize: 2, accessibilitySupport: 'auto', ariaLabel: activeFile.filename, scrollBeyondLastLine: false }}
            />
            {preview && <article className="skill-markdown-preview"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{documents[activeName]}</ReactMarkdown></article>}
          </div>
          {message && <div className={`skill-toast ${error ? 'error' : ''}`} role="status">{t(message)}</div>}
        </main>
      </div>
    </section>
  </div>, document.body);
}
