import type { PermissionCenterView } from './api';

export type IdentityView = PermissionCenterView['identities'][number];
export type IdentityGroup = IdentityView & { count: number };
export type AuditTone = 'info' | 'success' | 'warning' | 'danger';

export type AuditLogEntry = {
  id: number;
  code: string;
  title: string;
  detail: string;
  actor: string;
  tone: AuditTone;
  createdAt: string;
};

function groupIdentities(identities: IdentityView[]): IdentityGroup[] {
  const groups = new Map<string, IdentityGroup>();
  identities.forEach((identity) => {
    const key = [identity.type, identity.principal, identity.trust_level, identity.parent_identity_id || ''].join('\u0000');
    const group = groups.get(key);
    if (group) group.count += 1;
    else groups.set(key, { ...identity, count: 1 });
  });
  const rank: Record<string, number> = { main_agent: 0, subagent: 1, tool_provider: 2, external_provider: 2, tool_runtime: 3, reviewer: 4 };
  return [...groups.values()].sort((left, right) => (rank[left.type] ?? 99) - (rank[right.type] ?? 99));
}

export function buildIdentityPresentation(identities: IdentityView[], runId: string) {
  const current = identities.filter((identity) => identity.run_id === runId);
  // Older servers did not return run_id. Keep their identities visible while
  // preferring the precise current-run view whenever metadata is available.
  const visible = current.length ? current : identities;
  const active = visible.filter((identity) => !identity.revoked_at);
  return {
    execution: groupIdentities(active.filter((identity) => identity.type !== 'reviewer')),
    reviewers: groupIdentities(active.filter((identity) => identity.type === 'reviewer')),
    historicalCount: current.length
      ? identities.filter((identity) => identity.run_id && identity.run_id !== runId).length
      : 0,
  };
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

type Translate = (text: string) => string;

function firstResource(payload: Record<string, unknown>, translate: Translate): string {
  const resources = Array.isArray(payload.resources) ? payload.resources : [];
  const direct = resources.find((resource) => typeof resource === 'string');
  if (typeof direct === 'string') return direct.replace(/^task:\/\/[^/]+\/workspace\//, `${translate('工作区')}/`);
  const requests = Array.isArray(payload.requests) ? payload.requests : [];
  const request = requests.find((item) => item && typeof item === 'object') as Record<string, unknown> | undefined;
  return stringValue(request?.resource).replace(/^task:\/\/[^/]+\/workspace\//, `${translate('工作区')}/`);
}

export function buildAuditLog(events: NonNullable<PermissionCenterView['policy_explanations']>, translate: Translate = (text) => text): AuditLogEntry[] {
  return [...events]
    .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))
    .map((event) => {
      const payload = event.payload;
      const tool = stringValue(payload.tool_name) || translate('工具');
      const resource = firstResource(payload, translate);
      const action = stringValue(payload.action_summary);
      const decision = stringValue(payload.decision);
      const reason = stringValue(payload.reason_code);
      const detailParts = [action, resource, reason && translate('原因：{reason}').replace('{reason}', reason)].filter(Boolean);

      if (event.type === 'approval.requested') return {
        id: event.id, code: event.type, title: translate('{tool} 请求用户确认').replace('{tool}', tool),
        detail: detailParts.join(' · ') || stringValue(payload.impact) || translate('操作需要明确授权后才能继续。'),
        actor: translate('Astra 权限门'), tone: 'warning' as const, createdAt: event.created_at,
      };
      if (event.type === 'approval.decided') {
        const rejected = decision === 'reject';
        const labels: Record<string, string> = {
          approve_once: '用户允许了本次操作', allow_similar: '用户允许了本次运行中的类似操作',
          allow_task: '用户允许了当前任务中的类似操作', reject: '用户拒绝了操作',
        };
        return {
          id: event.id, code: event.type, title: translate('{tool}：{decision}').replace('{tool}', tool).replace('{decision}', translate(labels[decision] || '用户已作出决定')),
          detail: rejected && stringValue(payload.guidance) ? translate('用户说明：{guidance}').replace('{guidance}', stringValue(payload.guidance)) : translate('审批决定已记录并应用。'),
          actor: translate('当前用户'), tone: rejected ? 'danger' as const : 'success' as const, createdAt: event.created_at,
        };
      }
      if (event.type === 'tool_call.effect_blocked_by_mode') return {
        id: event.id, code: event.type, title: translate('{tool} 未执行').replace('{tool}', tool),
        detail: stringValue(payload.summary) || translate('当前执行模式阻止了这项操作。'),
        actor: translate('Astra 执行策略'), tone: 'danger' as const, createdAt: event.created_at,
      };

      const denied = decision === 'deny';
      const asked = decision === 'ask';
      return {
        id: event.id, code: event.type,
        title: translate('{tool} 权限判定：{decision}').replace('{tool}', tool).replace('{decision}', translate(denied ? '阻止' : asked ? '需要确认' : '允许')),
        detail: detailParts.join(' · ') || translate('权限策略已完成判定。'),
        actor: translate('Astra 权限引擎'), tone: denied ? 'danger' as const : asked ? 'warning' as const : 'success' as const,
        createdAt: event.created_at,
      };
    });
}
