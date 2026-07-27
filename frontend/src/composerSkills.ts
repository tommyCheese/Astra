import type { SkillSummary } from './api';

export type SlashSkillCommand = {
  start: number;
  end: number;
  query: string;
};

export type SkillCommandOption = {
  skill: SkillSummary;
  selected: boolean;
};

export function detectSlashSkillCommand(
  value: string,
  selectionStart: number | null,
  selectionEnd: number | null = selectionStart,
  isComposing = false,
): SlashSkillCommand | null {
  if (
    isComposing
    || selectionStart === null
    || selectionEnd === null
    || selectionStart !== selectionEnd
    || selectionStart < 0
    || selectionStart > value.length
  ) return null;

  let start = selectionStart;
  while (start > 0 && !/\s/u.test(value[start - 1])) start -= 1;
  if (value[start] !== '/') return null;

  let end = selectionStart;
  while (end < value.length && !/\s/u.test(value[end])) end += 1;
  const token = value.slice(start, end);
  if (token.slice(1).includes('/') || token.includes('\\')) return null;

  return {
    start,
    end,
    query: value.slice(start + 1, selectionStart),
  };
}

export function filterSkillCommandOptions(
  skills: SkillSummary[],
  query: string,
  selectedSkillIds: string[],
): SkillCommandOption[] {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const selected = new Set(selectedSkillIds);
  return skills
    .filter((skill) => skill.enabled && Boolean(skill.active_revision))
    .filter((skill) => {
      if (!normalizedQuery) return true;
      return [skill.name, skill.description, skill.qualified_identity]
        .some((value) => value.toLocaleLowerCase().includes(normalizedQuery));
    })
    .sort((left, right) => (
      left.name.localeCompare(right.name)
      || left.qualified_identity.localeCompare(right.qualified_identity)
    ))
    .map((skill) => ({ skill, selected: selected.has(skill.qualified_identity) }));
}

export function normalizeSelectedSkillIds(identities: string[]): string[] {
  const seen = new Set<string>();
  return identities.flatMap((identity) => {
    const normalized = identity.trim();
    if (!/^(?:builtin|custom):[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/u.test(normalized) || seen.has(normalized)) {
      return [];
    }
    seen.add(normalized);
    return [normalized];
  });
}
