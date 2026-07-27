import Editor, { type Monaco } from '@monaco-editor/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import {
  AstraApiError,
  cloneSkill,
  createSkill,
  getSkill,
  getSkillDiff,
  getSkillFile,
  importSkill,
  listSkillRevisions,
  listSkills,
  publishSkill,
  removeSkill,
  restoreSkillRevision,
  setSkillEnabled,
  testSkillDraft,
  updateSkillFiles,
  validateSkill,
  type SkillDetail,
  type SkillDiagnostic,
  type SkillFile,
  type SkillRevision,
  type SkillSummary,
} from './api';
import { CloseButton } from './CloseButton';
import { useI18n } from './i18n';

type SaveState = 'saved' | 'dirty' | 'saving' | 'conflict' | 'error';
type FileOperation = { action: 'write' | 'delete' | 'move'; path: string; target?: string; content?: string };
type ConflictView = { path: string; base: string; local: string; remote: string };

const languageByExtension: Record<string, string> = {
  md: 'markdown', yaml: 'yaml', yml: 'yaml', json: 'json', py: 'python',
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', ts: 'typescript',
  tsx: 'typescript', jsx: 'javascript', sh: 'shell', bash: 'shell',
  html: 'html', css: 'css', txt: 'plaintext',
};

function editorLanguage(path: string) {
  return languageByExtension[path.split('.').pop()?.toLowerCase() ?? ''] ?? 'plaintext';
}

function markdownBody(value: string) {
  return value.replace(/^---\s*\n[\s\S]*?\n---\s*(?:\n|$)/, '');
}

function errorMessage(error: unknown, t: (text: string) => string) {
  return error instanceof AstraApiError ? t(error.payload.message)
    : error instanceof Error ? error.message : t('操作失败');
}

function toBase64(bytes: ArrayBuffer) {
  const view = new Uint8Array(bytes);
  let binary = '';
  for (let offset = 0; offset < view.length; offset += 0x8000) {
    binary += String.fromCharCode(...view.subarray(offset, offset + 0x8000));
  }
  return window.btoa(binary);
}

export function SkillWorkbench({
  onClose,
  onTestRun,
}: {
  onClose: () => void;
  onTestRun?: (runId: string) => void;
}) {
  const { language, t } = useI18n();
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selected, setSelected] = useState<SkillDetail | null>(null);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [tabs, setTabs] = useState<string[]>([]);
  const [buffers, setBuffers] = useState<Record<string, string>>({});
  const [saveState, setSaveState] = useState<SaveState>('saved');
  const [diagnostics, setDiagnostics] = useState<SkillDiagnostic[]>([]);
  const [query, setQuery] = useState('');
  const [fileQuery, setFileQuery] = useState('');
  const [preview, setPreview] = useState(true);
  const [revisions, setRevisions] = useState<SkillRevision[]>([]);
  const [diff, setDiff] = useState<Array<{ path: string; status: string; patch?: string | null }>>([]);
  const [inspector, setInspector] = useState<'diagnostics' | 'history' | 'diff'>('diagnostics');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [conflictView, setConflictView] = useState<ConflictView | null>(null);
  const [darkTheme, setDarkTheme] = useState(
    () => document.documentElement.dataset.theme === 'dark',
  );
  const saveTimer = useRef<number>();
  const tokenRef = useRef('');
  const baseBuffers = useRef<Record<string, string>>({});
  const monacoRef = useRef<Monaco | null>(null);

  const refreshList = useCallback(async (preferredId?: string) => {
    const items = await listSkills();
    setSkills(items);
    const id = preferredId ?? selected?.id ?? items[0]?.id;
    if (id) {
      const detail = await getSkill(id);
      setSelected(detail);
      tokenRef.current = detail.draft_revision_token ?? detail.active_revision?.id ?? '';
      setDiagnostics(detail.diagnostics);
    }
  }, [selected?.id]);

  useEffect(() => {
    void refreshList().catch((error) => setMessage(errorMessage(error, t)));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const updateTheme = () => setDarkTheme(
      document.documentElement.dataset.theme === 'dark',
    );
    const observer = new MutationObserver(updateTheme);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });
    updateTheme();
    return () => observer.disconnect();
  }, []);

  useEffect(() => () => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    for (const model of monacoRef.current?.editor.getModels() ?? []) {
      if (model.uri.scheme.startsWith('skill-')) model.dispose();
    }
  }, []);

  const selectSkill = async (skillId: string) => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    for (const model of monacoRef.current?.editor.getModels() ?? []) {
      if (model.uri.scheme.startsWith('skill-')) model.dispose();
    }
    setBusy(true);
    try {
      const detail = await getSkill(skillId);
      setSelected(detail);
      tokenRef.current = detail.draft_revision_token ?? detail.active_revision?.id ?? '';
      setTabs([]);
      setBuffers({});
      baseBuffers.current = {};
      setConflictView(null);
      setActivePath(null);
      setDiagnostics(detail.diagnostics);
      setDiff([]);
      setRevisions([]);
      setSaveState('saved');
      setMessage('');
    } catch (error) {
      setMessage(errorMessage(error, t));
    } finally {
      setBusy(false);
    }
  };

  const openFile = async (file: SkillFile) => {
    if (!file.text) {
      setMessage(
        t('二进制资源 {path} 可导出或由 Sandbox 只读使用，不能在编辑器中打开。')
          .replace('{path}', file.path),
      );
      return;
    }
    setActivePath(file.path);
    setTabs((items) => items.includes(file.path) ? items : [...items, file.path]);
    if (buffers[file.path] !== undefined || !selected) return;
    try {
      const loaded = await getSkillFile(selected.id, file.path);
      const content = loaded.content ?? '';
      baseBuffers.current[file.path] = content;
      setBuffers((items) => ({ ...items, [file.path]: content }));
    } catch (error) {
      setMessage(errorMessage(error, t));
    }
  };

  const applyOperations = async (operations: FileOperation[]) => {
    if (!selected || selected.readonly) return;
    setSaveState('saving');
    try {
      const result = await updateSkillFiles(selected.id, tokenRef.current, operations);
      tokenRef.current = result.revision_token;
      setSelected((item) => item ? {
        ...item,
        files: result.files,
        draft_revision_token: result.revision_token,
        diagnostics: result.diagnostics,
      } : item);
      setDiagnostics(result.diagnostics);
      for (const operation of operations) {
        if (operation.action === 'write') {
          baseBuffers.current[operation.path] = operation.content ?? '';
        }
      }
      setConflictView(null);
      setSaveState('saved');
      setMessage(t('草稿已保存'));
    } catch (error) {
      if (error instanceof AstraApiError && error.payload.code === 'SKILL_DRAFT_STALE') {
        const write = operations.find((item) => item.action === 'write');
        if (write) {
          try {
            const [remote, latest] = await Promise.all([
              getSkillFile(selected.id, write.path),
              getSkill(selected.id),
            ]);
            tokenRef.current = latest.draft_revision_token ?? tokenRef.current;
            setSelected(latest);
            setConflictView({
              path: write.path,
              base: baseBuffers.current[write.path] ?? '',
              local: write.content ?? '',
              remote: remote.content ?? '',
            });
          } catch {
            // Preserve the local buffer if the latest remote Draft cannot be loaded.
          }
        }
        setSaveState('conflict');
        setMessage(t('草稿已在其他窗口变化。请比较基线、本地与远端版本后选择。'));
      } else {
        setSaveState('error');
        setMessage(errorMessage(error, t));
      }
    }
  };

  const changeContent = (value: string | undefined) => {
    if (!activePath || selected?.readonly) return;
    const content = value ?? '';
    setBuffers((items) => ({ ...items, [activePath]: content }));
    setSaveState('dirty');
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    const path = activePath;
    saveTimer.current = window.setTimeout(() => {
      void applyOperations([{ action: 'write', path, content }]);
    }, 700);
  };

  const refreshDetail = async () => {
    if (!selected) return;
    await selectSkill(selected.id);
    await refreshList(selected.id);
  };

  const perform = async (action: () => Promise<void>) => {
    setBusy(true);
    setMessage('');
    try {
      await action();
    } catch (error) {
      setMessage(errorMessage(error, t));
    } finally {
      setBusy(false);
    }
  };

  const createNew = () => {
    const name = window.prompt(t('Skill 名称（小写字母、数字、连字符）'));
    if (!name) return;
    const description = window.prompt(t('Skill 描述')) ?? '';
    if (!description) return;
    void perform(async () => {
      const created = await createSkill(name.trim(), description.trim());
      await refreshList(created.id);
      await selectSkill(created.id);
    });
  };

  const createFile = () => {
    if (!selected || selected.readonly) return;
    const path = window.prompt(t('新文件路径，例如 references/guide.md'));
    if (!path) return;
    void applyOperations([{ action: 'write', path, content: '' }]).then(() => refreshDetail());
  };

  const renameFile = () => {
    if (!selected || selected.readonly || !activePath || activePath === 'SKILL.md') return;
    const target = window.prompt(t('新路径'), activePath);
    if (!target || target === activePath) return;
    const previous = activePath;
    void applyOperations([{ action: 'move', path: previous, target }]).then(async () => {
      setTabs((items) => items.map((item) => item === previous ? target : item));
      setBuffers((items) => {
        const next = { ...items, [target]: items[previous] ?? '' };
        delete next[previous];
        return next;
      });
      setActivePath(target);
      await refreshDetail();
    });
  };

  const deleteFile = () => {
    if (!selected || selected.readonly || !activePath || activePath === 'SKILL.md') return;
    if (!window.confirm(t('删除 {path}？').replace('{path}', activePath))) return;
    const path = activePath;
    void applyOperations([{ action: 'delete', path }]).then(async () => {
      setTabs((items) => items.filter((item) => item !== path));
      setActivePath(null);
      await refreshDetail();
    });
  };

  const visibleSkills = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return skills.filter((item) => !needle
      || `${item.name} ${item.description} ${item.origin}`.toLowerCase().includes(needle));
  }, [query, skills]);
  const visibleFiles = useMemo(() => {
    const needle = fileQuery.trim().toLowerCase();
    return (selected?.files ?? []).filter((item) => !needle || item.path.toLowerCase().includes(needle));
  }, [fileQuery, selected?.files]);
  const activeFile = selected?.files.find((item) => item.path === activePath);
  const activeContent = activePath ? buffers[activePath] : undefined;
  const saveStateLabel: Record<SaveState, string> = {
    saved: t('已保存'),
    dirty: t('未保存'),
    saving: t('保存中…'),
    conflict: t('版本冲突'),
    error: t('保存失败'),
  };
  const lifecycleLabel = (state: string) => ({
    draft: t('草稿'),
    published: t('已发布'),
    disabled: t('已停用'),
    removed: t('已移除'),
  })[state] ?? state;

  return <section className="skill-workbench">
    <header className="skill-header">
      <div><span>Astra Skills</span><h2>{t('Skill 资料库')}</h2><p>{t('共享、版本化、可审计的 Agent 工作流')}</p></div>
      <div className="skill-header-actions">
        <button type="button" onClick={createNew}>{t('新建')}</button>
        <label className="skill-import-button">{t('导入 ZIP')}<input type="file" accept=".zip" onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (!file) return;
          void perform(async () => {
            const imported = await importSkill(file.name, toBase64(await file.arrayBuffer()));
            await refreshList(imported.id);
            await selectSkill(imported.id);
          });
          event.currentTarget.value = '';
        }} /></label>
        <CloseButton className="settings-close" label={t('关闭 Skill 资料库')} onClick={onClose} />
      </div>
    </header>

    <div className="skill-layout">
      <aside className="skill-catalog-pane">
        <input aria-label={t('搜索 Skill')} placeholder={t('搜索 Skill')} value={query} onChange={(event) => setQuery(event.currentTarget.value)} />
        <div className="skill-list">
          {visibleSkills.map((skill) => <button className={selected?.id === skill.id ? 'active' : ''} type="button" key={skill.id} onClick={() => void selectSkill(skill.id)}>
            <span><b>{skill.name}</b><em className={`origin-${skill.origin}`}>{skill.origin === 'builtin' ? t('Astra 内建') : t('自定义')}</em></span>
            <small>{skill.description}</small>
            <i>{lifecycleLabel(skill.lifecycle_state)} · {skill.active_revision ? `v${skill.active_revision.version}` : t('未发布')}{!skill.enabled ? ` · ${t('已停用')}` : ''}</i>
          </button>)}
        </div>
      </aside>

      {!selected ? <div className="skill-empty">{t('选择或创建一个 Skill')}</div> : <>
        <aside className="skill-file-pane">
          <div className="skill-file-heading"><strong>{selected.name}</strong><span>{selected.readonly ? t('只读') : saveStateLabel[saveState]}</span></div>
          <input aria-label={t('搜索 Skill 文件')} placeholder={t('搜索文件')} value={fileQuery} onChange={(event) => setFileQuery(event.currentTarget.value)} />
          <div className="skill-file-actions">
            <button type="button" disabled={selected.readonly} onClick={createFile}>＋ {t('文件')}</button>
            <button type="button" disabled={selected.readonly || !activePath || activePath === 'SKILL.md'} onClick={renameFile}>{t('重命名')}</button>
            <button type="button" disabled={selected.readonly || !activePath || activePath === 'SKILL.md'} onClick={deleteFile}>{t('删除')}</button>
          </div>
          <div className="skill-tree" role="tree">
            {visibleFiles.map((file) => <button role="treeitem" type="button" className={activePath === file.path ? 'active' : ''} key={file.path} onClick={() => void openFile(file)}>
              <span>{file.kind === 'script' ? '⌘' : file.kind === 'asset' ? '◇' : '▤'}</span>{file.path}<small>{file.text ? '' : t('二进制')}</small>
            </button>)}
          </div>
          <div className="skill-meta">
            <span>{selected.qualified_identity}</span>
            <span>{selected.compatibility || t('无兼容性声明')}</span>
            <span>{selected.requested_tool_patterns.length ? `${t('请求能力')}${language === 'en' ? ': ' : '：'}${selected.requested_tool_patterns.join(', ')}` : t('未请求工具能力')}</span>
          </div>
        </aside>

        <main className="skill-editor-pane">
          <div className="skill-commandbar">
            <button type="button" disabled={busy} onClick={() => void perform(async () => {
              const report = await validateSkill(selected.id);
              setDiagnostics(report.diagnostics);
              setInspector('diagnostics');
              setMessage(t(report.publishable ? '校验通过，可以发布' : '存在阻止发布的问题'));
            })}>{t('校验')}</button>
            {!selected.readonly && <button className="primary" type="button" disabled={busy || saveState === 'dirty' || saveState === 'saving'} onClick={() => void perform(async () => {
              await publishSkill(selected.id, tokenRef.current);
              await refreshDetail();
              setMessage(t('已发布不可变 Revision'));
            })}>{t('发布')}</button>}
            {selected.origin === 'builtin' && <button type="button" onClick={() => {
              const name = window.prompt(t('克隆为自定义 Skill'), `${selected.name.replace(/^astra-/, '')}-custom`);
              if (name) void perform(async () => {
                const clone = await cloneSkill(selected.id, name);
                await refreshList(clone.id);
                await selectSkill(clone.id);
              });
            }}>{t('克隆')}</button>}
            <button type="button" onClick={() => void perform(async () => {
              await setSkillEnabled(selected.id, !selected.enabled);
              await refreshDetail();
            })}>{t(selected.enabled ? '停用' : '启用')}</button>
            <a href={`/api/skills/${selected.id}/export`}>{t('导出')}</a>
            {!selected.readonly && <button type="button" onClick={() => {
              const goal = window.prompt(t('Draft 测试目标'));
              if (!goal) return;
              const trusted = window.confirm(t('使用可信模式测试？取消则使用快速模式。'));
              void perform(async () => {
                const run = await testSkillDraft(selected.id, tokenRef.current, goal, trusted ? 'trusted' : 'standard');
                setMessage(t('Draft 测试已创建：{id}').replace('{id}', run.run_id));
                onTestRun?.(run.run_id);
              });
            }}>{t('测试 Draft')}</button>}
            {!selected.readonly && <button className="danger" type="button" onClick={() => {
              if (!window.confirm(t('移除 {name}？历史 Revision 仍可审计。').replace('{name}', selected.name))) return;
              void perform(async () => { await removeSkill(selected.id); setSelected(null); await refreshList(); });
            }}>{t('移除')}</button>}
            <span className={`skill-save-state state-${saveState}`}>{saveStateLabel[saveState]}</span>
          </div>

          <div className="skill-tabs">
            {tabs.map((path) => <div className={`skill-tab ${path === activePath ? 'active' : ''}`} key={path}>
              <button type="button" onClick={() => setActivePath(path)}>{path.split('/').pop()}</button>
              <CloseButton className="skill-tab-close" label={t('关闭文件 {name}').replace('{name}', path)} onClick={() => {
                setTabs((items) => items.filter((item) => item !== path));
                if (activePath === path) setActivePath(tabs.find((item) => item !== path) ?? null);
              }} />
            </div>)}
            {activePath === 'SKILL.md' && <button type="button" aria-pressed={preview} onClick={() => setPreview((value) => !value)}>{t('预览')}</button>}
          </div>

          {!activeFile ? <div className="skill-editor-empty"><strong>{t('打开一个文件开始编辑')}</strong><span>{t('SKILL.md 定义工作流，scripts/、references/ 与 assets/ 按需披露。')}</span></div>
            : !activeFile.text ? <div className="skill-editor-empty">{t('二进制资源不在浏览器中解码或执行。')}</div>
              : <div className={`skill-editor-grid ${activePath === 'SKILL.md' && preview ? 'with-preview' : ''}`}>
                <Editor
                  theme={darkTheme ? 'vs-dark' : 'light'}
                  height="100%"
                  path={activeFile.uri}
                  language={editorLanguage(activeFile.path)}
                  value={activeContent ?? ''}
                  onChange={changeContent}
                  onMount={(_, monaco) => { monacoRef.current = monaco; }}
                  options={{
                    readOnly: selected.readonly,
                    minimap: { enabled: false },
                    wordWrap: activePath === 'SKILL.md' ? 'on' : 'off',
                    automaticLayout: true,
                    fontSize: 13,
                    tabSize: 2,
                    renderValidationDecorations: 'on',
                    accessibilitySupport: 'auto',
                  }}
                />
                {activePath === 'SKILL.md' && preview && <article className="skill-markdown-preview">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{markdownBody(activeContent ?? '')}</ReactMarkdown>
                </article>}
              </div>}

          <section className="skill-inspector">
            <nav>
              <button type="button" className={inspector === 'diagnostics' ? 'active' : ''} onClick={() => setInspector('diagnostics')}>{t('问题 {count}').replace('{count}', String(diagnostics.length))}</button>
              <button type="button" className={inspector === 'history' ? 'active' : ''} onClick={() => {
                setInspector('history');
                void listSkillRevisions(selected.id).then(setRevisions).catch((error) => setMessage(errorMessage(error, t)));
              }}>{t('历史')}</button>
              <button type="button" className={inspector === 'diff' ? 'active' : ''} onClick={() => {
                setInspector('diff');
                void getSkillDiff(selected.id).then((result) => setDiff(result.files)).catch((error) => setMessage(errorMessage(error, t)));
              }}>{t('Draft / 已发布版本差异')}</button>
            </nav>
            <div className="skill-inspector-body">
              {conflictView && <div className="skill-three-way" role="region" aria-label={t('三方版本比较')}>
                <header><strong>{t('{path} · 三方版本比较').replace('{path}', conflictView.path)}</strong>
                  <button type="button" onClick={() => {
                    baseBuffers.current[conflictView.path] = conflictView.remote;
                    setBuffers((items) => ({ ...items, [conflictView.path]: conflictView.remote }));
                    setConflictView(null);
                    setSaveState('saved');
                  }}>{t('采用远端')}</button>
                  <button type="button" onClick={() => {
                    const pending = conflictView;
                    setConflictView(null);
                    void applyOperations([{ action: 'write', path: pending.path, content: pending.local }]);
                  }}>{t('保留本地并重试')}</button>
                </header>
                <div><label>{t('共同基线')}</label><pre>{conflictView.base}</pre></div>
                <div><label>{t('本地修改')}</label><pre>{conflictView.local}</pre></div>
                <div><label>{t('远端草稿')}</label><pre>{conflictView.remote}</pre></div>
              </div>}
              {inspector === 'diagnostics' && (diagnostics.length
                ? diagnostics.map((item, index) => <button type="button" key={`${item.code}-${index}`} onClick={() => item.path && selected.files.find((file) => file.path === item.path) && void openFile(selected.files.find((file) => file.path === item.path)!)}>
                  <b>{item.severity}</b><span>{item.path ?? 'package'}{item.line ? `:${item.line}` : ''}</span>{item.message}
                </button>)
                : <span>{t('没有诊断问题')}</span>)}
              {inspector === 'history' && (revisions.length
                ? revisions.map((revision) => <div key={revision.id}><span>v{revision.version}</span><code>{revision.digest.slice(0, 20)}…</code><time>{revision.published_at ? new Date(revision.published_at).toLocaleString(language === 'en' ? 'en-US' : 'zh-CN') : ''}</time>{!selected.readonly && <button type="button" onClick={() => void perform(async () => { await restoreSkillRevision(selected.id, revision.id); await refreshDetail(); })}>{t('恢复到 Draft')}</button>}</div>)
                : <span>{t('尚无已发布 Revision')}</span>)}
              {inspector === 'diff' && (diff.length
                ? diff.map((item) => <details key={item.path}><summary><b>{item.status}</b> {item.path}</summary>{item.patch && <pre>{item.patch}</pre>}</details>)
                : <span>{t('没有差异')}</span>)}
            </div>
          </section>
          {(message || busy) && <div className="skill-toast" role="status">{busy ? t('处理中…') : message}</div>}
        </main>
      </>}
    </div>
  </section>;
}
