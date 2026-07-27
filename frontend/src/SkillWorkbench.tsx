import Editor, { type Monaco } from '@monaco-editor/react';
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import {
  AstraApiError,
  cloneSkill,
  createSkill,
  getSkill,
  getSkillDiff,
  getSkillFile,
  getSkillRevision,
  getSkillRevisionDiff,
  getSkillRevisionFile,
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
  type SkillRevisionDetail,
  type SkillRevisionDiff,
  type SkillSummary,
} from './api';
import { CloseButton } from './CloseButton';
import { useI18n } from './i18n';

type SaveState = 'saved' | 'dirty' | 'saving' | 'conflict' | 'error';
type FileOperation = { action: 'write' | 'delete' | 'move'; path: string; target?: string; content?: string };
type ConflictView = { path: string; base: string; local: string; remote: string };
type DisplayMode = 'grid' | 'list';
type SortMode = 'updated' | 'created' | 'name';
type OriginFilter = 'all' | 'builtin' | 'custom';
type StateFilter = 'all' | 'enabled' | 'disabled';
type TreeNode = { name: string; path: string; kind: 'folder'; children: TreeItem[] } | { name: string; path: string; kind: 'file'; file: SkillFile };
type TreeItem = TreeNode;
type SkillActionDialog =
  | { kind: 'new-file' | 'new-folder' | 'rename-file'; value: string }
  | { kind: 'delete-file' | 'delete-skill' }
  | { kind: 'test-draft'; value: string; mode: 'standard' | 'trusted' };

const languageByExtension: Record<string, string> = {
  md: 'markdown', yaml: 'yaml', yml: 'yaml', json: 'json', py: 'python',
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', ts: 'typescript',
  tsx: 'typescript', jsx: 'javascript', sh: 'shell', bash: 'shell',
  html: 'html', css: 'css', txt: 'plaintext',
};

function editorLanguage(path: string) {
  return languageByExtension[path.split('.').pop()?.toLowerCase() ?? ''] ?? 'plaintext';
}

function editorModelPath(skillId: string, filePath: string) {
  const encodedPath = filePath.split('/').map(encodeURIComponent).join('/');
  return `skill-editor://${encodeURIComponent(skillId)}/${encodedPath}`;
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

function formatDate(value: string | null | undefined, language: string) {
  return value ? new Date(value).toLocaleString(language === 'en' ? 'en-US' : 'zh-CN') : '—';
}

function buildFileTree(files: SkillFile[], virtualFolders: string[] = []): TreeItem[] {
  type MutableFolder = { name: string; path: string; kind: 'folder'; children: Map<string, MutableFolder | TreeItem> };
  const root: MutableFolder = { name: '', path: '', kind: 'folder', children: new Map() };
  const ensureFolder = (path: string) => {
    let parent = root;
    let current = '';
    for (const name of path.split('/').filter(Boolean)) {
      current = current ? `${current}/${name}` : name;
      const existing = parent.children.get(name);
      if (existing?.kind === 'folder') {
        parent = existing as MutableFolder;
      } else {
        const folder: MutableFolder = { name, path: current, kind: 'folder', children: new Map() };
        parent.children.set(name, folder);
        parent = folder;
      }
    }
  };
  virtualFolders.forEach(ensureFolder);
  for (const file of files) {
    const parts = file.path.split('/');
    const name = parts.pop() ?? file.path;
    const folderPath = parts.join('/');
    ensureFolder(folderPath);
    let parent = root;
    for (const segment of parts) parent = parent.children.get(segment) as MutableFolder;
    parent.children.set(name, { name, path: file.path, kind: 'file', file });
  }
  const freeze = (folder: MutableFolder): TreeItem[] => Array.from(folder.children.values())
    .sort((a, b) => a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'folder' ? -1 : 1)
    .map((item) => item.kind === 'folder'
      ? { name: item.name, path: item.path, kind: 'folder', children: freeze(item as MutableFolder) }
      : item as TreeItem);
  return freeze(root);
}

function SkillTree({
  files,
  activePath,
  selectedFolder,
  virtualFolders,
  onOpenFile,
  onSelectFolder,
  readonly = false,
}: {
  files: SkillFile[];
  activePath: string | null;
  selectedFolder: string;
  virtualFolders?: string[];
  onOpenFile: (file: SkillFile) => void;
  onSelectFolder: (path: string) => void;
  readonly?: boolean;
}) {
  const { t } = useI18n();
  const tree = useMemo(() => buildFileTree(files, virtualFolders), [files, virtualFolders]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const renderNodes = (nodes: TreeItem[], depth = 0): ReactNode => nodes.map((node) => {
    if (node.kind === 'folder') {
      const isCollapsed = collapsed.has(node.path);
      return <div key={`folder-${node.path}`}>
        <button
          className={`skill-tree-row folder ${selectedFolder === node.path ? 'selected' : ''}`}
          role="treeitem"
          aria-expanded={!isCollapsed}
          type="button"
          style={{ paddingInlineStart: `${8 + depth * 14}px` }}
          onClick={() => {
            onSelectFolder(node.path);
            setCollapsed((items) => {
              const next = new Set(items);
              if (next.has(node.path)) next.delete(node.path); else next.add(node.path);
              return next;
            });
          }}
        >
          <span className="skill-tree-chevron">{isCollapsed ? '›' : '⌄'}</span>
          <span className={`skill-tree-icon ${isCollapsed ? '' : 'open'}`} aria-hidden="true" />
          <span>{node.name}</span>
        </button>
        {!isCollapsed && renderNodes(node.children, depth + 1)}
      </div>;
    }
    return <button
      className={`skill-tree-row file ${activePath === node.path ? 'active' : ''}`}
      role="treeitem"
      type="button"
      key={node.path}
      style={{ paddingInlineStart: `${25 + depth * 14}px` }}
      onClick={() => onOpenFile(node.file)}
    >
      <span className={`skill-file-type kind-${node.file.kind}`}>{node.file.path.endsWith('.md') ? 'MD' : node.file.kind === 'script' ? '</>' : node.file.kind === 'asset' ? 'IMG' : '{}'}</span>
      <span>{node.name}</span>
      {!node.file.text && <small>{t('二进制')}</small>}
      {readonly && <small>◉</small>}
    </button>;
  });
  return <div className="skill-tree" role="tree">
    <button className={`skill-tree-row folder root ${selectedFolder === '' ? 'selected' : ''}`} role="treeitem" aria-expanded type="button" onClick={() => onSelectFolder('')}>
      <span className="skill-tree-chevron">⌄</span><span className="skill-tree-icon open" aria-hidden="true" /><span>{t('Skill 根目录')}</span>
    </button>
    {renderNodes(tree)}
  </div>;
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
  const [editorOpen, setEditorOpen] = useState(false);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [selectedFolder, setSelectedFolder] = useState('');
  const [virtualFolders, setVirtualFolders] = useState<string[]>([]);
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
  const [displayMode, setDisplayMode] = useState<DisplayMode>('grid');
  const [sortMode, setSortMode] = useState<SortMode>('updated');
  const [originFilter, setOriginFilter] = useState<OriginFilter>('all');
  const [stateFilter, setStateFilter] = useState<StateFilter>('all');
  const [historyRevision, setHistoryRevision] = useState<SkillRevisionDetail | null>(null);
  const [historyPath, setHistoryPath] = useState<string | null>(null);
  const [historyContent, setHistoryContent] = useState('');
  const [historyMode, setHistoryMode] = useState<'files' | 'diff'>('files');
  const [historyDiff, setHistoryDiff] = useState<SkillRevisionDiff | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createDescription, setCreateDescription] = useState('');
  const [createError, setCreateError] = useState('');
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [actionDialog, setActionDialog] = useState<SkillActionDialog | null>(null);
  const [darkTheme, setDarkTheme] = useState(() => document.documentElement.dataset.theme === 'dark');
  const saveTimer = useRef<number>();
  const tokenRef = useRef('');
  const baseBuffers = useRef<Record<string, string>>({});
  const monacoRef = useRef<Monaco | null>(null);

  const refreshList = useCallback(async () => {
    setSkills(await listSkills());
  }, []);

  useEffect(() => {
    void refreshList().catch((error) => setMessage(errorMessage(error, t)));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const updateTheme = () => setDarkTheme(document.documentElement.dataset.theme === 'dark');
    const observer = new MutationObserver(updateTheme);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    updateTheme();
    return () => observer.disconnect();
  }, []);

  useEffect(() => () => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    for (const model of monacoRef.current?.editor.getModels() ?? []) {
      if (model.uri.scheme.startsWith('skill-')) model.dispose();
    }
  }, []);

  useEffect(() => {
    if (!createOpen && !actionDialog) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !createSubmitting) setCreateOpen(false);
      if (event.key === 'Escape' && !busy) setActionDialog(null);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [actionDialog, busy, createOpen, createSubmitting]);

  const resetEditor = () => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    for (const model of monacoRef.current?.editor.getModels() ?? []) {
      if (model.uri.scheme.startsWith('skill-')) model.dispose();
    }
    setTabs([]);
    setBuffers({});
    setVirtualFolders([]);
    baseBuffers.current = {};
    setConflictView(null);
    setActivePath(null);
    setSelectedFolder('');
    setDiff([]);
    setRevisions([]);
    setSaveState('saved');
  };

  const selectSkill = async (skillId: string, openEditor = false) => {
    setBusy(true);
    try {
      const detail = await getSkill(skillId);
      resetEditor();
      setSelected(detail);
      tokenRef.current = detail.draft_revision_token ?? detail.active_revision?.id ?? '';
      setDiagnostics(detail.diagnostics);
      setEditorOpen(openEditor);
      setMessage('');
    } catch (error) {
      setMessage(errorMessage(error, t));
    } finally {
      setBusy(false);
    }
  };

  const closeDetail = () => {
    setSelected(null);
    setEditorOpen(false);
    resetEditor();
  };

  const openFile = async (file: SkillFile) => {
    if (!file.text) {
      setMessage(t('二进制资源 {path} 可导出或由 Sandbox 只读使用，不能在编辑器中打开。').replace('{path}', file.path));
      return;
    }
    setActivePath(file.path);
    setSelectedFolder(file.path.includes('/') ? file.path.split('/').slice(0, -1).join('/') : '');
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
      setSelected((item) => item ? { ...item, files: result.files, draft_revision_token: result.revision_token, diagnostics: result.diagnostics, updated_at: new Date().toISOString() } : item);
      setDiagnostics(result.diagnostics);
      for (const operation of operations) {
        if (operation.action === 'write') baseBuffers.current[operation.path] = operation.content ?? '';
      }
      setConflictView(null);
      setSaveState('saved');
      setMessage(t('草稿已保存'));
    } catch (error) {
      if (error instanceof AstraApiError && error.payload.code === 'SKILL_DRAFT_STALE') {
        const write = operations.find((item) => item.action === 'write');
        if (write) {
          try {
            const [remote, latest] = await Promise.all([getSkillFile(selected.id, write.path), getSkill(selected.id)]);
            tokenRef.current = latest.draft_revision_token ?? tokenRef.current;
            setSelected(latest);
            setConflictView({ path: write.path, base: baseBuffers.current[write.path] ?? '', local: write.content ?? '', remote: remote.content ?? '' });
          } catch {
            // Keep the local buffer visible if the latest Draft cannot be fetched.
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
    saveTimer.current = window.setTimeout(() => void applyOperations([{ action: 'write', path, content }]), 700);
  };

  const refreshDetail = async () => {
    if (!selected) return;
    const detail = await getSkill(selected.id);
    setSelected(detail);
    tokenRef.current = detail.draft_revision_token ?? detail.active_revision?.id ?? '';
    setDiagnostics(detail.diagnostics);
    await refreshList();
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

  const openCreateDrawer = () => {
    setCreateName('');
    setCreateDescription('');
    setCreateError('');
    setCreateOpen(true);
  };

  const closeCreateDrawer = () => {
    if (createSubmitting) return;
    setCreateOpen(false);
    setCreateError('');
  };

  const createNameValid = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(createName) && !createName.includes('--');
  const canCreate = createNameValid && createDescription.trim().length > 0 && !createSubmitting;

  const createNew = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canCreate) return;
    setCreateSubmitting(true);
    setCreateError('');
    try {
      const created = await createSkill(createName.trim(), createDescription.trim());
      await refreshList();
      setCreateOpen(false);
      await selectSkill(created.id, true);
    } catch (error) {
      setCreateError(errorMessage(error, t));
    } finally {
      setCreateSubmitting(false);
    }
  };

  const createFolder = () => {
    if (!selected || selected.readonly) return;
    const initial = selectedFolder ? `${selectedFolder}/` : '';
    setActionDialog({ kind: 'new-folder', value: initial });
  };

  const confirmCreateFolder = (pathValue: string) => {
    const path = pathValue.trim().replace(/^\/+|\/+$/g, '');
    if (!path) return;
    setVirtualFolders((items) => items.includes(path) ? items : [...items, path]);
    setSelectedFolder(path);
    setActionDialog(null);
  };

  const createFile = () => {
    if (!selected || selected.readonly) return;
    const initial = selectedFolder ? `${selectedFolder}/` : '';
    setActionDialog({ kind: 'new-file', value: initial });
  };

  const confirmCreateFile = (pathValue: string) => {
    if (!selected) return;
    const path = pathValue.trim().replace(/^\/+/, '');
    if (!path) return;
    setActionDialog(null);
    void applyOperations([{ action: 'write', path, content: '' }]).then(async () => {
      await refreshDetail();
      const detail = await getSkill(selected.id);
      const file = detail.files.find((item) => item.path === path);
      if (file) void openFile(file);
    });
  };

  const renameFile = () => {
    if (!selected || selected.readonly || !activePath || activePath === 'SKILL.md') return;
    setActionDialog({ kind: 'rename-file', value: activePath });
  };

  const confirmRenameFile = (targetValue: string) => {
    if (!selected || !activePath) return;
    const target = targetValue.trim().replace(/^\/+/, '');
    if (!target || target === activePath) return;
    const previous = activePath;
    setActionDialog(null);
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
    setActionDialog({ kind: 'delete-file' });
  };

  const confirmDeleteFile = () => {
    if (!activePath) return;
    const path = activePath;
    setActionDialog(null);
    void applyOperations([{ action: 'delete', path }]).then(async () => {
      setTabs((items) => items.filter((item) => item !== path));
      setActivePath(null);
      await refreshDetail();
    });
  };

  const nextCloneName = (sourceName: string) => {
    const base = sourceName.replace(/^astra-/, '');
    const used = new Set(skills.map((item) => item.name));
    let candidate = `${base}-copy`;
    let suffix = 2;
    while (used.has(candidate)) {
      candidate = `${base}-copy-${suffix}`;
      suffix += 1;
    }
    return candidate;
  };

  const cloneSelected = () => {
    if (!selected) return;
    const name = nextCloneName(selected.name);
    void perform(async () => {
      const clone = await cloneSkill(selected.id, name);
      await refreshList();
      await selectSkill(clone.id, true);
    });
  };

  const confirmDeleteSkill = () => {
    if (!selected) return;
    const skillId = selected.id;
    setActionDialog(null);
    void perform(async () => {
      await removeSkill(skillId);
      closeDetail();
      await refreshList();
    });
  };

  const runDraftTest = (goal: string, mode: 'standard' | 'trusted') => {
    if (!selected || !goal.trim()) return;
    setActionDialog(null);
    void perform(async () => {
      const run = await testSkillDraft(selected.id, tokenRef.current, goal.trim(), mode);
      setMessage(t('Draft 测试已创建：{id}').replace('{id}', run.run_id));
      onTestRun?.(run.run_id);
    });
  };

  const visibleSkills = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return skills
      .filter((item) => !needle || `${item.name} ${item.description} ${item.origin} ${item.qualified_identity}`.toLowerCase().includes(needle))
      .filter((item) => originFilter === 'all' || item.origin === originFilter)
      .filter((item) => stateFilter === 'all' || (stateFilter === 'enabled' ? item.enabled : !item.enabled))
      .sort((a, b) => sortMode === 'name'
        ? a.name.localeCompare(b.name)
        : new Date(sortMode === 'created' ? b.created_at : b.updated_at).getTime() - new Date(sortMode === 'created' ? a.created_at : a.updated_at).getTime());
  }, [originFilter, query, skills, sortMode, stateFilter]);

  const visibleFiles = useMemo(() => {
    const needle = fileQuery.trim().toLowerCase();
    return (selected?.files ?? []).filter((item) => !needle || item.path.toLowerCase().includes(needle));
  }, [fileQuery, selected?.files]);
  const activeFile = selected?.files.find((item) => item.path === activePath);
  const activeContent = activePath ? buffers[activePath] : undefined;
  const saveStateLabel: Record<SaveState, string> = {
    saved: t('已保存'), dirty: t('未保存'), saving: t('保存中…'), conflict: t('版本冲突'), error: t('保存失败'),
  };
  const lifecycleLabel = (state: string) => ({
    draft: t('草稿'), published: t('已发布'), disabled: t('已停用'), removed: t('已移除'),
  })[state] ?? state;

  const viewRevision = (revision: SkillRevision) => {
    if (!selected) return;
    void perform(async () => {
      const detail = await getSkillRevision(selected.id, revision.id);
      setHistoryRevision(detail);
      setHistoryPath(null);
      setHistoryContent('');
      setHistoryMode('files');
      setHistoryDiff(null);
    });
  };

  const compareRevision = (revision: SkillRevision) => {
    if (!selected) return;
    void perform(async () => {
      const [detail, comparison] = await Promise.all([
        getSkillRevision(selected.id, revision.id),
        getSkillRevisionDiff(selected.id, revision.id),
      ]);
      setHistoryRevision(detail);
      setHistoryPath(null);
      setHistoryContent('');
      setHistoryDiff(comparison);
      setHistoryMode('diff');
    });
  };

  const openHistoryFile = (file: SkillFile) => {
    if (!selected || !historyRevision || !file.text) return;
    setHistoryPath(file.path);
    void getSkillRevisionFile(selected.id, historyRevision.id, file.path)
      .then((result) => setHistoryContent(result.content ?? ''))
      .catch((error) => setMessage(errorMessage(error, t)));
  };

  const card = (skill: SkillSummary) => <button
    className="skill-library-item"
    type="button"
    key={skill.id}
    onClick={() => void selectSkill(skill.id)}
  >
    <span className="skill-library-item-icon">{skill.origin === 'builtin' ? 'A' : 'S'}</span>
    <span className="skill-library-item-copy">
      <span className="skill-library-item-title"><strong>{skill.name}</strong><em className={`origin-${skill.origin}`}>{skill.origin === 'builtin' ? t('Astra 内建') : t('自定义')}</em></span>
      <small>{skill.description}</small>
      <span className="skill-library-item-meta">
        <i className={skill.enabled ? 'enabled' : 'disabled'}>{skill.enabled ? t('已启用') : t('已停用')}</i>
        <span>{skill.active_revision ? `v${skill.active_revision.version}` : t('未发布')}</span>
        <time>{formatDate(skill.updated_at, language)}</time>
      </span>
    </span>
    <span className="skill-library-item-arrow">›</span>
  </button>;

  return <section className="skill-library-page">
    <header className="skill-library-header">
      <div className="skill-library-heading">
        <span className="skill-eyebrow">ASTRA SKILLS</span>
        <h2>{t('Skill 资料库')}</h2>
        <p>{t('浏览、管理与创作可复用的 Agent 工作流')}</p>
      </div>
      <div className="skill-header-actions">
        <button className="skill-button primary" type="button" onClick={openCreateDrawer}>＋ {t('新建 Skill')}</button>
        <label className="skill-button">{t('导入 ZIP')}<input type="file" accept=".zip" onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (!file) return;
          void perform(async () => {
            const imported = await importSkill(file.name, toBase64(await file.arrayBuffer()));
            await refreshList();
            await selectSkill(imported.id);
          });
          event.currentTarget.value = '';
        }} /></label>
        <CloseButton className="settings-close" label={t('关闭 Skill 资料库')} onClick={onClose} />
      </div>
    </header>

    <div className="skill-library-toolbar">
      <label className="skill-search"><span>⌕</span><input aria-label={t('搜索 Skill')} placeholder={t('搜索名称、描述或标识符')} value={query} onChange={(event) => setQuery(event.currentTarget.value)} /></label>
      <select aria-label={t('来源筛选')} value={originFilter} onChange={(event) => setOriginFilter(event.currentTarget.value as OriginFilter)}>
        <option value="all">{t('全部来源')}</option><option value="builtin">{t('Astra 内建')}</option><option value="custom">{t('自定义')}</option>
      </select>
      <select aria-label={t('状态筛选')} value={stateFilter} onChange={(event) => setStateFilter(event.currentTarget.value as StateFilter)}>
        <option value="all">{t('全部状态')}</option><option value="enabled">{t('已启用')}</option><option value="disabled">{t('已停用')}</option>
      </select>
      <select aria-label={t('排序方式')} value={sortMode} onChange={(event) => setSortMode(event.currentTarget.value as SortMode)}>
        <option value="updated">{t('最近更新')}</option><option value="created">{t('最近创建')}</option><option value="name">{t('按名称')}</option>
      </select>
      <div className="skill-view-switch" role="group" aria-label={t('显示方式')}>
        <button type="button" className={displayMode === 'grid' ? 'active' : ''} aria-label={t('网格视图')} aria-pressed={displayMode === 'grid'} onClick={() => setDisplayMode('grid')}>▦</button>
        <button type="button" className={displayMode === 'list' ? 'active' : ''} aria-label={t('列表视图')} aria-pressed={displayMode === 'list'} onClick={() => setDisplayMode('list')}>☷</button>
      </div>
    </div>

    <div className="skill-library-summary"><span>{t('{count} 个 Skill').replace('{count}', String(visibleSkills.length))}</span><span>{t('点击卡片查看详情')}</span></div>
    <div className={`skill-library-collection mode-${displayMode}`}>
      {visibleSkills.map(card)}
      {!visibleSkills.length && <div className="skill-library-empty"><span>⌕</span><strong>{t('没有匹配的 Skill')}</strong><small>{t('调整搜索或筛选条件后重试')}</small></div>}
    </div>

    {createOpen && <div className="skill-create-layer" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) closeCreateDrawer();
    }}>
      <aside className="skill-create-drawer" role="dialog" aria-modal="true" aria-labelledby="skill-create-title">
        <header className="skill-create-titlebar">
          <div className="skill-dialog-icon">S</div>
          <div>
            <span>{t('自定义 Skill')}</span>
            <h3 id="skill-create-title">{t('新建 Skill')}</h3>
          </div>
          <CloseButton label={t('关闭新建 Skill')} onClick={closeCreateDrawer} />
        </header>
        <form className="skill-create-form" onSubmit={(event) => void createNew(event)}>
          <div className="skill-create-intro">
            <strong>{t('创建可复用的工作流')}</strong>
            <p>{t('先填写基本信息。创建后会直接打开编辑器，你可以继续添加指令、脚本与参考资料。')}</p>
          </div>
          <label className="skill-create-field">
            <span>{t('Skill 名称')} <i>{t('必填')}</i></span>
            <input
              autoFocus
              aria-describedby="skill-create-name-help"
              aria-invalid={createName.length > 0 && !createNameValid}
              autoComplete="off"
              maxLength={64}
              placeholder="research-notes"
              spellCheck={false}
              value={createName}
              onChange={(event) => {
                setCreateName(event.currentTarget.value.trim().toLowerCase());
                setCreateError('');
              }}
            />
            <small id="skill-create-name-help" className={createName.length > 0 && !createNameValid ? 'error' : ''}>
              {createName.length > 0 && !createNameValid
                ? t('名称须为 1–64 位小写字母、数字或单连字符')
                : t('使用小写字母、数字和单连字符')}
            </small>
          </label>
          <div className="skill-create-identity">
            <span>{t('标识符预览')}</span>
            <code>custom:{createName || 'skill-name'}</code>
          </div>
          <label className="skill-create-field description">
            <span>{t('Skill 描述')} <i>{t('必填')}</i></span>
            <textarea
              maxLength={1024}
              placeholder={t('说明这个 Skill 解决什么问题，以及何时应该使用它')}
              rows={6}
              value={createDescription}
              onChange={(event) => {
                setCreateDescription(event.currentTarget.value);
                setCreateError('');
              }}
            />
            <small>{createDescription.length} / 1024</small>
          </label>
          {createError && <div className="skill-create-error" role="alert">{createError}</div>}
          <footer className="skill-create-actions">
            <button className="skill-button" type="button" disabled={createSubmitting} onClick={closeCreateDrawer}>{t('取消')}</button>
            <button className="skill-button primary" type="submit" disabled={!canCreate}>
              {createSubmitting ? t('正在创建…') : t('创建并编辑')}
            </button>
          </footer>
        </form>
      </aside>
    </div>}

    {selected && !editorOpen && <div className="skill-modal-layer" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) closeDetail();
    }}>
      <section className="skill-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="skill-detail-title">
        <header className="skill-dialog-titlebar">
          <span className="skill-dialog-icon">{selected.origin === 'builtin' ? 'A' : 'S'}</span>
          <div><span>{selected.origin === 'builtin' ? t('Astra 内建 Skill') : t('自定义 Skill')}</span><h3 id="skill-detail-title">{selected.name}</h3></div>
          <CloseButton label={t('关闭详情')} onClick={closeDetail} />
        </header>
        <div className="skill-detail-body">
          <div className="skill-detail-lead"><p>{selected.description}</p><span className={`skill-state-pill ${selected.enabled ? 'enabled' : 'disabled'}`}>{selected.enabled ? t('已启用') : t('已停用')}</span></div>
          <div className="skill-detail-actions">
            <button className="skill-button primary" type="button" onClick={() => setEditorOpen(true)}>{selected.readonly ? t('查看文件') : t('编辑')}</button>
            {selected.origin === 'builtin' && <button className="skill-button" type="button" disabled={busy} title={t('将创建 {name}').replace('{name}', nextCloneName(selected.name))} onClick={cloneSelected}>{t('克隆')}</button>}
            <button className="skill-button" type="button" onClick={() => void perform(async () => {
              await setSkillEnabled(selected.id, !selected.enabled);
              await refreshDetail();
            })}>{t(selected.enabled ? '停用' : '启用')}</button>
            <a className="skill-button" href={`/api/skills/${selected.id}/export`}>{t('导出')}</a>
            {!selected.readonly && <button className="skill-button danger" type="button" onClick={() => setActionDialog({ kind: 'delete-skill' })}>{t('删除')}</button>}
          </div>
          <section className="skill-detail-section">
            <h4>{t('基本信息')}</h4>
            <dl className="skill-metadata-grid">
              <div><dt>{t('名称')}</dt><dd>{selected.name}</dd></div>
              <div><dt>{t('标识符')}</dt><dd><code>{selected.qualified_identity}</code></dd></div>
              <div><dt>{t('创建时间')}</dt><dd>{formatDate(selected.created_at, language)}</dd></div>
              <div><dt>{t('更新时间')}</dt><dd>{formatDate(selected.updated_at, language)}</dd></div>
              <div><dt>{t('当前版本')}</dt><dd>{selected.active_revision ? `v${selected.active_revision.version}` : t('未发布')}</dd></div>
              <div><dt>{t('生命周期')}</dt><dd>{lifecycleLabel(selected.lifecycle_state)}</dd></div>
            </dl>
          </section>
          <section className="skill-detail-section">
            <h4>{t('元数据')}</h4>
            <dl className="skill-metadata-grid">
              <div><dt>{t('兼容性')}</dt><dd>{selected.compatibility || t('无兼容性声明')}</dd></div>
              <div><dt>{t('文件')}</dt><dd>{t('{count} 个文件').replace('{count}', String(selected.files.length))}</dd></div>
              <div className="wide"><dt>{t('请求能力')}</dt><dd>{selected.requested_tool_patterns.length ? selected.requested_tool_patterns.map((item) => <code key={item}>{item}</code>) : t('未请求工具能力')}</dd></div>
              <div className="wide"><dt>{t('内容摘要')}</dt><dd><code>{selected.active_revision?.digest ?? '—'}</code></dd></div>
            </dl>
          </section>
        </div>
      </section>
    </div>}

    {selected && editorOpen && <div className="skill-modal-layer editor-layer">
      <section className="skill-editor-dialog" role="dialog" aria-modal="true" aria-labelledby="skill-editor-title">
        <header className="skill-editor-titlebar">
          <div><span className="skill-dialog-icon">{selected.origin === 'builtin' ? 'A' : 'S'}</span><strong id="skill-editor-title">{selected.name}</strong><small>{selected.readonly ? t('只读') : saveStateLabel[saveState]}</small></div>
          <div className="skill-editor-window-actions">
            <button type="button" onClick={() => { setEditorOpen(false); resetEditor(); }}>{t('返回详情')}</button>
            <CloseButton label={t('关闭编辑器')} onClick={closeDetail} />
          </div>
        </header>
        <div className="skill-editor-shell">
          <aside className="skill-explorer">
            <header>
              <strong>{t('资源管理器')}</strong>
              <span>{t('{count} 个文件').replace('{count}', String(selected.files.length))}</span>
            </header>
            <div className="skill-explorer-tools">
              <button type="button" disabled={selected.readonly} title={t('新建文件')} aria-label={t('新建文件')} onClick={createFile}><span aria-hidden="true">＋</span>{t('文件')}</button>
              <button type="button" disabled={selected.readonly} title={t('新建文件夹')} aria-label={t('新建文件夹')} onClick={createFolder}><span aria-hidden="true">＋</span>{t('文件夹')}</button>
              <button type="button" disabled={selected.readonly || !activePath || activePath === 'SKILL.md'} title={t('重命名')} aria-label={t('重命名')} onClick={renameFile}><span aria-hidden="true">✎</span>{t('重命名')}</button>
              <button className="danger" type="button" disabled={selected.readonly || !activePath || activePath === 'SKILL.md'} title={t('删除')} aria-label={t('删除')} onClick={deleteFile}><span aria-hidden="true">⌫</span>{t('删除')}</button>
            </div>
            <label className="skill-file-search"><span>⌕</span><input aria-label={t('搜索 Skill 文件')} placeholder={t('筛选文件')} value={fileQuery} onChange={(event) => setFileQuery(event.currentTarget.value)} /></label>
            <SkillTree files={visibleFiles} activePath={activePath} selectedFolder={selectedFolder} virtualFolders={virtualFolders} onOpenFile={(file) => void openFile(file)} onSelectFolder={setSelectedFolder} readonly={selected.readonly} />
            <div className="skill-explorer-meta"><span>{selected.qualified_identity}</span><span>{selected.compatibility || t('无兼容性声明')}</span></div>
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
              <button type="button" onClick={() => void perform(async () => {
                await setSkillEnabled(selected.id, !selected.enabled);
                await refreshDetail();
              })}>{t(selected.enabled ? '停用' : '启用')}</button>
              <a href={`/api/skills/${selected.id}/export`}>{t('导出')}</a>
              {!selected.readonly && <button type="button" onClick={() => setActionDialog({ kind: 'test-draft', value: '', mode: 'standard' })}>{t('测试 Draft')}</button>}
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
            {!activeFile ? <div className="skill-editor-empty"><strong>{t('打开一个文件开始编辑')}</strong><span>{t('从左侧资源管理器选择文件，或创建新的文件与文件夹。')}</span></div>
              : !activeFile.text ? <div className="skill-editor-empty">{t('二进制资源不在浏览器中解码或执行。')}</div>
                : <div className={`skill-editor-grid ${activePath === 'SKILL.md' && preview ? 'with-preview' : ''}`}>
                  <Editor
                    theme={darkTheme ? 'vs-dark' : 'light'}
                    height="100%"
                    path={editorModelPath(selected.id, activeFile.path)}
                    language={editorLanguage(activeFile.path)}
                    value={activeContent ?? ''}
                    onChange={changeContent}
                    onMount={(_, monaco) => { monacoRef.current = monaco; }}
                    options={{ readOnly: selected.readonly, minimap: { enabled: false }, wordWrap: activePath === 'SKILL.md' ? 'on' : 'off', automaticLayout: true, fontSize: 13, tabSize: 2, renderValidationDecorations: 'on', accessibilitySupport: 'auto', scrollBeyondLastLine: false }}
                  />
                  {activePath === 'SKILL.md' && preview && <article className="skill-markdown-preview"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{markdownBody(activeContent ?? '')}</ReactMarkdown></article>}
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
                  ? diagnostics.map((item, index) => <button type="button" key={`${item.code}-${index}`} onClick={() => {
                    const file = item.path ? selected.files.find((entry) => entry.path === item.path) : undefined;
                    if (file) void openFile(file);
                  }}><b>{item.severity}</b><span>{item.path ?? 'package'}{item.line ? `:${item.line}` : ''}</span>{item.message}</button>)
                  : <span>{t('没有诊断问题')}</span>)}
                {inspector === 'history' && (revisions.length
                  ? revisions.map((revision) => <div className="skill-history-row" key={revision.id}>
                    <span>v{revision.version}</span><code>{revision.digest.slice(0, 20)}…</code><time>{formatDate(revision.published_at, language)}</time>
                    <button type="button" onClick={() => viewRevision(revision)}>{t('查看')}</button>
                    <button type="button" onClick={() => compareRevision(revision)}>{t('比较')}</button>
                    {!selected.readonly && <button type="button" onClick={() => void perform(async () => { await restoreSkillRevision(selected.id, revision.id); await refreshDetail(); })}>{t('恢复到 Draft')}</button>}
                  </div>)
                  : <span>{t('尚无已发布 Revision')}</span>)}
                {inspector === 'diff' && (diff.length
                  ? diff.map((item) => <details key={item.path}><summary><b>{item.status}</b> {item.path}</summary>{item.patch && <pre>{item.patch}</pre>}</details>)
                  : <span>{t('没有差异')}</span>)}
              </div>
            </section>
            {(message || busy) && <div className="skill-toast" role="status">{busy ? t('处理中…') : message}</div>}
          </main>
        </div>
      </section>
    </div>}

    {selected && historyRevision && <div className="skill-modal-layer history-layer">
      <section className="skill-history-dialog" role="dialog" aria-modal="true" aria-labelledby="skill-history-title">
        <header className="skill-editor-titlebar">
          <div><span className="skill-dialog-icon">↶</span><h3 id="skill-history-title">{t('历史 Revision')} v{historyRevision.version}</h3><small>{formatDate(historyRevision.published_at, language)}</small></div>
          <div className="skill-editor-window-actions">
            <button className={historyMode === 'files' ? 'active' : ''} type="button" onClick={() => setHistoryMode('files')}>{t('浏览文件')}</button>
            <button className={historyMode === 'diff' ? 'active' : ''} type="button" onClick={() => {
              if (historyDiff) {
                setHistoryMode('diff');
                return;
              }
              void getSkillRevisionDiff(selected.id, historyRevision.id).then((result) => {
                setHistoryDiff(result);
                setHistoryMode('diff');
              }).catch((error) => setMessage(errorMessage(error, t)));
            }}>{t('与当前版本对比')}</button>
            <a className="skill-button" href={`/api/skills/${selected.id}/export?revision_id=${historyRevision.id}`}>{t('导出此版本')}</a>
            <CloseButton label={t('关闭历史版本')} onClick={() => setHistoryRevision(null)} />
          </div>
        </header>
        <div className={`skill-history-shell ${historyMode === 'diff' ? 'show-diff' : ''}`}>
          <aside className="skill-explorer history"><header><strong>{t('历史文件')}</strong><span>v{historyRevision.version}</span></header><SkillTree files={historyRevision.files} activePath={historyPath} selectedFolder="" onOpenFile={openHistoryFile} onSelectFolder={() => undefined} readonly /></aside>
          {historyMode === 'files' ? <main className="skill-history-viewer">
            <div className="skill-history-meta"><code>{historyRevision.digest}</code><span>{historyRevision.files.length} {t('文件')}</span></div>
            {historyPath ? <Editor theme={darkTheme ? 'vs-dark' : 'light'} height="100%" path={`history-${historyRevision.id}-${historyPath}`} language={editorLanguage(historyPath)} value={historyContent} options={{ readOnly: true, minimap: { enabled: false }, automaticLayout: true, scrollBeyondLastLine: false }} />
              : <div className="skill-editor-empty"><strong>{t('选择一个历史文件')}</strong><span>{t('此版本为不可变只读快照，不会影响当前 Draft。')}</span></div>}
          </main> : <main className="skill-git-diff">
            <header>
              <div><strong>{t('Git 差异')}</strong><span>{historyDiff ? `v${historyDiff.base_version} → v${historyDiff.target_version}` : ''}</span></div>
              <span>{historyDiff ? t('{count} 个变更文件').replace('{count}', String(historyDiff.files.length)) : t('正在生成差异…')}</span>
            </header>
            {historyDiff?.patch ? <pre aria-label={t('Git 差异')}>
              {historyDiff.patch.split('\n').map((line, index) => <span
                className={line.startsWith('+') && !line.startsWith('+++') ? 'addition'
                  : line.startsWith('-') && !line.startsWith('---') ? 'deletion'
                    : line.startsWith('@@') ? 'hunk'
                      : line.startsWith('diff --git') ? 'file-header' : 'context'}
                key={`${index}-${line}`}
              >{line || ' '}{'\n'}</span>)}
            </pre> : <div className="skill-editor-empty"><strong>{t('与当前版本无差异')}</strong><span>{t('该历史 Revision 与当前已发布版本内容一致。')}</span></div>}
          </main>}
        </div>
      </section>
    </div>}
    {selected && actionDialog && <div className="skill-action-layer" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) setActionDialog(null);
    }}>
      <form
        className={`skill-action-dialog ${actionDialog.kind.startsWith('delete-') ? 'danger' : ''}`}
        role={actionDialog.kind.startsWith('delete-') ? 'alertdialog' : 'dialog'}
        aria-modal="true"
        aria-labelledby="skill-action-title"
        onSubmit={(event) => {
          event.preventDefault();
          if (actionDialog.kind === 'new-folder') confirmCreateFolder(actionDialog.value);
          if (actionDialog.kind === 'new-file') confirmCreateFile(actionDialog.value);
          if (actionDialog.kind === 'rename-file') confirmRenameFile(actionDialog.value);
          if (actionDialog.kind === 'delete-file') confirmDeleteFile();
          if (actionDialog.kind === 'delete-skill') confirmDeleteSkill();
          if (actionDialog.kind === 'test-draft') runDraftTest(actionDialog.value, actionDialog.mode);
        }}
      >
        <header>
          <span className="skill-action-icon">{actionDialog.kind.startsWith('delete-') ? '!' : actionDialog.kind === 'test-draft' ? '▷' : '›_'}</span>
          <div>
            <small>{t(actionDialog.kind === 'test-draft' ? 'Draft 工具' : actionDialog.kind.startsWith('delete-') ? '谨慎操作' : '资源管理器')}</small>
            <h3 id="skill-action-title">{t(
              actionDialog.kind === 'new-file' ? '新建文件'
                : actionDialog.kind === 'new-folder' ? '新建文件夹'
                  : actionDialog.kind === 'rename-file' ? '重命名'
                    : actionDialog.kind === 'delete-file' ? '删除文件？'
                      : actionDialog.kind === 'delete-skill' ? '删除 Skill？'
                        : '测试 Draft',
            )}</h3>
          </div>
          <CloseButton label={t('关闭')} onClick={() => { if (!busy) setActionDialog(null); }} />
        </header>
        <div className="skill-action-body">
          {(actionDialog.kind === 'new-file' || actionDialog.kind === 'new-folder' || actionDialog.kind === 'rename-file') && <label>
            <span>{t(actionDialog.kind === 'new-folder' ? '文件夹路径' : '文件路径')}</span>
            <input
              autoFocus
              spellCheck={false}
              value={actionDialog.value}
              placeholder={actionDialog.kind === 'new-folder' ? 'references' : 'references/guide.md'}
              onChange={(event) => setActionDialog({ ...actionDialog, value: event.currentTarget.value })}
            />
            <small>{t('使用相对于 Skill 根目录的路径')}</small>
          </label>}
          {actionDialog.kind === 'delete-file' && <>
            <p>{t('即将删除文件 {path}。此修改会写入当前 Draft。').replace('{path}', activePath ?? '')}</p>
            <code>{activePath}</code>
          </>}
          {actionDialog.kind === 'delete-skill' && <>
            <p>{t('将移除自定义 Skill {name}。历史 Revision 仍保留用于审计。').replace('{name}', selected.name)}</p>
            <code>{selected.qualified_identity}</code>
          </>}
          {actionDialog.kind === 'test-draft' && <>
            <label>
              <span>{t('测试目标')}</span>
              <textarea autoFocus rows={4} value={actionDialog.value} placeholder={t('描述希望这个 Skill 完成的任务')} onChange={(event) => setActionDialog({ ...actionDialog, value: event.currentTarget.value })} />
            </label>
            <fieldset>
              <legend>{t('执行模式')}</legend>
              <button type="button" aria-pressed={actionDialog.mode === 'standard'} onClick={() => setActionDialog({ ...actionDialog, mode: 'standard' })}><strong>{t('快速模式')}</strong><small>{t('使用标准隔离与审批策略')}</small></button>
              <button type="button" aria-pressed={actionDialog.mode === 'trusted'} onClick={() => setActionDialog({ ...actionDialog, mode: 'trusted' })}><strong>{t('可信模式')}</strong><small>{t('使用可信执行策略进行完整验证')}</small></button>
            </fieldset>
          </>}
        </div>
        <footer>
          <button className="skill-button" type="button" disabled={busy} onClick={() => setActionDialog(null)}>{t('取消')}</button>
          <button
            className={`skill-button ${actionDialog.kind.startsWith('delete-') ? 'danger-solid' : 'primary'}`}
            type="submit"
            disabled={busy || ('value' in actionDialog && !actionDialog.value.trim())}
          >
            {t(actionDialog.kind === 'delete-file' ? '确认删除文件'
              : actionDialog.kind === 'delete-skill' ? '确认删除 Skill'
                : actionDialog.kind === 'test-draft' ? '开始测试'
                  : actionDialog.kind === 'rename-file' ? '保存路径'
                    : '创建')}
          </button>
        </footer>
      </form>
    </div>}
    {message && !editorOpen && <div className="skill-toast library-toast" role="status">{message}</div>}
  </section>;
}
