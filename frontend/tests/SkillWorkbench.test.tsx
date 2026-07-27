import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SkillWorkbench } from '../src/SkillWorkbench';
import { I18nProvider } from '../src/i18n';
import * as api from '../src/api';

vi.mock('@monaco-editor/react', () => ({
  default: ({ value, onChange, options }: { value: string; onChange: (value: string) => void; options: { readOnly: boolean } }) => (
    <textarea aria-label="Monaco Skill editor" value={value} readOnly={options.readOnly} onChange={(event) => onChange(event.currentTarget.value)} />
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
  requested_tool_patterns: ['web_search'],
  files: [{
    path: 'SKILL.md', uri: 'skill-draft://skill-1/token-1/SKILL.md',
    digest: 'sha256:file', size_bytes: 10, media_type: 'text/markdown',
    kind: 'instructions', text: true, readonly: false,
  }],
};

function renderWorkbench() {
  return render(<I18nProvider><SkillWorkbench onClose={() => undefined} /></I18nProvider>);
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
    renderWorkbench();
    await screen.findAllByText('research-notes');
    fireEvent.click(await screen.findByRole('treeitem', { name: /SKILL\.md/ }));
    const editor = await screen.findByLabelText('Monaco Skill editor');
    fireEvent.change(editor, { target: { value: '# Updated workflow' } });
    await waitFor(() => expect(api.updateSkillFiles).toHaveBeenCalledWith(
      custom.id,
      'token-1',
      [{ action: 'write', path: 'SKILL.md', content: '# Updated workflow' }],
    ), { timeout: 1500 });
    expect(await screen.findByText('草稿已保存')).toBeInTheDocument();
  });

  it('validates and publishes the current immutable revision', async () => {
    renderWorkbench();
    await screen.findByText('research-notes');
    fireEvent.click(screen.getByRole('button', { name: '校验' }));
    expect(await screen.findByText('校验通过，可以发布')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '发布' }));
    await waitFor(() => expect(api.publishSkill).toHaveBeenCalledWith('skill-1', 'token-1'));
  });

  it('shows a three-way comparison and retries against the latest Draft token', async () => {
    renderWorkbench();
    await screen.findByText('research-notes');
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
    renderWorkbench();
    await screen.findByText('Astra 内建');
    expect(screen.queryByRole('button', { name: '发布' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '克隆' })).toBeInTheDocument();
  });

  it('renders the complete workbench chrome in English', async () => {
    window.localStorage.setItem('astra.language', 'en');
    renderWorkbench();
    expect(await screen.findByRole('heading', { name: 'Skill Library' })).toBeInTheDocument();
    expect(screen.getByLabelText('Search skills')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Validate' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Publish' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '0 issues' })).toBeInTheDocument();
    expect(screen.queryByText('选择或创建一个 Skill')).not.toBeInTheDocument();
  });
});
