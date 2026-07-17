import { describe, expect, it } from 'vitest';
import { buildAuditLog, buildIdentityPresentation } from '../src/auditPresentation';

describe('audit presentation', () => {
  it('shows only the current run identity chain and folds task history', () => {
    const identities = [
      { id: 'main-now', type: 'main_agent', principal: 'astra.agent', trust_level: 'platform', run_id: 'run-now' },
      { id: 'tool-now', type: 'tool_runtime', principal: 'astra.builtin:chart.render@1.0', trust_level: 'platform', run_id: 'run-now', parent_identity_id: 'provider-now' },
      { id: 'reviewer-now', type: 'reviewer', principal: 'local-user', trust_level: 'user', run_id: 'run-now' },
      { id: 'main-old', type: 'main_agent', principal: 'astra.agent', trust_level: 'platform', run_id: 'run-old' },
    ];
    const result = buildIdentityPresentation(identities, 'run-now');
    expect(result.execution.map((identity) => identity.id)).toEqual(['main-now', 'tool-now']);
    expect(result.reviewers[0].id).toBe('reviewer-now');
    expect(result.historicalCount).toBe(1);
  });

  it('turns permission events into newest-first readable log entries', () => {
    const result = buildAuditLog([
      { id: 1, type: 'approval.requested', payload: { tool_name: 'chart.render', action_summary: '保存图表' }, created_at: '2026-07-17T10:00:00Z' },
      { id: 2, type: 'approval.decided', payload: { tool_name: 'chart.render', decision: 'reject' }, created_at: '2026-07-17T10:01:00Z' },
    ]);
    expect(result.map((entry) => entry.id)).toEqual([2, 1]);
    expect(result[0]).toMatchObject({ tone: 'danger', actor: '当前用户' });
    expect(result[1]).toMatchObject({ tone: 'warning', actor: 'Astra 权限门' });
  });
});
