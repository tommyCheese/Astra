import { describe, expect, it } from 'vitest';
import type { SkillSummary } from '../src/api';
import {
  detectSlashSkillCommand,
  filterSkillCommandOptions,
  filterSlashCommandOptions,
  normalizeSelectedSkillIds,
} from '../src/composerSkills';

function skill(overrides: Partial<SkillSummary> = {}): SkillSummary {
  return {
    id: 'skill-1',
    name: 'hello-astra',
    qualified_identity: 'custom:hello-astra',
    origin: 'custom',
    description: '用于打招呼和介绍自己',
    enabled: true,
    readonly: false,
    lifecycle_state: 'published',
    active_revision: {
      id: 'revision-1',
      version: 1,
      digest: 'sha256:test',
      published_at: '2026-07-27T00:00:00Z',
      revoked_at: null,
      test_only: false,
      diagnostics: [],
    },
    diagnostics: [],
    created_at: '2026-07-27T00:00:00Z',
    updated_at: '2026-07-27T00:00:00Z',
    ...overrides,
  };
}

describe('detectSlashSkillCommand', () => {
  it('detects commands at the start or after whitespace and includes the whole token range', () => {
    expect(detectSlashSkillCommand('/hel suffix', 4)).toEqual({ start: 0, end: 4, query: 'hel' });
    expect(detectSlashSkillCommand('请使用 /hello-astra 完成', 12)).toEqual({
      start: 4,
      end: 16,
      query: 'hello-a',
    });
  });

  it('does not treat URLs, nested paths, word-internal slashes, selections, or IME text as commands', () => {
    expect(detectSlashSkillCommand('https://example.com', 8)).toBeNull();
    expect(detectSlashSkillCommand('/tmp/file', 9)).toBeNull();
    expect(detectSlashSkillCommand('foo/bar', 7)).toBeNull();
    expect(detectSlashSkillCommand('/hello', 1, 4)).toBeNull();
    expect(detectSlashSkillCommand('/hello', 6, 6, true)).toBeNull();
  });
});

describe('filterSkillCommandOptions', () => {
  it('filters eligible Skills across metadata, orders them, and annotates selected state', () => {
    const builtin = skill({
      id: 'skill-2',
      name: 'astra-authoring',
      qualified_identity: 'builtin:astra-authoring',
      origin: 'builtin',
      description: 'Author Agent Skills',
    });
    const disabled = skill({ id: 'skill-3', name: 'hidden', qualified_identity: 'custom:hidden', enabled: false });
    expect(filterSkillCommandOptions([skill(), disabled, builtin], 'astra', ['custom:hello-astra']))
      .toEqual([
        { skill: builtin, selected: false },
        { skill: skill(), selected: true },
      ]);
    expect(filterSkillCommandOptions([skill()], 'missing', [])).toEqual([]);
  });
});

describe('normalizeSelectedSkillIds', () => {
  it('trims, validates, and deduplicates identities while preserving order', () => {
    expect(normalizeSelectedSkillIds([
      ' custom:hello-astra ',
      'custom:hello-astra',
      'invalid',
      'builtin:astra-authoring',
    ])).toEqual(['custom:hello-astra', 'builtin:astra-authoring']);
  });
});

describe('filterSlashCommandOptions', () => {
  it('places matching registered system commands before Skill options', () => {
    const options = filterSlashCommandOptions(
      [{ name: 'compact', command: '/compact', description: '压缩上下文', effect: 'compact_context', argument_mode: 'none', usage: '/compact', side_effect: 'write', available: true, execution_mode: 'host', unavailable_reason: null }],
      [skill({ name: 'compact-helper', qualified_identity: 'custom:compact-helper' })],
      'comp',
      [],
    );
    expect(options[0]).toMatchObject({ kind: 'command', command: { name: 'compact' } });
    expect(options[1]).toMatchObject({ kind: 'skill', skill: { name: 'compact-helper' } });
  });
});
