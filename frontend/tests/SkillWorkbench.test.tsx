import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SkillWorkbench } from '../src/SkillWorkbench';
import { I18nProvider } from '../src/i18n';
import * as api from '../src/api';

vi.mock('@monaco-editor/react', () => ({
  default: ({ path, value, onChange, options }: { path: string; value: string; onChange: (value: string) => void; options: { readOnly: boolean } }) => (
    <textarea aria-label="Monaco Skill editor" data-model-path={path} value={value} readOnly={options.readOnly} onChange={(event) => onChange(event.currentTarget.value)} />
  ),
}));

vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api')>();
  return {
    ...actual,
    listSkills: vi.fn(),
    getSkill: vi.fn(),
    getSkillFile: vi.fn(),
    updateSkillFiles: vi.fn(),
    validateSkill: vi.fn(),
    publishSkill: vi.fn(),
    listSkillRevisions: vi.fn(async () => []),
    getSkillDiff: vi.fn(async () => ({ files: [] })),
    getSkillRevision: vi.fn(),
    getSkillRevisionFile: vi.fn(),
    getSkillRevisionDiff: vi.fn(),
    setSkillEnabled: vi.fn(),
    createSkill: vi.fn(),
    importSkill: vi.fn(),
    cloneSkill: vi.fn(),
    removeSkill: vi.fn(),
    restoreSkillRevision: vi.fn(),
    testSkillDraft: vi.fn(),
  };
});

const custom = {
  id: 'skill-1',
  name: 'research-notes',
  qualified_identity: 'custom:research-notes',
  origin: 'custom' as const,
  description: 'Collect notes',
  enabled: true,
  readonly: false,
  lifecycle_state: 'published',
  active_revision: { id: 'revision-1', version: 1, digest: 'sha256:one', test_only: false, diagnostics: [] },
  draft_revision_token: 'token-1',
  diagnostics: [],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  compatibility: 'Astra 0.1+',
  requested_tool_patterns: ['catalog_search'],
  files: [{
    path: 'SKILL.md', uri: 'skill-draft://skill-1/token-1/SKILL.md',
    digest: 'sha256:file', size_bytes: 10, media_type: 'text/markdown',
    kind: 'instructions', text: true, readonly: false,
  }],
};

function renderWorkbench() {
  return render(<I18nProvider><SkillWorkbench onClose={() => undefined} /></I18nProvider>);
}

async function openCustomEditor() {
  fireEvent.click(await screen.findByRole('button', { name: /research-notes/ }));
  fireEvent.click(await screen.findByRole('button', { name: '编辑' }));
}

describe('SkillWorkbench', () => {
  afterEach(cleanup);

  beforeEach(() => {
    const values = new Map<string, string>([['astra.language', 'zh-CN']]);
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
        removeItem: (key: string) => values.delete(key),
        clear: () => values.clear(),
      },
    });
    vi.clearAllMocks();
    vi.mocked(api.listSkills).mockResolvedValue([custom]);
    vi.mocked(api.getSkill).mockResolvedValue(custom);
    vi.mocked(api.getSkillFile).mockResolvedValue({
      ...custom.files[0],
      content: '---\nname: research-notes\ndescription: Collect notes\n---\n\n# Workflow',
    });
    vi.mocked(api.updateSkillFiles).mockResolvedValue({
      skill_id: custom.id,
      revision_token: 'token-2',
      readonly: false,
      files: custom.files,
      diagnostics: [],
    });
    vi.mocked(api.validateSkill).mockResolvedValue({
      valid: true, publishable: true, digest: 'sha256:two', diagnostics: [],
    });
    vi.mocked(api.publishSkill).mockResolvedValue({
      id: 'revision-2', version: 2, digest: 'sha256:two', test_only: false, diagnostics: [],
    });
  });

  it('lists shared Skills and autosaves a virtual Monaco model', async () => {
    vi.mocked(api.updateSkillFiles).mockResolvedValueOnce({
      skill_id: custom.id,
      revision_token: 'token-2',
      readonly: false,
      files: [{ ...custom.files[0], uri: 'skill-draft://skill-1/token-2/SKILL.md' }],
      diagnostics: [],
    });
    renderWorkbench();
    await openCustomEditor();
    fireEvent.click(await screen.findByRole('treeitem', { name: /SKILL\.md/ }));
    const editor = await screen.findByLabelText('Monaco Skill editor');
    expect(editor).toHaveAttribute('data-model-path', 'skill-editor://skill-1/SKILL.md');
    fireEvent.change(editor, { target: { value: '# Updated workflow' } });
    await waitFor(() => expect(api.updateSkillFiles).toHaveBeenCalledWith(
      custom.id,
      'token-1',
      [{ action: 'write', path: 'SKILL.md', content: '# Updated workflow' }],
    ), { timeout: 1500 });
    expect(await screen.findByText('草稿已保存')).toBeInTheDocument();
    expect(screen.getByLabelText('Monaco Skill editor')).toHaveAttribute('data-model-path', 'skill-editor://skill-1/SKILL.md');
  });

  it('validates and publishes the current immutable revision', async () => {
    renderWorkbench();
    await openCustomEditor();
    fireEvent.click(screen.getByRole('button', { name: '校验' }));
    expect(await screen.findByText('校验通过，可以发布')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '发布' }));
    await waitFor(() => expect(api.publishSkill).toHaveBeenCalledWith('skill-1', 'token-1'));
  });

  it('shows a three-way comparison and retries against the latest Draft token', async () => {
    renderWorkbench();
    await openCustomEditor();
    fireEvent.click(await screen.findByRole('treeitem', { name: /SKILL\.md/ }));
    const editor = await screen.findByLabelText('Monaco Skill editor');
    vi.mocked(api.updateSkillFiles).mockRejectedValueOnce(new api.AstraApiError({
      type: 'state.conflict',
      code: 'SKILL_DRAFT_STALE',
      message: 'stale',
      retryable: false,
      trace_id: 'req-test',
      details: {},
    }));
    vi.mocked(api.getSkillFile).mockResolvedValueOnce({
      ...custom.files[0],
      content: '# Remote workflow',
    });
    vi.mocked(api.getSkill).mockResolvedValueOnce({
      ...custom,
      draft_revision_token: 'token-remote',
    });
    fireEvent.change(editor, { target: { value: '# Local workflow' } });
    expect(await screen.findByRole('region', { name: '三方版本比较' }, { timeout: 1500 })).toBeInTheDocument();
    expect(screen.getAllByText('# Local workflow')).toHaveLength(2);
    expect(screen.getByText('# Remote workflow')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '保留本地并重试' }));
    await waitFor(() => expect(api.updateSkillFiles).toHaveBeenLastCalledWith(
      custom.id,
      'token-remote',
      [{ action: 'write', path: 'SKILL.md', content: '# Local workflow' }],
    ));
  });

  it('renders built-in Skills as read-only and offers cloning', async () => {
    const builtin = {
      ...custom,
      id: 'builtin-1',
      name: 'astra-skill-authoring',
      qualified_identity: 'builtin:astra-skill-authoring',
      origin: 'builtin' as const,
      readonly: true,
      draft_revision_token: null,
      files: [{ ...custom.files[0], readonly: true, uri: 'skill-revision://builtin-1/revision-1/SKILL.md' }],
    };
    vi.mocked(api.listSkills).mockResolvedValue([builtin]);
    vi.mocked(api.getSkill).mockResolvedValue(builtin);
    vi.mocked(api.cloneSkill).mockResolvedValue({
      ...custom,
      id: 'clone-1',
      name: 'skill-authoring-copy',
      qualified_identity: 'custom:skill-authoring-copy',
    });
    renderWorkbench();
    fireEvent.click(await screen.findByRole('button', { name: /astra-skill-authoring/ }));
    await screen.findByText('Astra 内建 Skill');
    expect(screen.queryByRole('button', { name: '发布' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看文件' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '克隆' }));
    await waitFor(() => expect(api.cloneSkill).toHaveBeenCalledWith('builtin-1', 'skill-authoring-copy'));
  });

  it('creates a Skill from the themed drawer and opens its editor', async () => {
    const created = {
      ...custom,
      id: 'skill-new',
      name: 'daily-research',
      qualified_identity: 'custom:daily-research',
      description: 'Collect a daily research digest',
    };
    vi.mocked(api.createSkill).mockResolvedValue(created);
    vi.mocked(api.getSkill).mockResolvedValueOnce(created);
    renderWorkbench();

    fireEvent.click(await screen.findByRole('button', { name: '＋ 新建 Skill' }));
    const dialog = screen.getByRole('dialog', { name: '新建 Skill' });
    expect(dialog).toBeInTheDocument();

    const submit = screen.getByRole('button', { name: '创建并编辑' });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: /Skill 名称/ }), { target: { value: 'Daily Research' } });
    expect(screen.getByText('名称须为 1–64 位小写字母、数字或单连字符')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: /Skill 名称/ }), { target: { value: 'daily-research' } });
    fireEvent.change(screen.getByRole('textbox', { name: /Skill 描述/ }), { target: { value: 'Collect a daily research digest' } });
    fireEvent.click(submit);

    await waitFor(() => expect(api.createSkill).toHaveBeenCalledWith('daily-research', 'Collect a daily research digest'));
    expect(await screen.findByRole('button', { name: '发布' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: '新建 Skill' })).not.toBeInTheDocument();
  });

  it('uses themed editor dialogs for file operations instead of native prompts', async () => {
    renderWorkbench();
    await openCustomEditor();

    fireEvent.click(screen.getByRole('button', { name: '新建文件' }));
    const dialog = screen.getByRole('dialog', { name: '新建文件' });
    expect(dialog).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: /文件路径/ }), { target: { value: 'references/guide.md' } });
    fireEvent.click(screen.getByRole('button', { name: '创建' }));

    await waitFor(() => expect(api.updateSkillFiles).toHaveBeenCalledWith(
      custom.id,
      'token-1',
      [{ action: 'write', path: 'references/guide.md', content: '' }],
    ));
    expect(screen.queryByRole('dialog', { name: '新建文件' })).not.toBeInTheDocument();
  });

  it('configures a Draft test in-app with an explicit execution mode', async () => {
    vi.mocked(api.testSkillDraft).mockResolvedValue({
      run_id: 'run-1',
      task_id: 'task-1',
      status: 'queued',
      answer_mode: 'trusted',
    });
    renderWorkbench();
    await openCustomEditor();

    fireEvent.click(screen.getByRole('button', { name: '测试 Draft' }));
    expect(screen.getByRole('dialog', { name: '测试 Draft' })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: /测试目标/ }), { target: { value: 'Collect sources' } });
    fireEvent.click(screen.getByRole('button', { name: /可信模式/ }));
    fireEvent.click(screen.getByRole('button', { name: '开始测试' }));

    await waitFor(() => expect(api.testSkillDraft).toHaveBeenCalledWith(custom.id, 'token-1', 'Collect sources', 'trusted'));
  });

  it('renders the complete workbench chrome in English', async () => {
    window.localStorage.setItem('astra.language', 'en');
    renderWorkbench();
    expect(await screen.findByRole('heading', { name: 'Skill Library' })).toBeInTheDocument();
    expect(screen.getByLabelText('Search skills')).toBeInTheDocument();
    expect(screen.getByLabelText('Grid view')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('Grid view')).toHaveTextContent('Grid');
    fireEvent.click(screen.getByLabelText('List view'));
    expect(screen.getByLabelText('List view')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('Sort by').closest('label')).toHaveTextContent('Sort');
    fireEvent.click(await screen.findByRole('button', { name: /research-notes/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    expect(screen.getByRole('button', { name: 'Validate' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Publish' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '0 issues' })).toBeInTheDocument();
    expect(screen.queryByText('选择或创建一个 Skill')).not.toBeInTheDocument();
  });

  it('opens the editor as a full-viewport workspace with accessible vector icons', async () => {
    const { container } = renderWorkbench();
    await openCustomEditor();

    expect(container.querySelector('.editor-layer .skill-editor-dialog')).toBeInTheDocument();
    expect(container.querySelector('.skill-tree-icon svg')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新建文件' }).querySelector('svg')).toBeInTheDocument();
    expect(screen.getByLabelText('搜索 Skill 文件').previousElementSibling).toMatchObject({ tagName: 'svg' });
  });

  it('opens a published revision as a read-only historical file tree', async () => {
    const revision = {
      id: 'revision-1',
      version: 1,
      digest: 'sha256:one',
      published_at: '2026-01-01T00:00:00Z',
      revoked_at: null,
      test_only: false,
      diagnostics: [],
    };
    vi.mocked(api.listSkillRevisions).mockResolvedValue([revision]);
    vi.mocked(api.getSkillRevision).mockResolvedValue({ ...revision, files: custom.files.map((file) => ({ ...file, readonly: true })) });
    vi.mocked(api.getSkillRevisionFile).mockResolvedValue({
      ...custom.files[0],
      readonly: true,
      content: '# Historical workflow',
    });
    vi.mocked(api.getSkillRevisionDiff).mockResolvedValue({
      skill_id: custom.id,
      base_revision_id: revision.id,
      target_revision_id: 'revision-2',
      base_version: 1,
      target_version: 2,
      patch: 'diff --git a/SKILL.md b/SKILL.md\n--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-Old\n+New\n',
      files: [{ path: 'SKILL.md', status: 'modified', patch: '@@ -1 +1 @@\n-Old\n+New\n' }],
    });
    renderWorkbench();
    await openCustomEditor();
    fireEvent.click(screen.getByRole('button', { name: '历史' }));
    fireEvent.click(await screen.findByRole('button', { name: '查看' }));
    expect(await screen.findByRole('heading', { name: /历史 Revision v1/ })).toBeInTheDocument();
    const historicalFiles = screen.getAllByRole('treeitem', { name: /SKILL\.md/ });
    fireEvent.click(historicalFiles[historicalFiles.length - 1]);
    expect(await screen.findByDisplayValue('# Historical workflow')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '与当前版本对比' }));
    expect(await screen.findByLabelText('Git 差异')).toHaveTextContent('diff --git a/SKILL.md b/SKILL.md');
  });
});
