import { CSSProperties, FormEvent, KeyboardEvent as ReactKeyboardEvent, MouseEvent, PointerEvent as ReactPointerEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AstraApiError, ApiErrorPayload, buildRuntime, cancelRun, cancelRuntimeBuild, createConversationShare, createRun, decideToolApproval, deleteConversation, getConversation, getConversationStrategy, getPermissionCenter, getRun, getRuntimeProfile, getToolSettings, listConversationShares, listConversations, listLibraryFiles, listRuns, resumeRun, revokeConversationShare, revokePermissionGrant, streamRunEvents, updateConversation, updateConversationStrategy, updateToolSettings, type ConversationStrategyPreferences, type LibraryFile, type PermissionCenterView, type RunModelConfig, type ToolSetting } from './api';
import { buildAuditLog, buildIdentityPresentation, type IdentityGroup } from './auditPresentation';
import { I18nProvider, useI18n } from './i18n';
import { ThemeProvider, useTheme } from './theme';
import type { ArtifactView, ChatMessage, ConversationShare, ConversationShareSummary, ConversationSummary, PendingApproval, RunView } from './types';
import { UsageDashboard } from './UsageDashboard';
import { buildPresentation, HISTORY_LIMIT, normalizeRunView, type ConversationEntry } from './conversations';
import { createOptimisticProcessState, isDecisionGroup, reconcileProcessSnapshot, reduceProcessEvent, type ProcessStreamItem, type ProcessStreamState } from './processStream';
import type { RunStreamEvent } from './api';

const terminalStatuses = new Set(['completed', 'completed_with_warnings', 'failed', 'blocked', 'waiting_user', 'cancelled']);
const STORAGE_KEYS = {
  conversations: 'astra.conversations.v1',
  processPanelDefaultOpen: 'astra.process-panel-default-open.v1',
  modelProviders: 'astra.model-providers.v1',
  selectedModel: 'astra.selected-model.v1',
  sidebarCollapsed: 'astra.sidebar-collapsed.v1',
  sidebarWidth: 'astra.sidebar-width.v1',
};
const SIDEBAR_DEFAULT_WIDTH = 260;
const SIDEBAR_MIN_WIDTH = 220;
const SIDEBAR_MAX_WIDTH = 420;
const DEFAULT_CONVERSATION_STRATEGY: ConversationStrategyPreferences = {
  preferred_answer_mode: 'standard',
  reasoning_effort: 'balanced',
  max_tool_calls: 8,
  planning_strategy: 'adaptive',
  reflection_enabled: true,
  reflection_trigger: 'adaptive',
};

const TOOL_CALL_LIMITS: Record<ConversationStrategyPreferences['reasoning_effort'], { min: number; max: number; defaultValue: number }> = {
  fast: { min: 0, max: 5, defaultValue: 5 },
  balanced: { min: 6, max: 15, defaultValue: 8 },
  deep: { min: 16, max: 50, defaultValue: 16 },
};

function reasoningEffortValue(label: string): ConversationStrategyPreferences['reasoning_effort'] {
  return label === '快速' ? 'fast' : label === '深入' ? 'deep' : 'balanced';
}

function toolLimitForEffort(effort: ConversationStrategyPreferences['reasoning_effort'], current: number) {
  const range = TOOL_CALL_LIMITS[effort];
  return current >= range.min && current <= range.max ? current : range.defaultValue;
}

function reasoningEffortLabel(value: ConversationStrategyPreferences['reasoning_effort']): string {
  return value === 'fast' ? '快速' : value === 'deep' ? '深入' : '均衡';
}

function planningStrategyLabel(value: ConversationStrategyPreferences['planning_strategy']): string {
  return value === 'plan_first' ? '先规划' : '自适应';
}

function reflectionTriggerLabel(value: ConversationStrategyPreferences['reflection_trigger']): string {
  return value === 'failure_only' ? '失败时' : value === 'every_turn' ? '每轮' : '按需';
}

export function App() {
  return <I18nProvider><ThemeProvider><AppContent /></ThemeProvider></I18nProvider>;
}

function AppContent() {
  const { language, t } = useI18n();
  const [goal, setGoal] = useState('');
  const [run, setRun] = useState<RunView | null>(null);
  const [conversationHistory, setConversationHistory] = useState<ConversationEntry[]>(loadConversationHistory);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [priorMessages, setPriorMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [approvalSubmitting, setApprovalSubmitting] = useState(false);
  const [streamingAnswer, setStreamingAnswer] = useState('');
  const [answerComplete, setAnswerComplete] = useState(false);
  const [answerSettling, setAnswerSettling] = useState(false);
  const [processState, setProcessState] = useState<ProcessStreamState | null>(null);
  const [processPanelDefaultOpen, setProcessPanelDefaultOpen] = useState(loadProcessPanelDefaultOpen);
  const [processPanelOpenByRun, setProcessPanelOpenByRun] = useState<Record<string, boolean>>({});
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [error, setError] = useState<ApiErrorPayload | null>(null);
  const [view, setView] = useState<'chat' | 'settings' | 'shares' | 'library'>('chat');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => readLocalJson<boolean>(STORAGE_KEYS.sidebarCollapsed) ?? false);
  const [sidebarWidth, setSidebarWidth] = useState(loadSidebarWidth);
  const [usageOpen, setUsageOpen] = useState(false);
  const [controlCenterOpen, setControlCenterOpen] = useState(false);
  const [strategyHelpOpen, setStrategyHelpOpen] = useState(false);
  const [conversationAction, setConversationAction] = useState<{ kind: 'rename' | 'share' | 'delete'; conversation: ConversationEntry } | null>(null);
  const [modelOpen, setModelOpen] = useState(false);
  const [attachOpen, setAttachOpen] = useState(false);
  const [executionMenuOpen, setExecutionMenuOpen] = useState(false);
  const [executionMode, setExecutionMode] = useState<'plan' | 'default' | 'bypass'>('default');
  const [bypassConfirmOpen, setBypassConfirmOpen] = useState(false);
  const [providerConfigs, setProviderConfigs] = useState<ModelProviderConfig[]>(loadProviderConfigs);
  const [selectedModelKey, setSelectedModelKey] = useState(() => readLocalString(STORAGE_KEYS.selectedModel) || 'openai:gpt-5');
  const [reflectionEnabled, setReflectionEnabled] = useState(true);
  const [answerMode, setAnswerMode] = useState<'standard' | 'trusted'>('standard');
  const [reasoningEffort, setReasoningEffort] = useState('均衡');
  const [toolCallLimit, setToolCallLimit] = useState(8);
  const [planningStrategy, setPlanningStrategy] = useState('自适应');
  const [reflectionTrigger, setReflectionTrigger] = useState('按需');
  const [conversationStrategyReady, setConversationStrategyReady] = useState(false);
  const [trustedTransitionActive, setTrustedTransitionActive] = useState(false);
  const [settingsCategory, setSettingsCategory] = useState('模型管理');
  const attachMenuRef = useRef<HTMLDivElement>(null);
  const executionMenuRef = useRef<HTMLDivElement>(null);
  const modelMenuRef = useRef<HTMLDivElement>(null);
  const goalInputRef = useRef<HTMLTextAreaElement>(null);
  const conversationRef = useRef<HTMLDivElement>(null);
  const followLatestRef = useRef(true);
  const jumpingToLatestRef = useRef(false);
  const jumpResetTimerRef = useRef<number>();
  const deltaBufferRef = useRef('');
  const deltaFrameRef = useRef<number>();
  const processEventBufferRef = useRef<RunStreamEvent[]>([]);
  const processFrameRef = useRef<number>();
  const refreshTimerRef = useRef<number>();
  const conversationStrategyRef = useRef<ConversationStrategyPreferences>(DEFAULT_CONVERSATION_STRATEGY);
  const conversationStrategyTouchedRef = useRef(false);
  const conversationStrategySaveRef = useRef<Promise<void>>(Promise.resolve());
  const initialSnapshotControllerRef = useRef<AbortController>();
  const conversationControllerRef = useRef<AbortController>();
  const cancelRequestedRef = useRef(false);
  const trustedTransitionTimerRef = useRef<number>();
  const availableModels = useMemo(() => providerConfigs
    .filter((provider) => provider.enabled)
    .flatMap((provider) => parseModelIds(provider.models).map((model) => ({ key: `${provider.id}:${model}`, model, providerId: provider.id, providerName: provider.name }))), [providerConfigs]);
  const selectedModel = availableModels.find((item) => item.key === selectedModelKey)?.model ?? '';

  useEffect(() => writeLocalJson(STORAGE_KEYS.conversations, conversationHistory.slice(0, HISTORY_LIMIT)), [conversationHistory]);
  useEffect(() => writeLocalJson(STORAGE_KEYS.processPanelDefaultOpen, processPanelDefaultOpen), [processPanelDefaultOpen]);
  useEffect(() => writeLocalJson(STORAGE_KEYS.modelProviders, providerConfigs), [providerConfigs]);
  useEffect(() => writeLocalString(STORAGE_KEYS.selectedModel, selectedModelKey), [selectedModelKey]);
  useEffect(() => writeLocalJson(STORAGE_KEYS.sidebarCollapsed, sidebarCollapsed), [sidebarCollapsed]);
  useEffect(() => writeLocalJson(STORAGE_KEYS.sidebarWidth, sidebarWidth), [sidebarWidth]);
  useEffect(() => () => {
    if (trustedTransitionTimerRef.current !== undefined) {
      window.clearTimeout(trustedTransitionTimerRef.current);
    }
  }, []);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    void getConversationStrategy(controller.signal).then((strategy) => {
      if (!active || conversationStrategyTouchedRef.current) return;
      conversationStrategyRef.current = strategy;
      setAnswerMode(strategy.preferred_answer_mode ?? 'standard');
      setReasoningEffort(reasoningEffortLabel(strategy.reasoning_effort));
      setToolCallLimit(strategy.max_tool_calls);
      setPlanningStrategy(planningStrategyLabel(strategy.planning_strategy));
      setReflectionEnabled(strategy.reflection_enabled);
      setReflectionTrigger(reflectionTriggerLabel(strategy.reflection_trigger));
    }).catch(() => { /* Retain documented defaults while the backend is unavailable. */ }).finally(() => {
      if (active) setConversationStrategyReady(true);
    });
    return () => {
      active = false;
      controller.abort();
    };
  }, []);

  useEffect(() => () => {
    if (jumpResetTimerRef.current !== undefined) window.clearTimeout(jumpResetTimerRef.current);
    if (deltaFrameRef.current !== undefined) window.cancelAnimationFrame(deltaFrameRef.current);
    if (processFrameRef.current !== undefined) window.cancelAnimationFrame(processFrameRef.current);
    if (refreshTimerRef.current !== undefined) window.clearTimeout(refreshTimerRef.current);
    initialSnapshotControllerRef.current?.abort();
    conversationControllerRef.current?.abort();
  }, []);

  useEffect(() => {
    let active = true;
    void listConversations(HISTORY_LIMIT).then((summaries) => {
      if (!active) return;
      if (!summaries.length) throw new Error('no persisted conversations');
      setConversationHistory((local) => summaries.map((summary) => {
        const cached = local.find((item) => item.id === summary.id);
        return { ...cached, id: summary.id, priorMessages: cached?.priorMessages ?? [], title: summary.title, pinned_at: summary.pinned_at, updated_at: summary.updated_at, has_active_share: summary.has_active_share };
      }));
    }).catch(() => listRuns(200).then((runs) => {
      if (!active) return;
      const grouped = new Map<string, RunView[]>();
      for (const item of runs) grouped.set(item.task_id, [...(grouped.get(item.task_id) ?? []), normalizeRunView(item)]);
      setConversationHistory([...grouped.entries()].map(([id, items]) => ({ id, run: items[0], priorMessages: [...items.slice(1)].reverse().flatMap(buildPresentation) })));
    }).catch(() => { /* retain browser history while the backend is offline */ }));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (availableModels.length && !availableModels.some((item) => item.key === selectedModelKey)) {
      setSelectedModelKey(availableModels[0].key);
    } else if (!availableModels.length && selectedModelKey) {
      setSelectedModelKey('');
    }
  }, [availableModels, selectedModelKey]);

  useEffect(() => {
    if (!attachOpen && !modelOpen && !executionMenuOpen) {
      return;
    }

    function closeOnOutsideInteraction(event: PointerEvent) {
      const target = event.target as Node;
      if (!attachMenuRef.current?.contains(target)) {
        setAttachOpen(false);
      }
      if (!modelMenuRef.current?.contains(target)) {
        setModelOpen(false);
      }
      if (!executionMenuRef.current?.contains(target)) {
        setExecutionMenuOpen(false);
      }
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setAttachOpen(false);
        setModelOpen(false);
        setExecutionMenuOpen(false);
      }
    }

    document.addEventListener('pointerdown', closeOnOutsideInteraction);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsideInteraction);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [attachOpen, modelOpen, executionMenuOpen]);

  useEffect(() => {
    if (!sidebarOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSidebarOpen(false);
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [sidebarOpen]);

  function rememberConversation(nextRun: RunView, previousMessages: ChatMessage[] = priorMessages) {
    const conversationId = activeConversationId ?? nextRun.task_id;
    setActiveConversationId(conversationId);
    setConversationHistory((items) => {
      const existing = items.find((item) => item.id === conversationId);
      return [{ ...existing, id: conversationId, run: nextRun, priorMessages: previousMessages, title: existing?.title ?? conversationTitle(nextRun, t('当前 Web Agent 会话')), updated_at: new Date().toISOString() }, ...items.filter((item) => item.id !== conversationId)].slice(0, HISTORY_LIMIT);
    });
  }

  async function openConversation(conversation: ConversationEntry) {
    const previousConversationId = activeConversationId;
    initialSnapshotControllerRef.current?.abort();
    initialSnapshotControllerRef.current = undefined;
    conversationControllerRef.current?.abort();
    const controller = new AbortController();
    conversationControllerRef.current = controller;
    setActiveConversationId(conversation.id);
    setStreamingAnswer('');
    setAnswerComplete(false);
    setAnswerSettling(false);
    deltaBufferRef.current = '';
    processEventBufferRef.current = [];
    try {
      const detail = await getConversation(conversation.id, controller.signal);
      if (conversationControllerRef.current !== controller) return;
      const runs = detail.runs.map(normalizeRunView);
      const latest = runs[runs.length - 1];
      if (!latest) throw new Error('conversation has no runs');
      setPriorMessages(runs.slice(0, -1).flatMap(buildPresentation));
      setRun(latest);
      setProcessState(reconcileProcessSnapshot(null, latest));
      setConversationHistory((items) => items.map((item) => item.id === conversation.id ? { ...item, run: latest, title: detail.title, pinned_at: detail.pinned_at, updated_at: detail.updated_at, has_active_share: detail.has_active_share } : item));
    } catch (error) {
      if (controller.signal.aborted) return;
      if (conversation.run) {
        const snapshot = normalizeRunView(conversation.run);
        setPriorMessages(conversation.priorMessages);
        setRun(snapshot);
        setProcessState(reconcileProcessSnapshot(null, snapshot));
      } else {
        setActiveConversationId(previousConversationId);
        setError({ type: 'infrastructure.database', code: 'CONVERSATION_LOAD_FAILED', message: t('无法加载该对话，请稍后重试。'), retryable: true, trace_id: 'unavailable' });
      }
    } finally {
      if (conversationControllerRef.current === controller) {
        conversationControllerRef.current = undefined;
      }
    }
    followLatestRef.current = true;
    setShowJumpToLatest(false);
    changeView('chat');
  }

  function persistConversationStrategy(patch: Partial<ConversationStrategyPreferences>) {
    conversationStrategyTouchedRef.current = true;
    const next = { ...conversationStrategyRef.current, ...patch };
    conversationStrategyRef.current = next;
    setAnswerMode(next.preferred_answer_mode);
    setReasoningEffort(reasoningEffortLabel(next.reasoning_effort));
    setToolCallLimit(next.max_tool_calls);
    setPlanningStrategy(planningStrategyLabel(next.planning_strategy));
    setReflectionEnabled(next.reflection_enabled);
    setReflectionTrigger(reflectionTriggerLabel(next.reflection_trigger));
    conversationStrategySaveRef.current = conversationStrategySaveRef.current
      .then(() => updateConversationStrategy(next))
      .then(() => undefined)
      .catch((err) => {
        setError(err instanceof AstraApiError ? err.payload : { type: 'infrastructure.database', code: 'STRATEGY_PREFERENCE_SAVE_FAILED', message: t('保存对话策略失败，当前选择可能无法在重启后恢复。'), retryable: true, trace_id: 'unavailable' });
      });
  }

  function toggleTrustedMode() {
    const nextMode = answerMode === 'trusted' ? 'standard' : 'trusted';
    if (trustedTransitionTimerRef.current !== undefined) {
      window.clearTimeout(trustedTransitionTimerRef.current);
    }
    if (nextMode === 'trusted') {
      setTrustedTransitionActive(true);
      trustedTransitionTimerRef.current = window.setTimeout(() => {
        setTrustedTransitionActive(false);
        trustedTransitionTimerRef.current = undefined;
      }, 1800);
    } else {
      setTrustedTransitionActive(false);
      trustedTransitionTimerRef.current = undefined;
    }
    persistConversationStrategy({ preferred_answer_mode: nextMode });
  }

  async function cancelRunById(runId: string, previousMessages: ChatMessage[] = priorMessages) {
    initialSnapshotControllerRef.current?.abort();
    initialSnapshotControllerRef.current = undefined;
    setAnswerSettling(false);
    try {
      const next = normalizeRunView(await cancelRun(runId));
      deltaBufferRef.current = '';
      setStreamingAnswer('');
      setAnswerComplete(false);
      setRun(next);
      setProcessState((state) => reconcileProcessSnapshot(state, next));
      rememberConversation(next, previousMessages);
    } catch (err) {
      setError(err instanceof AstraApiError ? err.payload : { type: 'runtime.internal_error', code: 'CANCEL_RUN_FAILED', message: t('终止回答失败，当前回答可能仍在继续。'), retryable: true, trace_id: 'unavailable' });
    } finally {
      cancelRequestedRef.current = false;
      setStopping(false);
    }
  }

  async function stopActiveRun() {
    if (stopping) return;
    cancelRequestedRef.current = true;
    setStopping(true);
    setAnswerSettling(false);
    if (run && !terminalStatuses.has(run.status)) {
      await cancelRunById(run.id);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (loading || run?.pending_approval || (run && !terminalStatuses.has(run.status))) return;
    const trimmedGoal = goal.trim();
    if (!trimmedGoal) {
      setError({ type: 'validation.input_invalid', code: 'GOAL_REQUIRED', message: t('请输入你想完成的目标。'), retryable: false, trace_id: 'local' });
      return;
    }
    setError(null);
    followLatestRef.current = true;
    setShowJumpToLatest(false);
    setStreamingAnswer('');
    setAnswerComplete(false);
    setAnswerSettling(false);
    deltaBufferRef.current = '';
    setLoading(true);
    try {
      const previousMessages = run ? messages : [];
      const selectedOption = availableModels.find((item) => item.key === selectedModelKey);
      const selectedProvider = providerConfigs.find((item) => item.id === selectedOption?.providerId);
      const modelConfig = selectedOption && selectedProvider ? {
        provider: selectedProvider.id,
        name: selectedOption.model,
        api_key: selectedProvider.apiKey,
        base_url: selectedProvider.endpoint,
      } : undefined;
      const created = run?.status === 'waiting_user'
        ? await resumeRun(run.id, trimmedGoal, typeof run.waiting_state?.continuation_token === 'string' ? run.waiting_state.continuation_token : undefined, modelConfig)
        : await createRun(trimmedGoal, run?.task_id, answerMode, {
        reasoning_effort: conversationStrategyRef.current.reasoning_effort,
        max_tool_calls: conversationStrategyRef.current.max_tool_calls,
        planning_strategy: conversationStrategyRef.current.planning_strategy,
        reflection_enabled: conversationStrategyRef.current.reflection_enabled,
        reflection_trigger: conversationStrategyRef.current.reflection_trigger,
        execution_mode: executionMode === 'plan' ? 'plan_only' : executionMode === 'bypass' ? 'auto_approval' : 'request_approval',
        verification_level: 'standard',
        }, modelConfig);
      const createdAnswerMode = (created as { answer_mode?: 'standard' | 'trusted' }).answer_mode;
      const current = normalizeRunView({
        id: created.run_id,
        task_id: created.task_id,
        status: created.status,
        mode: 'general-agent',
        answer_mode: createdAnswerMode ?? answerMode,
        execution_profile: {},
        result: null,
        steps: [], tool_calls: [], artifacts: [], events: [], turns: [], memories: [],
        chat_messages: [{ id: `optimistic-${created.run_id}`, role: 'user', content: trimmedGoal, status: 'completed', metadata: {} }],
      } as RunView);
      setPriorMessages(previousMessages);
      setRun(current);
      setProcessState(createOptimisticProcessState(created.run_id));
      rememberConversation(current, previousMessages);
      setGoal('');
      if (cancelRequestedRef.current) {
        await cancelRunById(created.run_id, previousMessages);
        return;
      }
      initialSnapshotControllerRef.current?.abort();
      const initialSnapshotController = new AbortController();
      initialSnapshotControllerRef.current = initialSnapshotController;
      void getRun(created.run_id, initialSnapshotController.signal).then((snapshot) => {
        if (initialSnapshotControllerRef.current !== initialSnapshotController) return;
        initialSnapshotControllerRef.current = undefined;
        const next = normalizeRunView(snapshot);
        setRun(next);
        setProcessState((state) => reconcileProcessSnapshot(state, next));
        rememberConversation(next, previousMessages);
      }).catch(() => { /* SSE and fallback polling will recover the snapshot. */ });
    } catch (err) {
      cancelRequestedRef.current = false;
      setStopping(false);
      setError(err instanceof AstraApiError ? err.payload : { type: 'runtime.internal_error', code: 'REQUEST_FAILED', message: t('服务暂时出现异常，请稍后重试。'), retryable: true, trace_id: 'unavailable' });
    } finally {
      setLoading(false);
    }
  }

  function selectedRunModel(): RunModelConfig | undefined {
    const selectedOption = availableModels.find((item) => item.key === selectedModelKey);
    const selectedProvider = providerConfigs.find((item) => item.id === selectedOption?.providerId);
    const keyOptional = selectedProvider
      ? ['ollama', 'lmstudio', 'vllm', 'localai'].includes(selectedProvider.id)
      : false;
    if (!selectedProvider || (!selectedProvider.apiKey.trim() && !keyOptional)) {
      return undefined;
    }
    return selectedOption && selectedProvider ? {
      provider: selectedProvider.id,
      name: selectedOption.model,
      api_key: selectedProvider.apiKey,
      base_url: selectedProvider.endpoint,
    } : undefined;
  }

  async function decideApproval(decision: 'approve_once' | 'allow_similar' | 'allow_task' | 'reject') {
    const approval = run?.pending_approval;
    const token = run?.waiting_state?.continuation_token;
    if (!run || !approval || typeof token !== 'string' || approvalSubmitting) return;
    setApprovalSubmitting(true);
    setError(null);
    try {
      const resumed = await decideToolApproval(run.id, approval.id, decision, token, selectedRunModel());
      const optimistic = { ...run, status: resumed.status, pending_approval: null, waiting_state: null };
      setRun(optimistic);
      rememberConversation(optimistic);
      const snapshot = normalizeRunView(await getRun(run.id));
      setRun(snapshot);
      setProcessState((state) => reconcileProcessSnapshot(state, snapshot));
      rememberConversation(snapshot);
    } catch (err) {
      setError(err instanceof AstraApiError ? err.payload : { type: 'runtime.internal_error', code: 'APPROVAL_FAILED', message: t('提交批准决定失败，请刷新后重试。'), retryable: true, trace_id: 'unavailable' });
    } finally {
      setApprovalSubmitting(false);
    }
  }

  useEffect(() => {
    if (!run || terminalStatuses.has(run.status)) {
      return;
    }
    let active = true;
    let fallback: number | undefined;
    let refreshing = false;
    let closeStream: () => void = () => {};
    const controller = new AbortController();
    const flushDeltas = () => {
      deltaFrameRef.current = undefined;
      const delta = deltaBufferRef.current;
      deltaBufferRef.current = '';
      if (delta) setStreamingAnswer((value) => value + delta);
    };
    const queueDelta = (delta: string) => {
      deltaBufferRef.current += delta;
      if (deltaFrameRef.current === undefined) deltaFrameRef.current = window.requestAnimationFrame(flushDeltas);
    };
    const flushProcessEvents = () => {
      processFrameRef.current = undefined;
      const events = processEventBufferRef.current;
      processEventBufferRef.current = [];
      if (!events.length) return;
      setProcessState((state) => {
        let next = state?.runId === run.id ? state : createOptimisticProcessState(run.id);
        for (const event of events) next = reduceProcessEvent(next, event);
        return next;
      });
    };
    const queueProcessEvent = (event: RunStreamEvent) => {
      processEventBufferRef.current.push(event);
      if (processFrameRef.current === undefined) processFrameRef.current = window.requestAnimationFrame(flushProcessEvents);
    };
    const refreshRun = async () => {
      if (refreshing || !active) return;
      refreshing = true;
      try {
        const next = normalizeRunView(await getRun(run.id, controller.signal));
        if (!active) return;
        setRun(next);
        setProcessState((state) => reconcileProcessSnapshot(state, next));
        rememberConversation(next);
        if (terminalStatuses.has(next.status) && next.result) {
          setStreamingAnswer('');
          setAnswerSettling(false);
          closeStream();
          if (fallback !== undefined) window.clearInterval(fallback);
        }
      } catch {
        // Keep polling so a transient backend outage can recover automatically.
      } finally {
        refreshing = false;
      }
    };
    const scheduleRefresh = (immediate = false) => {
      if (refreshTimerRef.current !== undefined) window.clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = window.setTimeout(() => { refreshTimerRef.current = undefined; void refreshRun(); }, immediate ? 0 : 100);
    };
    closeStream = streamRunEvents(run.id, (event) => {
      initialSnapshotControllerRef.current?.abort();
      initialSnapshotControllerRef.current = undefined;
      if (event.type === 'answer.started') {
        deltaBufferRef.current = '';
        setStreamingAnswer('');
        setAnswerComplete(false);
        setAnswerSettling(false);
        return;
      }
      if (event.type === 'answer.delta') {
        setAnswerSettling(false);
        queueDelta(String(event.payload.delta ?? ''));
        return;
      }
      if (event.type === 'answer.settling') {
        if (deltaFrameRef.current !== undefined) window.cancelAnimationFrame(deltaFrameRef.current);
        flushDeltas();
        setAnswerComplete(true);
        setAnswerSettling(true);
        return;
      }
      if (event.type === 'answer.completed') {
        if (deltaFrameRef.current !== undefined) window.cancelAnimationFrame(deltaFrameRef.current);
        deltaFrameRef.current = undefined;
        deltaBufferRef.current = '';
        setStreamingAnswer(String(event.payload.content ?? ''));
        setAnswerComplete(true);
        setAnswerSettling(true);
        scheduleRefresh(true);
        return;
      }
      const isProcessEvent = event.type.startsWith('reasoning.') || ['agent_turn.created', 'tool_call.started', 'tool_call.completed', 'reflection.created', 'verification.created'].includes(event.type);
      if (isProcessEvent) {
        queueProcessEvent(event);
        if (!['reasoning.phase.started', 'reasoning.summary.delta', 'reasoning.summary.completed'].includes(event.type)) scheduleRefresh();
        return;
      }
      if (event.type !== 'heartbeat' && event.type !== 'stream.ready') scheduleRefresh();
    }, () => { void refreshRun(); });
    fallback = window.setInterval(() => { void refreshRun(); }, 3000);
    return () => {
      active = false;
      controller.abort();
      closeStream();
      if (deltaFrameRef.current !== undefined) window.cancelAnimationFrame(deltaFrameRef.current);
      if (processFrameRef.current !== undefined) window.cancelAnimationFrame(processFrameRef.current);
      processEventBufferRef.current = [];
      if (refreshTimerRef.current !== undefined) window.clearTimeout(refreshTimerRef.current);
      if (fallback !== undefined) window.clearInterval(fallback);
    };
  }, [run?.id, run?.status === 'waiting_user']);

  const messages = useMemo(() => {
    const currentMessages = buildPresentation(run)
      .filter((message) => !streamingAnswer || message.metadata.presentation !== 'answer')
      .map((message) => ({ ...message, id: `${run?.id ?? 'idle'}:${priorMessages.length}:${message.id}` }));
    const streamed = streamingAnswer ? [{ id: `${run?.id ?? 'idle'}-stream`, role: 'assistant', content: streamingAnswer, status: answerComplete ? 'completed' : 'streaming', metadata: {} }] : [];
    return [...priorMessages, ...currentMessages, ...streamed];
  }, [priorMessages, run, streamingAnswer, answerComplete]);
  const activeProcessItemId = [...(processState?.items ?? [])].reverse().find((item) => item.status === 'running')?.id;
  const initializeProcessPanel = useCallback((runId: string) => {
    setProcessPanelOpenByRun((states) => Object.prototype.hasOwnProperty.call(states, runId)
      ? states
      : { ...states, [runId]: processPanelDefaultOpen });
  }, [processPanelDefaultOpen]);
  const changeProcessPanelOpen = useCallback((runId: string, open: boolean) => {
    setProcessPanelOpenByRun((states) => ({ ...states, [runId]: open }));
    setProcessPanelDefaultOpen(open);
  }, []);

  useEffect(() => {
    if (!followLatestRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      const element = conversationRef.current;
      if (!element) return;
      if (typeof element.scrollTo === 'function') element.scrollTo({ top: element.scrollHeight, behavior: 'auto' });
      else element.scrollTop = element.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [messages.length, streamingAnswer, run?.status, activeProcessItemId]);

  function handleConversationScroll() {
    const element = conversationRef.current;
    if (!element || jumpingToLatestRef.current) return;
    const nearLatest = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
    followLatestRef.current = nearLatest;
    setShowJumpToLatest(!nearLatest);
  }

  function jumpToLatest() {
    const element = conversationRef.current;
    if (!element) return;
    jumpingToLatestRef.current = true;
    followLatestRef.current = true;
    setShowJumpToLatest(false);
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' });
    else element.scrollTop = element.scrollHeight;
    if (jumpResetTimerRef.current !== undefined) window.clearTimeout(jumpResetTimerRef.current);
    jumpResetTimerRef.current = window.setTimeout(() => { jumpingToLatestRef.current = false; }, 450);
  }

  function changeView(nextView: 'chat' | 'settings' | 'shares' | 'library') {
    setView(nextView);
  }

  function startNewChat() {
    conversationControllerRef.current?.abort();
    conversationControllerRef.current = undefined;
    initialSnapshotControllerRef.current?.abort();
    initialSnapshotControllerRef.current = undefined;
    setRun(null);
    setActiveConversationId(null);
    setPriorMessages([]);
    setError(null);
    setStreamingAnswer('');
    setAnswerComplete(false);
    setAnswerSettling(false);
    setProcessState(null);
    cancelRequestedRef.current = false;
    setStopping(false);
    deltaBufferRef.current = '';
    followLatestRef.current = true;
    setShowJumpToLatest(false);
    setGoal('');
    changeView('chat');
    setSidebarOpen(false);
  }

  async function toggleConversationPin(conversation: ConversationEntry) {
    try {
      const updated = await updateConversation(conversation.id, { pinned: !conversation.pinned_at });
      setConversationHistory((items) => items.map((item) => item.id === updated.id ? { ...item, title: updated.title, pinned_at: updated.pinned_at, updated_at: updated.updated_at } : item));
    } catch (err) {
      setError(err instanceof AstraApiError ? err.payload : { type: 'infrastructure.database', code: 'CONVERSATION_PIN_FAILED', message: t('更新置顶状态失败，请稍后重试。'), retryable: true, trace_id: 'unavailable' });
    }
  }

  const activeConversationTitle = conversationHistory.find((item) => item.id === activeConversationId)?.title;

  return (
    <main
      className={`app-layout ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}
      style={{ '--sidebar-width': `${sidebarWidth}px` } as CSSProperties}
    >
      {trustedTransitionActive && (
        <div className="trusted-mode-transition" aria-hidden="true" data-testid="trusted-mode-transition">
          <i className="trusted-mode-transition-wave wave-one" />
          <i className="trusted-mode-transition-wave wave-two" />
        </div>
      )}
      <Sidebar
        open={sidebarOpen}
        run={run}
        activeConversationId={activeConversationId}
        conversations={conversationHistory}
        activeView={view}
        onNewChat={startNewChat}
        onSelectConversation={(conversation) => { setSidebarOpen(false); void openConversation(conversation); }}
        onConversationAction={(kind, conversation) => setConversationAction({ kind, conversation })}
        onTogglePin={(conversation) => { void toggleConversationPin(conversation); }}
        onOpenSettings={() => {
          setSidebarOpen(false);
          setSettingsCategory('模型管理');
          changeView('settings');
        }}
        onOpenShares={() => { setSidebarOpen(false); changeView('shares'); }}
        onOpenLibrary={() => { setSidebarOpen(false); changeView('library'); }}
        onOpenUsage={() => { setSidebarOpen(false); setUsageOpen(true); }}
        onClose={() => setSidebarOpen(false)}
        collapsed={sidebarCollapsed}
        width={sidebarWidth}
        onCollapse={() => setSidebarCollapsed(true)}
        onExpand={() => setSidebarCollapsed(false)}
        onWidthChange={setSidebarWidth}
      />
      {sidebarOpen && <button className="sidebar-backdrop" type="button" aria-label={t('关闭导航遮罩')} onClick={() => setSidebarOpen(false)} />}

      <section className="workspace">
        {view === 'library' ? (
          <LibraryView
            onClose={() => changeView('chat')}
            onOpenConversation={(id, title) => { void openConversation({ id, title, priorMessages: [] }); }}
          />
        ) : view === 'shares' ? (
          <SharedConversationsView
            onClose={() => changeView('chat')}
            onOpenConversation={(id, title) => { void openConversation({ id, title, priorMessages: [], has_active_share: true }); }}
            onShareChanged={(ids, active) => setConversationHistory((items) => items.map((item) => ids.includes(item.id) ? { ...item, has_active_share: active } : item))}
          />
        ) : view === 'settings' ? (
          <SettingsView
            activeCategory={settingsCategory}
            onCategoryChange={setSettingsCategory}
            onClose={() => changeView('chat')}
            providerConfigs={providerConfigs}
            onProviderConfigsChange={setProviderConfigs}
          />
        ) : <>
        <section className="chat-topbar">
          <div className="chat-topbar-leading">
            <button className="mobile-sidebar-trigger" type="button" aria-label={t('打开导航')} onClick={() => setSidebarOpen(true)}><span /><span /><span /></button>
            <h1>{activeConversationTitle || 'Astra'}</h1>
          </div>
          {run && <div className="topbar-run-controls"><button type="button" onClick={() => setControlCenterOpen(true)}>{t('任务安全')}</button><span className={`status status-${run.status}`}>{statusLabel(run.status)}</span></div>}
        </section>

        <section className="chat-surface">
          <QuestionRail messages={messages} />
          <div className="conversation" ref={conversationRef} onScroll={handleConversationScroll}>
            {!messages.length && (
              <div className="welcome">
                <h2>{t('Navigate Ideas. Create Reality.')}</h2>
                <p>{t('今天想完成点什么？')}</p>
              </div>
            )}
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} run={run} processState={processState} processPanelDefaultOpen={processPanelDefaultOpen} processPanelOpenByRun={processPanelOpenByRun} onProcessPanelInitialize={initializeProcessPanel} onProcessPanelOpenChange={changeProcessPanelOpen} />
            ))}
            {answerSettling && streamingAnswer && <div className="answer-settling" role="status" aria-live="polite"><span className="settling-spinner" aria-hidden="true" />{t('正在整理并验证结果…')}</div>}
            {run && !terminalStatuses.has(run.status) && !streamingAnswer && !processState && (
              <div className="bubble assistant waiting-message" role="status" aria-live="polite">
                <span className="bubble-label">Astra</span>
                <span className="sr-only">{t(activeState(run))}</span>
                <div className="waiting-line" aria-hidden="true"><span className="thinking-orb"><i /><i /><i /></span></div>
              </div>
            )}
          </div>

          {showJumpToLatest && <button className="jump-latest-button" type="button" onClick={jumpToLatest}><span aria-hidden="true">↓</span>{t('回到最新')}</button>}
          <div className={`composer-dock ${run?.pending_approval ? 'has-approval' : ''}`}>
            {run?.pending_approval && (
              <ApprovalCard
                approval={run.pending_approval}
                submitting={approvalSubmitting}
                onDecision={(decision) => { void decideApproval(decision); }}
              />
            )}
            <form className={`chat-composer ${run?.pending_approval ? 'approval-pending' : ''}`} onSubmit={submit} onClick={(event) => {
            const target = event.target as HTMLElement;
            if (!target.closest('button, textarea, input, select, a, [role="button"]')) {
              goalInputRef.current?.focus({ preventScroll: true });
            }
          }}>
            <button
              className={`trusted-mode-toggle ${answerMode === 'trusted' ? 'active' : ''}`}
              type="button"
              role="switch"
              aria-checked={answerMode === 'trusted'}
              aria-label={t('可信模式')}
              onClick={toggleTrustedMode}
            >
              <Icon name="requestApprove" />
              <span>{answerMode === 'trusted' ? t('可信模式') : t('快速回答')}</span>
              <i aria-hidden="true"><b /></i>
            </button>
            <div className="composer-menu-wrap" ref={attachMenuRef}>
              <button
                className="composer-icon-button"
                type="button"
                aria-label={t('添加内容')}
                aria-expanded={attachOpen}
                aria-haspopup="menu"
                title={t('添加内容')}
                onClick={() => {
                  setAttachOpen((open) => !open);
                  setModelOpen(false);
                  setExecutionMenuOpen(false);
                }}
              >+</button>
              {attachOpen && (
                <div className="floating-menu attachment-menu">
                  <button type="button" disabled><span>↥</span><div><strong>{t('上传文件')}</strong><small>{t('即将支持')}</small></div></button>
                  <button type="button" disabled><span>▧</span><div><strong>{t('添加图片')}</strong><small>{t('即将支持')}</small></div></button>
                  <button type="button" disabled><span>⌁</span><div><strong>{t('连接来源')}</strong><small>{t('即将支持')}</small></div></button>
                </div>
              )}
            </div>
            <div className="execution-menu-wrap" ref={executionMenuRef}>
              <button className={`execution-mode-button mode-${executionMode}`} type="button" aria-expanded={executionMenuOpen} aria-haspopup="menu" onClick={() => {
                setExecutionMenuOpen((open) => !open);
                setAttachOpen(false);
                setModelOpen(false);
              }}>
                <Icon name={executionMode === 'plan' ? 'route' : executionMode === 'bypass' ? 'autoApprove' : 'requestApprove'} />
                <span>{executionMode === 'plan' ? t('仅规划') : executionMode === 'bypass' ? t('自动批准') : t('请求批准')}</span>
                <i className="execution-mode-chevron" aria-hidden="true" />
              </button>
              {executionMenuOpen && <ExecutionModeMenu value={executionMode} onChange={(mode) => {
                if (mode === 'bypass') setBypassConfirmOpen(true);
                else {
                  setExecutionMode(mode);
                  setExecutionMenuOpen(false);
                }
              }} />}
            </div>
            <textarea
              ref={goalInputRef}
              value={goal}
              disabled={Boolean(run?.pending_approval)}
              onChange={(event) => setGoal(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              placeholder={t('输入任务 / 继续追问...')}
            />
            <div className="model-menu-wrap" ref={modelMenuRef}>
              <button className="model-selector" type="button" aria-expanded={modelOpen} aria-haspopup="menu" aria-label={`${t('当前模型')}${language === 'zh-CN' ? '：' : ': '}${selectedModel || t('未配置模型')}`} onClick={() => {
                setModelOpen((open) => !open);
                setAttachOpen(false);
                setExecutionMenuOpen(false);
              }}>
                <span>{selectedModel || t('未配置模型')}</span><small>{answerMode === 'trusted' ? `${t(reasoningEffort)} · ${t('{count} 次工具').replace('{count}', String(toolCallLimit))} · ${reflectionEnabled ? `${t(reflectionTrigger)} ${t('反思')}` : t('反思关闭')}` : t('快速策略 · 工具按需')}</small><b>⌄</b>
              </button>
              {modelOpen && (
                <ModelMenu
                  selectedModelKey={selectedModelKey}
                  trusted={answerMode === 'trusted'}
                  onModelChange={setSelectedModelKey}
                  modelOptions={availableModels}
                  reasoningEffort={reasoningEffort}
                  onReasoningEffortChange={(value) => {
                    const effort = reasoningEffortValue(value);
                    persistConversationStrategy({ reasoning_effort: effort, max_tool_calls: toolLimitForEffort(effort, conversationStrategyRef.current.max_tool_calls) });
                  }}
                  toolCallLimit={toolCallLimit}
                  onToolCallLimitChange={(value) => persistConversationStrategy({ max_tool_calls: value })}
                  planningStrategy={planningStrategy}
                  onPlanningStrategyChange={(value) => persistConversationStrategy({ planning_strategy: value === '先规划' ? 'plan_first' : 'adaptive' })}
                  reflectionEnabled={reflectionEnabled}
                  onReflectionChange={(enabled) => persistConversationStrategy({ reflection_enabled: enabled })}
                  reflectionTrigger={reflectionTrigger}
                  onReflectionTriggerChange={(value) => persistConversationStrategy({ reflection_trigger: value === '失败时' ? 'failure_only' : value === '每轮' ? 'every_turn' : 'adaptive' })}
                  onOpenStrategyHelp={() => {
                    setModelOpen(false);
                    setStrategyHelpOpen(true);
                  }}
                />
              )}
            </div>
            {loading || stopping || (run && !terminalStatuses.has(run.status)) ? (
              <button className="send-button stop-button" type="button" aria-label={t('终止回答')} title={t('终止回答')} disabled={stopping} onClick={() => { void stopActiveRun(); }}>
                <span aria-hidden="true" />
              </button>
            ) : (
              <button className="send-button" type="submit" aria-label={t('发送')} disabled={!conversationStrategyReady || Boolean(run?.pending_approval)}>↑</button>
            )}
            </form>
          </div>
          {error && <ErrorDialog error={error} onClose={() => setError(null)} onRetry={error.retryable ? () => document.querySelector<HTMLFormElement>('.chat-composer')?.requestSubmit() : undefined} />}
        </section>

        </>}
      </section>
      {usageOpen && <UsageDashboard taskId={run?.task_id} runId={run?.id} onClose={() => setUsageOpen(false)} />}
      {controlCenterOpen && run && <ControlCenterDialog run={run} onClose={() => setControlCenterOpen(false)} />}
      {strategyHelpOpen && <StrategyHelpDialog onClose={() => setStrategyHelpOpen(false)} />}
      {bypassConfirmOpen && <BypassConfirmation onCancel={() => setBypassConfirmOpen(false)} onConfirm={() => {
        setExecutionMode('bypass');
        setExecutionMenuOpen(false);
        setBypassConfirmOpen(false);
      }} />}
      {conversationAction && <ConversationActionDialog action={conversationAction} onClose={() => setConversationAction(null)} onRenamed={(updated) => setConversationHistory((items) => items.map((item) => item.id === updated.id ? { ...item, title: updated.title, updated_at: updated.updated_at } : item))} onDeleted={(id) => {
        setConversationHistory((items) => items.filter((item) => item.id !== id));
        if (activeConversationId === id) startNewChat();
      }} onShareChanged={(id, active) => setConversationHistory((items) => items.map((item) => item.id === id ? { ...item, has_active_share: active } : item))} />}
    </main>
  );
}

function Sidebar({ open, collapsed, width, run, activeConversationId, conversations, activeView, onNewChat, onSelectConversation, onConversationAction, onTogglePin, onOpenSettings, onOpenShares, onOpenLibrary, onOpenUsage, onClose, onCollapse, onExpand, onWidthChange }: {
  open: boolean;
  collapsed: boolean;
  width: number;
  run: RunView | null;
  activeConversationId: string | null;
  conversations: ConversationEntry[];
  activeView: 'chat' | 'settings' | 'shares' | 'library';
  onNewChat: () => void;
  onSelectConversation: (conversation: ConversationEntry) => void;
  onConversationAction: (kind: 'rename' | 'share' | 'delete', conversation: ConversationEntry) => void;
  onTogglePin: (conversation: ConversationEntry) => void;
  onOpenSettings: () => void;
  onOpenShares: () => void;
  onOpenLibrary: () => void;
  onOpenUsage: () => void;
  onClose: () => void;
  onCollapse: () => void;
  onExpand: () => void;
  onWidthChange: (width: number) => void;
}) {
  const { t } = useI18n();
  const [menuId, setMenuId] = useState<string | null>(null);
  const menuRootRef = useRef<HTMLDivElement>(null);
  const resizeCleanupRef = useRef<(() => void) | null>(null);
  const pinned = conversations.filter((item) => item.pinned_at);
  const recent = conversations.filter((item) => !item.pinned_at);

  useEffect(() => () => resizeCleanupRef.current?.(), []);

  const beginResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const layout = event.currentTarget.closest('.app-layout') as HTMLElement | null;
    let liveWidth = startWidth;
    let frame: number | null = null;
    const paint = () => {
      layout?.style.setProperty('--sidebar-width', `${liveWidth}px`);
      frame = null;
    };
    const move = (moveEvent: PointerEvent) => {
      liveWidth = clampSidebarWidth(startWidth + moveEvent.clientX - startX);
      if (frame === null) frame = window.requestAnimationFrame(paint);
    };
    const finish = () => {
      if (frame !== null) window.cancelAnimationFrame(frame);
      layout?.style.setProperty('--sidebar-width', `${liveWidth}px`);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', finish);
      window.removeEventListener('pointercancel', finish);
      document.documentElement.classList.remove('sidebar-resizing');
      resizeCleanupRef.current = null;
      onWidthChange(liveWidth);
    };
    resizeCleanupRef.current?.();
    resizeCleanupRef.current = finish;
    document.documentElement.classList.add('sidebar-resizing');
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', finish);
    window.addEventListener('pointercancel', finish);
  };

  const resizeWithKeyboard = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const nextWidth = event.key === 'ArrowLeft' ? width - 16
      : event.key === 'ArrowRight' ? width + 16
        : event.key === 'Home' ? SIDEBAR_MIN_WIDTH
          : event.key === 'End' ? SIDEBAR_MAX_WIDTH
            : null;
    if (nextWidth === null) return;
    event.preventDefault();
    onWidthChange(clampSidebarWidth(nextWidth));
  };

  useEffect(() => {
    if (!menuId) return;
    const closeMenu = () => setMenuId(null);
    const closeWhenFocusLeaves = (event: PointerEvent | FocusEvent) => {
      if (event.target instanceof Node && !menuRootRef.current?.contains(event.target)) closeMenu();
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeMenu();
    };
    document.addEventListener('pointerdown', closeWhenFocusLeaves);
    document.addEventListener('focusin', closeWhenFocusLeaves);
    document.addEventListener('keydown', closeOnEscape);
    window.addEventListener('blur', closeMenu);
    return () => {
      document.removeEventListener('pointerdown', closeWhenFocusLeaves);
      document.removeEventListener('focusin', closeWhenFocusLeaves);
      document.removeEventListener('keydown', closeOnEscape);
      window.removeEventListener('blur', closeMenu);
    };
  }, [menuId]);

  const renderConversation = (conversation: ConversationEntry) => <div className={`history-item ${activeConversationId === conversation.id ? 'active' : ''}`} key={conversation.id} ref={menuId === conversation.id ? menuRootRef : undefined}>
    <button className="history-select" type="button" onClick={() => { setMenuId(null); onSelectConversation(conversation); }}><Icon name="message" /><span>{conversation.title ?? (conversation.run ? conversationTitle(conversation.run, t('当前 Web Agent 会话')) : t('未命名对话'))}</span></button>
    <button className="history-more" type="button" aria-label={`${t('更多操作')} ${conversation.title ?? ''}`} aria-expanded={menuId === conversation.id} aria-haspopup="menu" onClick={(event) => { event.stopPropagation(); setMenuId((current) => current === conversation.id ? null : conversation.id); }}>•••</button>
    {menuId === conversation.id && <div className="history-menu" role="menu">
      <button role="menuitem" type="button" onClick={() => { setMenuId(null); onConversationAction('rename', conversation); }}>{t('重命名')}</button>
      <button role="menuitem" type="button" onClick={() => { setMenuId(null); onTogglePin(conversation); }}>{conversation.pinned_at ? t('取消置顶') : t('置顶')}</button>
      <button role="menuitem" type="button" onClick={() => { setMenuId(null); onConversationAction('share', conversation); }}>{t('分享')}</button>
      <button className="danger" role="menuitem" type="button" onClick={() => { setMenuId(null); onConversationAction('delete', conversation); }}>{t('删除')}</button>
    </div>}
  </div>;
  return (
    <aside className={`sidebar ${open ? 'mobile-open' : ''}`}>
      <div className="brand">
        <AstraBrandIcon />
        <div className="brand-copy">
          <strong>Astra</strong>
          <span>Agent Console</span>
        </div>
        <button className="sidebar-collapse-trigger" type="button" aria-label={t('收起侧边栏')} onClick={onCollapse}><SidebarPanelIcon /></button>
        <button className="mobile-sidebar-close" type="button" aria-label={t('关闭导航')} onClick={onClose}>×</button>
      </div>

      {collapsed && <button className="sidebar-expand-trigger" type="button" aria-label={t('展开侧边栏')} title={t('展开侧边栏')} onClick={onExpand}><SidebarPanelIcon /></button>}

      <button className="new-chat-button" type="button" aria-label={t('新对话')} title={collapsed ? t('新对话') : undefined} onClick={onNewChat}>
        <span className="button-icon"><Icon name="plus" /></span>
        <span className="new-chat-label">{t('新对话')}</span>
      </button>

      <nav className="side-section">
        <div className="history-heading"><span className="side-title">{t('历史对话')}</span><small>{t('最多保留最近 {count} 个会话').replace('{count}', String(HISTORY_LIMIT))}</small></div>
        <div className="history-list">
          {pinned.length > 0 && <><span className="history-group-title">{t('置顶')}</span>{pinned.map(renderConversation)}</>}
          {recent.length > 0 && pinned.length > 0 && <span className="history-group-title">{t('最近')}</span>}
          {recent.map(renderConversation)}
          {!conversations.length && <div className="history-empty">{t('暂无对话')}</div>}
        </div>
      </nav>

      <div className="sidebar-bottom">
        <button className={`side-action sidebar-library-action ${activeView === 'library' ? 'active' : ''}`} type="button" aria-label={t('资料库')} title={collapsed ? t('资料库') : undefined} onClick={onOpenLibrary}>
          <Icon name="library" />
          <span>{t('资料库')}</span>
          <small>{t('全部文件')}</small>
        </button>
        <button className={`side-action sidebar-share-action ${activeView === 'shares' ? 'active' : ''}`} type="button" aria-label={t('已分享对话')} title={collapsed ? t('已分享对话') : undefined} onClick={onOpenShares}>
          <Icon name="link" />
          <span>{t('已分享对话')}</span>
          <small>{conversations.filter((item) => item.has_active_share).length}</small>
        </button>
        <button className="side-action sidebar-usage-action" type="button" aria-label={t('用量统计')} title={collapsed ? t('用量统计') : undefined} onClick={onOpenUsage}>
          <Icon name="chart" />
          <span>{t('用量统计')}</span>
          <small>{run?.tool_calls.length ?? 0} calls</small>
        </button>
        <button className={`side-action sidebar-settings-action ${activeView === 'settings' ? 'active' : ''}`} type="button" aria-label={t('设置')} title={collapsed ? t('设置') : undefined} onClick={onOpenSettings}>
          <Icon name="settings" />
          <span>{t('设置')}</span>
          <small>{t('本地配置')}</small>
        </button>
      </div>
      {!collapsed && <div
        className="sidebar-resizer"
        role="separator"
        aria-label={t('调整侧边栏宽度')}
        aria-orientation="vertical"
        aria-valuemin={SIDEBAR_MIN_WIDTH}
        aria-valuemax={SIDEBAR_MAX_WIDTH}
        aria-valuenow={width}
        tabIndex={0}
        onPointerDown={beginResize}
        onKeyDown={resizeWithKeyboard}
        onDoubleClick={() => onWidthChange(SIDEBAR_DEFAULT_WIDTH)}
      />}
    </aside>
  );
}

function SidebarPanelIcon() {
  return <svg className="sidebar-panel-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="2.5" y="3" width="15" height="14" rx="2.5" /><path d="M7 3v14" /></svg>;
}

function conversationTitle(run: RunView, fallback: string) {
  return run.summary?.trim() || run.chat_messages?.find((message) => message.role === 'user')?.content || fallback;
}

type LibraryGroupMode = 'time' | 'conversation' | 'type';
type LibrarySortMode = 'updated_desc' | 'updated_asc' | 'name_asc' | 'name_desc' | 'size_desc' | 'size_asc' | 'type_asc';
type LibraryViewMode = 'gallery' | 'list';

function libraryFileType(file: LibraryFile) {
  const mime = (file.mime_type ?? '').toLowerCase();
  const extension = file.path.split('.').pop()?.toLowerCase() ?? '';
  if (mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'heic'].includes(extension)) return '图片';
  if (mime.includes('sheet') || mime.includes('csv') || mime.includes('json') || ['csv', 'tsv', 'xls', 'xlsx', 'json', 'parquet'].includes(extension)) return '数据';
  if (['js', 'jsx', 'ts', 'tsx', 'py', 'java', 'go', 'rs', 'html', 'css', 'sql', 'sh', 'yaml', 'yml'].includes(extension)) return '代码';
  if (mime.includes('pdf') || mime.includes('word') || mime.startsWith('text/') || ['pdf', 'doc', 'docx', 'md', 'txt', 'rtf'].includes(extension)) return '文档';
  if (mime.startsWith('audio/') || mime.startsWith('video/') || ['mp3', 'wav', 'mp4', 'mov', 'webm'].includes(extension)) return '媒体';
  return '其他';
}

function libraryTimeGroup(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (date.getTime() >= startToday) return '今天';
  if (date.getTime() >= startToday - 6 * 24 * 60 * 60 * 1000) return '最近 7 天';
  return date.toLocaleDateString([], { year: 'numeric', month: 'long' });
}

function LibraryView({ onClose, onOpenConversation }: { onClose: () => void; onOpenConversation: (id: string, title: string) => void }) {
  const { t } = useI18n();
  const [files, setFiles] = useState<LibraryFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [groupMode, setGroupMode] = useState<LibraryGroupMode>('time');
  const [sortMode, setSortMode] = useState<LibrarySortMode>('updated_desc');
  const [viewMode, setViewMode] = useState<LibraryViewMode>('gallery');
  const [query, setQuery] = useState('');

  useEffect(() => {
    let active = true;
    void listLibraryFiles().then((items) => {
      if (active) setFiles(items);
    }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : t('资料库加载失败'));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [t]);

  const groups = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    const visible = files.filter((file) => !needle || `${file.path} ${file.conversation_title} ${file.mime_type ?? ''}`.toLocaleLowerCase().includes(needle));
    visible.sort((left, right) => {
      if (sortMode === 'updated_asc') return new Date(left.updated_at).getTime() - new Date(right.updated_at).getTime();
      if (sortMode === 'name_asc') return left.path.localeCompare(right.path, 'zh-CN', { numeric: true });
      if (sortMode === 'name_desc') return right.path.localeCompare(left.path, 'zh-CN', { numeric: true });
      if (sortMode === 'size_desc') return right.size_bytes - left.size_bytes;
      if (sortMode === 'size_asc') return left.size_bytes - right.size_bytes;
      if (sortMode === 'type_asc') return libraryFileType(left).localeCompare(libraryFileType(right), 'zh-CN') || left.path.localeCompare(right.path, 'zh-CN', { numeric: true });
      return new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime();
    });
    const grouped = new Map<string, LibraryFile[]>();
    for (const file of visible) {
      const key = groupMode === 'conversation' ? file.conversation_title || t('未命名对话') : groupMode === 'type' ? t(libraryFileType(file)) : t(libraryTimeGroup(file.updated_at));
      grouped.set(key, [...(grouped.get(key) ?? []), file]);
    }
    return [...grouped.entries()];
  }, [files, groupMode, query, sortMode, t]);

  return <section className="library-page">
    <header className="library-header">
      <div><span className="library-eyebrow">Astra Library</span><h2>{t('资料库')}</h2><p>{t('集中查看所有会话生成或保存的文档、图片和其他文件。')}</p></div>
      <button className="settings-close" type="button" aria-label={t('关闭资料库')} onClick={onClose}>×</button>
    </header>
    <div className="library-toolbar">
      <label className="library-search"><span className="sr-only">{t('搜索资料库')}</span><input value={query} onChange={(event) => setQuery(event.currentTarget.value)} placeholder={t('搜索文件或会话')} /></label>
      <div className="library-group-switch" aria-label={t('分类方式')}>
        {([['time', '时间'], ['conversation', '会话'], ['type', '类型']] as const).map(([value, label]) => <button type="button" aria-pressed={groupMode === value} key={value} onClick={() => setGroupMode(value)}>{t(label)}</button>)}
      </div>
      <div className="library-view-switch" aria-label={t('展示方式')}>
        <button type="button" aria-label={t('画廊视图')} aria-pressed={viewMode === 'gallery'} onClick={() => setViewMode('gallery')}><span className="gallery-view-icon" aria-hidden="true"><i /><i /><i /><i /></span><span>{t('画廊')}</span></button>
        <button type="button" aria-label={t('列表视图')} aria-pressed={viewMode === 'list'} onClick={() => setViewMode('list')}><span className="list-view-icon" aria-hidden="true"><i /><i /><i /></span><span>{t('列表')}</span></button>
      </div>
      <label className="library-sort"><span>{t('排序')}</span><select aria-label={t('资料库排序')} value={sortMode} onChange={(event) => setSortMode(event.currentTarget.value as LibrarySortMode)}>
        <option value="updated_desc">{t('最近更新')}</option><option value="updated_asc">{t('最早更新')}</option><option value="name_asc">{t('名称 A–Z')}</option><option value="name_desc">{t('名称 Z–A')}</option><option value="size_desc">{t('大小：从大到小')}</option><option value="size_asc">{t('大小：从小到大')}</option><option value="type_asc">{t('文件类型')}</option>
      </select></label>
    </div>
    <div className="library-summary"><strong>{files.length}</strong><span>{t('个文件')}</span>{query && <small>{t('当前显示 {count} 个').replace('{count}', String(groups.reduce((total, [, items]) => total + items.length, 0)))}</small>}</div>
    {loading ? <div className="library-state">{t('正在加载资料库…')}</div> : error ? <div className="library-state error">{error}</div> : !groups.length ? <div className="library-state"><strong>{query ? t('没有匹配的文件') : t('资料库还是空的')}</strong><span>{query ? t('尝试搜索其他名称、会话或文件类型。') : t('任务生成的文件会自动集中到这里。')}</span></div> : <div className={`library-groups view-${viewMode}`}>
      {groups.map(([label, items]) => <section className="library-group" key={label}><header><h3>{label}</h3><span>{items.length}</span></header><div className="library-grid">
        {items.map((file) => {
          const type = libraryFileType(file);
          const name = file.path.split('/').pop() || file.path;
          const previewableImage = type === '图片' && file.content_url;
          return <article className="library-card" key={file.id}>
            <div className={`library-preview type-${type}`} aria-hidden="true">{previewableImage ? <img src={file.content_url ?? ''} alt="" /> : <span>{file.path.split('.').pop()?.slice(0, 4).toUpperCase() || 'FILE'}</span>}</div>
            <div className="library-card-body"><strong title={file.path}>{name}</strong><span>{t(type)} · {formatFileSize(file.size_bytes)}</span><time dateTime={file.updated_at}>{new Date(file.updated_at).toLocaleString()}</time></div>
            <div className="library-card-actions"><button type="button" onClick={() => onOpenConversation(file.task_id, file.conversation_title)}>{file.conversation_title || t('未命名对话')}</button>{file.content_url ? <a href={file.content_url}>{t('打开')}</a> : <span>{t('受保护')}</span>}</div>
          </article>;
        })}
      </div></section>)}
    </div>}
  </section>;
}

function SharedConversationsView({ onClose, onOpenConversation, onShareChanged }: {
  onClose: () => void;
  onOpenConversation: (id: string, title: string) => void;
  onShareChanged: (ids: string[], active: boolean) => void;
}) {
  const { t } = useI18n();
  const [shares, setShares] = useState<ConversationShareSummary[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<'refresh' | 'revoke' | null>(null);
  const [message, setMessage] = useState('');

  const loadShares = useCallback(async () => {
    const items = await listConversationShares();
    setShares(items);
    setSelected((current) => new Set([...current].filter((id) => items.some((item) => item.conversation_id === id))));
  }, []);

  useEffect(() => {
    let active = true;
    void listConversationShares().then((items) => {
      if (active) setShares(items);
    }).catch(() => {
      if (active) setMessage(t('无法读取已分享对话'));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [t]);

  const selectedIds = [...selected];
  const allSelected = shares.length > 0 && selected.size === shares.length;
  const toggleSelected = (id: string) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  async function runBatch(action: 'refresh' | 'revoke') {
    if (!selectedIds.length || busy) return;
    if (action === 'revoke' && !window.confirm(t(`确定取消选中的 ${selectedIds.length} 个分享链接吗？获得链接的人将无法继续访问。`))) return;
    setBusy(action);
    setMessage('');
    const results = await Promise.allSettled(selectedIds.map((id) => action === 'refresh' ? createConversationShare(id, true) : revokeConversationShare(id)));
    const succeeded = selectedIds.filter((_, index) => results[index].status === 'fulfilled');
    const failed = selectedIds.length - succeeded.length;
    if (action === 'revoke' && succeeded.length) onShareChanged(succeeded, false);
    try {
      await loadShares();
    } catch {
      setMessage(t('操作已完成，但刷新分享列表失败'));
    }
    if (failed) setMessage(t(`${succeeded.length} 项成功，${failed} 项失败，请重试。`));
    else setMessage(action === 'refresh' ? t(`已更新 ${succeeded.length} 个分享快照。`) : t(`已取消 ${succeeded.length} 个分享链接。`));
    setBusy(null);
  }

  return <section className="shares-page">
    <header className="shares-header">
      <div><span>{t('对话管理')}</span><h1>{t('已分享对话')}</h1><p>{t('管理当前仍可访问的只读对话快照。')}</p></div>
      <button className="close-button" type="button" aria-label={t('关闭已分享对话')} onClick={onClose}>×</button>
    </header>
    <div className="shares-toolbar">
      <label><input type="checkbox" aria-label={t('全选已分享对话')} checked={allSelected} onChange={() => setSelected(allSelected ? new Set() : new Set(shares.map((item) => item.conversation_id)))} />{t('全选')}</label>
      <span>{t(`已选择 ${selected.size} 项`)}</span>
      <div>
        <button type="button" disabled={!selected.size || busy !== null} onClick={() => { void runBatch('refresh'); }}><Icon name="refresh" />{busy === 'refresh' ? t('更新中…') : t('更新快照')}</button>
        <button className="danger" type="button" disabled={!selected.size || busy !== null} onClick={() => { void runBatch('revoke'); }}>{busy === 'revoke' ? t('取消中…') : t('取消分享')}</button>
      </div>
    </div>
    {message && <p className="shares-message" role="status">{message}</p>}
    <div className="shares-list">
      {shares.map((share) => <article className="share-management-item" key={share.conversation_id}>
        <input type="checkbox" aria-label={`${t('选择')} ${share.title}`} checked={selected.has(share.conversation_id)} onChange={() => toggleSelected(share.conversation_id)} />
        <div className="share-management-main"><h2>{share.title}</h2><p>{t('快照更新时间')} {new Date(share.updated_at).toLocaleString()} · {share.message_count} {t('条消息')}</p></div>
        <div className="share-management-actions"><button type="button" onClick={() => onOpenConversation(share.conversation_id, share.title)}>{t('查看原对话')}</button><a href={share.url} target="_blank" rel="noreferrer">{t('打开分享页')}</a></div>
      </article>)}
      {!loading && !shares.length && <div className="shares-empty"><Icon name="link" /><h2>{t('暂无已分享对话')}</h2><p>{t('从对话的更多操作中创建分享链接后，会显示在这里。')}</p></div>}
      {loading && <div className="shares-empty"><p>{t('正在读取已分享对话…')}</p></div>}
    </div>
  </section>;
}

function ConversationActionDialog({ action, onClose, onRenamed, onDeleted, onShareChanged }: {
  action: { kind: 'rename' | 'share' | 'delete'; conversation: ConversationEntry };
  onClose: () => void;
  onRenamed: (conversation: ConversationSummary) => void;
  onDeleted: (id: string) => void;
  onShareChanged: (id: string, active: boolean) => void;
}) {
  const { t } = useI18n();
  const [title, setTitle] = useState(action.conversation.title ?? '');
  const [share, setShare] = useState<ConversationShare | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState('');
  useEffect(() => {
    if (action.kind === 'share' && action.conversation.has_active_share) {
      void createConversationShare(action.conversation.id).then(setShare).catch(() => setFailure(t('无法加载分享链接')));
    }
  }, [action, t]);
  const execute = async (operation: () => Promise<void>) => {
    setBusy(true); setFailure('');
    try { await operation(); } catch (error) { setFailure(error instanceof Error ? error.message : t('操作失败')); }
    finally { setBusy(false); }
  };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="conversation-action-dialog" role="dialog" aria-modal="true" aria-label={action.kind === 'rename' ? t('重命名对话') : action.kind === 'share' ? t('分享对话') : t('删除对话')} onKeyDown={(event) => { if (event.key === 'Escape') onClose(); }}>
      {action.kind === 'rename' && <form onSubmit={(event) => { event.preventDefault(); void execute(async () => { const updated = await updateConversation(action.conversation.id, { title }); onRenamed(updated); onClose(); }); }}><h2>{t('重命名对话')}</h2><input autoFocus value={title} maxLength={240} onChange={(event) => setTitle(event.target.value)} /><div className="dialog-actions"><button type="button" onClick={onClose}>{t('取消')}</button><button className="primary" disabled={busy || !title.trim()}>{t('保存')}</button></div></form>}
      {action.kind === 'delete' && <><h2>{t('永久删除对话？')}</h2><p>{t('将删除“{title}”的所有消息、执行记录和分享链接，此操作无法撤销。').replace('{title}', action.conversation.title ?? t('未命名对话'))}</p><div className="dialog-actions"><button type="button" onClick={onClose}>{t('取消')}</button><button className="danger" disabled={busy} type="button" onClick={() => { void execute(async () => { await deleteConversation(action.conversation.id); onDeleted(action.conversation.id); onClose(); }); }}>{t('永久删除')}</button></div></>}
      {action.kind === 'share' && <><h2>{t('分享对话')}</h2><p>{t('任何获得链接的人都可以查看创建时的只读快照。后续消息不会自动公开。')}</p>{share ? <><div className="share-link"><input readOnly value={`${window.location.origin}${share.url}`} /><button type="button" onClick={() => { void navigator.clipboard?.writeText(`${window.location.origin}${share.url}`); }}>{t('复制')}</button></div><small>{t('快照更新时间')} {new Date(share.updated_at).toLocaleString()}</small><div className="dialog-actions"><button type="button" onClick={() => { void execute(async () => { const updated = await createConversationShare(action.conversation.id, true); setShare(updated); }); }}>{t('更新快照')}</button><button className="danger" type="button" onClick={() => { void execute(async () => { await revokeConversationShare(action.conversation.id); setShare(null); onShareChanged(action.conversation.id, false); }); }}>{t('停止分享')}</button></div></> : <div className="dialog-actions"><button type="button" onClick={onClose}>{t('取消')}</button><button className="primary" type="button" onClick={() => { void execute(async () => { const created = await createConversationShare(action.conversation.id); setShare(created); onShareChanged(action.conversation.id, true); }); }}>{t('创建分享链接')}</button></div>}</>}
      {failure && <p className="dialog-error">{failure}</p>}
    </div>
  </div>;
}

function QuestionRail({ messages }: { messages: ChatMessage[] }) {
  const { t } = useI18n();
  const questions = messages.filter((message) => message.role === 'user');
  const latestQuestionId = questions.length ? questions[questions.length - 1].id : null;
  const [activeQuestionId, setActiveQuestionId] = useState<string | null>(latestQuestionId);
  useEffect(() => { setActiveQuestionId(latestQuestionId); }, [latestQuestionId]);
  if (!questions.length) return null;
  return <nav className="question-rail" aria-label={t('问题导航')}>{questions.map((question, index) => <button className={question.id === activeQuestionId ? 'active' : ''} type="button" key={question.id} aria-current={question.id === activeQuestionId ? 'true' : undefined} aria-label={`${t('跳转到问题')} ${index + 1}`} onClick={() => {
    setActiveQuestionId(question.id);
    const target = document.getElementById(`message-${question.id}`);
    if (typeof target?.scrollIntoView === 'function') target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }}><span /><div className="question-preview"><p>{question.content}</p></div></button>)}</nav>;
}

function CapabilityItem({ tool, busy, onChange }: { tool: ToolSetting; busy: boolean; onChange: (enabled: boolean) => void }) {
  const { t } = useI18n();
  const state = !tool.available ? tool.unavailable_reason ?? '当前不可用' : tool.enabled ? '已启用' : '已停用';
  return (
    <div className={`capability-item ${!tool.available ? 'unavailable' : ''}`}>
      <div>
        <strong>{t(tool.label)}</strong>
        <span>{t(tool.description)}</span>
        <small className={tool.available ? '' : 'capability-warning'}>{t(state)}</small>
      </div>
      <Toggle checked={tool.enabled} onChange={onChange} disabled={busy} label={`${t(tool.label)} · ${t(state)}`} />
    </div>
  );
}

function AstraBrandIcon() {
  const { t } = useI18n();
  const clickTimes = useRef<number[]>([]);
  const clearEffectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [burstId, setBurstId] = useState<number | null>(null);

  useEffect(() => () => {
    if (clearEffectTimer.current) clearTimeout(clearEffectTimer.current);
  }, []);

  function handleClick() {
    const now = Date.now();
    clickTimes.current = [...clickTimes.current.filter((time) => now - time < 1200), now];
    if (clickTimes.current.length < 5) return;

    clickTimes.current = [];
    setBurstId((current) => (current ?? 0) + 1);
    if (clearEffectTimer.current) clearTimeout(clearEffectTimer.current);
    clearEffectTimer.current = setTimeout(() => setBurstId(null), 1600);
  }

  return (
    <div className="brand-icon-wrap">
      <button className="brand-mark-trigger" type="button" aria-label={t('Astra 图标')} onClick={handleClick}>
        <img className="brand-mark" src="/astra.svg" alt="" />
      </button>
      {burstId !== null && (
        <div className="astra-burst" data-testid="astra-burst" key={burstId} aria-hidden="true">
          <span className="astra-core-flash" />
          <span className="astra-glow" />
          <span className="astra-orbit" />
          <span className="astra-orbit orbit-two" />
          <span className="astra-firework firework-one" />
          <span className="astra-firework firework-two" />
          <span className="astra-firework firework-three" />
          {Array.from({ length: 22 }, (_, index) => <i className={`astra-star star-${index + 1}`} key={index} />)}
        </div>
      )}
    </div>
  );
}

const settingCategories = ['模型管理', '工具', '运行时', '记忆', '界面', '数据与隐私'];
const settingCategoryIcons: Record<string, IconName> = {
  '模型管理': 'sparkle',
  '工具': 'tools',
  '运行时': 'terminal',
  '记忆': 'brain',
  '界面': 'palette',
  '数据与隐私': 'lock',
};

function SettingsView({ activeCategory, onCategoryChange, onClose, providerConfigs, onProviderConfigsChange }: {
  activeCategory: string;
  onCategoryChange: (category: string) => void;
  onClose: () => void;
  providerConfigs: ModelProviderConfig[];
  onProviderConfigsChange: (configs: ModelProviderConfig[]) => void;
}) {
  const { t } = useI18n();
  return (
    <section className="settings-page">
      <header className="settings-header">
        <div><span>{t('工作区')}</span><h1>{t('设置')}</h1></div>
        <button className="close-button" type="button" aria-label={t('关闭设置')} onClick={onClose}>×</button>
      </header>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label={t('设置类别')}>
          {settingCategories.map((category) => (
            <button className={category === activeCategory ? 'active' : ''} type="button" key={category} aria-current={category === activeCategory ? 'page' : undefined} onClick={() => onCategoryChange(category)}><Icon name={settingCategoryIcons[category]} /><span>{t(category)}</span></button>
          ))}
        </nav>
        <div className="settings-content">
          <SettingSection category={activeCategory} providerConfigs={providerConfigs} onProviderConfigsChange={onProviderConfigsChange} />
        </div>
      </div>
    </section>
  );
}

function SettingSection({ category, providerConfigs, onProviderConfigsChange }: { category: string; providerConfigs: ModelProviderConfig[]; onProviderConfigsChange: (configs: ModelProviderConfig[]) => void }) {
  const { language, setLanguage, t } = useI18n();
  const { mode, setMode } = useTheme();
  if (category === '模型管理') return <ModelManagement providers={providerConfigs} onChange={onProviderConfigsChange} />;
  if (category === '运行时') return <RuntimeSettings />;
  if (category === '工具') return <ToolSettings />;
  if (category === '记忆') return <SettingsGroup title="记忆" description="管理 Agent 在单次任务和不同对话之间保留的信息。"><SettingRow title="运行记忆" description="在当前任务中保留来源摘要和决策线索"><Toggle checked /></SettingRow><SettingRow title="跨对话记忆" description="在新对话中使用已确认的偏好与事实"><Toggle /></SettingRow><SettingRow title="写入阈值" description="仅保存高于该置信度的结构化记忆"><TranslatedSelect defaultValue="80" options={[['70', '70%'], ['80', '80%'], ['90', '90%']]} /></SettingRow><SettingRow title="记忆保留期" description="到期后自动清理非固定记忆"><TranslatedSelect defaultValue="30" options={[['7', `7 ${t('天')}`], ['30', `30 ${t('天')}`], ['forever', '永久']]} /></SettingRow></SettingsGroup>;
  if (category === '界面') return <SettingsGroup title="界面" description="调整工作区的信息密度和运行过程展示。"><SettingRow title="语言" description="选择界面显示语言"><select value={language} onChange={(event) => setLanguage(event.target.value as 'zh-CN' | 'en')}><option value="zh-CN">中文</option><option value="en">English</option></select></SettingRow><SettingRow title="主题模式" description="选择界面外观，或随操作系统自动切换"><select value={mode} onChange={(event) => setMode(event.target.value as 'system' | 'light' | 'dark')}><option value="system">{t('跟随系统')}</option><option value="light">{t('浅色模式')}</option><option value="dark">{t('暗色模式')}</option></select></SettingRow><SettingRow title="过程展示" description="在对话中显示工具调用和反思摘要"><Toggle checked /></SettingRow><SettingRow title="审计面板" description="任务完成后显示证据、事件和记忆"><Toggle checked /></SettingRow><SettingRow title="信息密度" description="控制对话和面板的间距"><TranslatedSelect defaultValue="compact" options={[['compact', '紧凑'], ['comfortable', '舒适']]} /></SettingRow></SettingsGroup>;
  if (category === '数据与隐私') return <SettingsGroup title="数据与隐私" description="控制任务记录、工具内容和诊断信息的保存方式。"><SettingRow title="保存运行记录" description="保留对话、工具调用元数据和验证报告"><Toggle checked /></SettingRow><SettingRow title="工具内容保留" description="决定是否保存工具返回的正文、文件内容或结构化结果"><TranslatedSelect defaultValue="metadata" options={[['none', '不保留内容'], ['metadata', '仅保留元数据'], ['full', '保留完整输出']]} /></SettingRow><SettingRow title="诊断日志" description="记录不包含工具内容的性能与错误信息"><Toggle checked /></SettingRow><button className="danger-button" type="button">{t('清除本地运行数据')}</button></SettingsGroup>;
  return null;
}

function ToolSettings() {
  const { t } = useI18n();
  const [tools, setTools] = useState<ToolSetting[]>([]);
  const [busyTool, setBusyTool] = useState<string | null>(null);
  const [message, setMessage] = useState('正在读取工具配置…');
  useEffect(() => {
    const controller = new AbortController();
    void getToolSettings(controller.signal).then((value) => {
      setTools(value.tools);
      setMessage('');
    }).catch((error) => {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      setMessage('无法读取工具配置');
    });
    return () => controller.abort();
  }, []);
  async function changeTool(name: ToolSetting['name'], enabled: boolean) {
    const previous = tools;
    const next = tools.map((tool) => tool.name === name ? { ...tool, enabled } : tool);
    setTools(next);
    setBusyTool(name);
    setMessage('');
    try {
      const saved = await updateToolSettings(next);
      setTools(saved.tools);
      setMessage(enabled ? '工具已启用，将用于之后新建的任务。' : '工具已停用，之后新建的任务不会调用它。');
    } catch {
      setTools(previous);
      setMessage('保存工具配置失败，已恢复原状态。');
    } finally {
      setBusyTool(null);
    }
  }
  return <SettingsGroup title="工具" description="管理 Agent 可用工具。修改会应用到之后新建的任务，运行中的任务不受影响。">
    <div className="capability-settings">
      {tools.map((tool) => <CapabilityItem key={tool.name} tool={tool} busy={busyTool !== null} onChange={(enabled) => void changeTool(tool.name, enabled)} />)}
      {!tools.length && <p className="tool-settings-message">{t(message)}</p>}
    </div>
    {tools.length > 0 && message && <p className="tool-settings-message" role="status">{t(message)}</p>}
    <p className="tool-settings-note">{t('开关保存在数据库中，服务重启后仍会保持当前状态。')}</p>
  </SettingsGroup>;
}

function RuntimeSettings() {
  const { t } = useI18n();
  const tr = (key: string, values: Record<string, string | number>) => Object.entries(values).reduce((text, [name, value]) => text.replace(`{${name}}`, String(value)), t(key));
  const [profile, setProfile] = useState<Awaited<ReturnType<typeof getRuntimeProfile>> | null>(null);
  const [dependencies, setDependencies] = useState<Array<{ id: string; name: string; version: string }>>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchInput, setBatchInput] = useState('');
  const [showBatch, setShowBatch] = useState(false);
  const [message, setMessage] = useState('');
  const [dirty, setDirty] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const nextDependencyId = useRef(0);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const building = profile?.build?.status === 'queued' || profile?.build?.status === 'building';
  const controlsDisabled = building || submitting;
  const makeDependency = (name = '', version = '') => ({ id: `dependency-${nextDependencyId.current++}`, name, version });
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const value = await getRuntimeProfile(controller.signal);
        if (!active) return;
        setProfile(value);
        setMessage((current) => current === '无法读取 Runtime 配置' ? '' : current);
        if (!dirtyRef.current) {
          setDependencies(value.dependencies.map((item) => ({ id: `saved-${item.name}`, name: item.name, version: item.version ?? '' })));
        }
        if (value.build?.status === 'failed') setDirty(true);
      } catch (error) {
        if (!active || (error instanceof DOMException && error.name === 'AbortError')) return;
        setMessage('无法读取 Runtime 配置');
      } finally {
        if (active) timer = window.setTimeout(refresh, building ? 1500 : 5000);
      }
    };
    void refresh();
    return () => {
      active = false;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [building]);
  function updateDependency(id: string, field: 'name' | 'version', value: string) {
    setDependencies((current) => current.map((item) => item.id === id ? { ...item, [field]: value } : item));
    setMessage('');
    setDirty(true);
  }
  function removeDependencies(ids: Set<string>) {
    setDependencies((current) => current.filter((item) => !ids.has(item.id)));
    setSelected(new Set());
    setMessage('');
    setDirty(true);
  }
  function addBatch() {
    try {
      const additions = batchInput.split(/\n+/).map((line) => line.trim()).filter(Boolean).map((line) => {
        const match = line.match(/^([A-Za-z0-9._-]+)(?:={1,2}([A-Za-z0-9.+-]+))?$/);
        if (!match) throw new Error(`格式错误：${line}`);
        return makeDependency(match[1], match[2] ?? '');
      });
      if (!additions.length) throw new Error('请输入至少一个依赖');
      setDependencies((current) => [...current, ...additions]);
      setBatchInput(''); setShowBatch(false); setMessage(''); setDirty(true);
    } catch (error) { setMessage(error instanceof Error ? error.message : '批量添加失败'); }
  }
  async function build() {
    try {
      const values = dependencies.map(({ name, version }) => ({ name: name.trim(), version: version.trim() }));
      if (values.some((item) => !item.name)) throw new Error('依赖名称不能为空');
      setMessage('');
      setSubmitting(true);
      const value = await buildRuntime(values);
      setProfile(value);
      setDirty(false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '构建请求失败');
    } finally {
      setSubmitting(false);
    }
  }
  async function cancelBuild() {
    if (!profile?.build?.id) return;
    try {
      setMessage('');
      setProfile(await cancelRuntimeBuild(profile.build.id));
    } catch (error) { setMessage(error instanceof Error ? error.message : '取消构建失败'); }
  }
  const buildStatus = profile?.build?.status ?? 'ready';
  const buildStatusLabel: Record<string, string> = { ready: '已就绪', queued: '等待构建', building: '构建中', succeeded: '构建成功', failed: '构建失败', cancelled: '已取消' };
  const buildProgress = Math.min(100, Math.max(0, profile?.build?.progress ?? (buildStatus === 'queued' ? 0 : 5)));
  return <SettingsGroup title="Docker 运行时" description="管理绘图工具使用的隔离镜像与 Python 依赖。只有构建阶段联网，工具执行始终断网。">
    <section className="runtime-overview" aria-label={t('Docker 运行状态')}>
      <div className="runtime-engine"><div><span>{t('运行引擎')}</span><strong><span className="runtime-health-dot" aria-hidden="true" />Docker Ready</strong><small>{t('一次性强化容器')}</small></div><span className={`runtime-status-badge runtime-status-${buildStatus}`}>{t(buildStatusLabel[buildStatus] ?? buildStatus)}</span></div>
      <div className="runtime-overview-details"><div><span>{t('当前镜像')}</span><strong>{profile?.active_image ?? t('读取中')}</strong></div><div><span>{t('依赖摘要')}</span><strong>{profile?.dependency_digest?.slice(0, 16) ?? t('基础依赖')}</strong></div></div>
      <div className="runtime-security-strip"><span>{t('断网执行')}</span><span>{t('只读根目录')}</span><span>{t('非 root')}</span><span>{t('资源受限')}</span></div>
    </section>
    <section className="runtime-dependencies" aria-labelledby="runtime-dependencies-title">
      <div className="runtime-dependency-heading"><div><strong id="runtime-dependencies-title">{t('Python 依赖管理')}</strong><span>{t('版本可留空，构建时将安装最新版本。核心绘图库由基础镜像锁定。')}</span></div></div>
      <div className="runtime-core-dependencies" aria-label={t('基础镜像核心依赖')}>
        <div className="runtime-core-heading"><div><strong>{t('核心依赖')}</strong><span>{t('随基础镜像提供，不允许修改或删除')}</span></div><span>{tr('{count} 项已锁定', { count: profile?.core_dependencies?.length ?? 0 })}</span></div>
        <div className="runtime-core-columns" aria-hidden="true"><span /><span>{t('依赖名称')}</span><span>{t('锁定版本')}</span><span>{t('状态')}</span></div>
        {(profile?.core_dependencies ?? []).map((item) => <div className="runtime-core-row" key={item.name}><span className="runtime-lock" aria-label={`${item.name} ${t('已锁定')}`}><Icon name="lock" /></span><strong>{item.name}</strong><code>{item.version}</code><span className="runtime-locked-badge">{t('已锁定')}</span></div>)}
      </div>
      <div className="runtime-custom-dependencies">
        <div className="runtime-custom-heading"><div><strong>{t('自定义依赖')}</strong><span>{t('可编辑、删除，并在下一次构建后生效')}</span></div><span>{tr('{count} 项', { count: dependencies.length })}</span></div>
        <div className="runtime-dependency-list">
          {dependencies.length > 0 && <><div className="runtime-dependency-toolbar"><label><input type="checkbox" aria-label={t('选择全部依赖')} checked={selected.size === dependencies.length} disabled={controlsDisabled} onChange={(event) => setSelected(event.target.checked ? new Set(dependencies.map((item) => item.id)) : new Set())} />{t('选择全部')}</label><button type="button" disabled={!selected.size || controlsDisabled} onClick={() => removeDependencies(selected)}>{t('删除所选')}{selected.size ? ` (${selected.size})` : ''}</button></div><div className="runtime-dependency-columns" aria-hidden="true"><span /><span>{t('依赖名称')}</span><span>{t('版本')}</span><span /></div></>}
          {dependencies.length === 0 ? <div className="runtime-dependency-empty"><strong>{t('尚未添加自定义依赖')}</strong><span>{t('可以添加额外的 Python 包扩展工具能力。')}</span></div> : dependencies.map((item) => { const name = item.name || t('未命名依赖'); return <div className="runtime-dependency-row" key={item.id}><input type="checkbox" aria-label={tr('选择 {name}', { name })} checked={selected.has(item.id)} disabled={controlsDisabled} onChange={(event) => setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(item.id); else next.delete(item.id); return next; })} /><input aria-label={t('依赖名称')} value={item.name} onChange={(event) => updateDependency(item.id, 'name', event.target.value)} placeholder={t('例如 polars')} disabled={controlsDisabled} /><input aria-label={tr('{name}版本', { name: item.name || t('依赖') })} value={item.version} onChange={(event) => updateDependency(item.id, 'version', event.target.value)} placeholder={t('最新版本')} disabled={controlsDisabled} /><button className="runtime-remove-dependency" type="button" aria-label={tr('删除 {name}', { name })} onClick={() => removeDependencies(new Set([item.id]))} disabled={controlsDisabled}>−</button></div>; })}
          <div className="runtime-dependency-add-actions"><button type="button" aria-label={t('添加依赖')} disabled={controlsDisabled} onClick={() => { setDependencies((current) => [...current, makeDependency()]); setMessage(''); setDirty(true); }}><span aria-hidden="true">+</span>{t('添加依赖')}</button><button type="button" disabled={controlsDisabled} aria-expanded={showBatch} onClick={() => setShowBatch((value) => !value)}>{t('批量添加')}</button></div>
        </div>
        {showBatch && <div className="runtime-batch-panel"><label htmlFor="runtime-batch-input">{t('每行一个依赖，可填写 `package` 或 `package==version`')}</label><textarea id="runtime-batch-input" rows={4} value={batchInput} onChange={(event) => setBatchInput(event.target.value)} placeholder={'polars==1.31.0\nopenpyxl'} spellCheck={false} disabled={controlsDisabled} /><div><button type="button" disabled={controlsDisabled} onClick={() => setShowBatch(false)}>{t('取消')}</button><button className="primary-button" type="button" disabled={controlsDisabled} onClick={addBatch}>{t('添加到列表')}</button></div></div>}
      </div>
      {building ? <div className="runtime-build-progress" role="status" aria-live="polite"><div className="runtime-build-progress-heading"><div><strong>{t(profile?.build?.phase ?? '准备构建')}</strong><span>{tr('{count} 个自定义依赖', { count: dependencies.length })}</span></div><b>{buildProgress}%</b></div><div className="runtime-progress-track" role="progressbar" aria-label={t('依赖构建进度')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={buildProgress}><span style={{ width: `${buildProgress}%` }} /></div><p>{t(profile?.build?.log ?? '正在等待构建输出')}</p><button className="secondary-button" type="button" onClick={() => void cancelBuild()}>{t('取消构建')}</button></div> : <div className="runtime-build-actions"><div><span>{tr('{count} 个自定义依赖', { count: dependencies.length })}{dirty ? ` · ${t('有未应用修改')}` : ''}</span>{profile?.build?.log && profile.build.status !== 'failed' && <small role="status">{t(profile.build.log)}</small>}</div><button className="primary-button" type="button" onClick={() => void build()} disabled={!dirty || submitting}>{t(submitting ? '正在提交…' : dirty ? '构建并激活' : '配置已同步')}</button></div>}
      {profile?.build?.status === 'failed' && profile.build.log && <p className="runtime-build-error" role="alert">{t(profile.build.log)}</p>}
      {message && <p className="runtime-build-error" role="alert">{message}</p>}
    </section>
  </SettingsGroup>;
}

type ModelProviderId =
  | 'openai' | 'anthropic' | 'google' | 'xai' | 'mistral' | 'groq' | 'openrouter'
  | 'together' | 'fireworks' | 'perplexity' | 'cohere' | 'cerebras' | 'nvidia'
  | 'huggingface' | 'azure' | 'deepseek' | 'qwen' | 'siliconflow' | 'moonshot'
  | 'zhipu' | 'minimax' | 'baidu' | 'tencent' | 'volcengine' | 'ollama'
  | 'lmstudio' | 'vllm' | 'localai' | 'compatible';
type ModelProviderGroup = 'global' | 'china' | 'local' | 'custom';
type ModelProviderConfig = {
  id: ModelProviderId;
  name: string;
  enabled: boolean;
  endpoint: string;
  models: string;
  apiKey: string;
};

const modelProviders: Array<{ id: ModelProviderId; name: string; detail: string; mark: string; group: ModelProviderGroup; protocol: string }> = [
  { id: 'openai', name: 'OpenAI', detail: 'OpenAI API', mark: 'O', group: 'global', protocol: 'OpenAI Chat Completions' },
  { id: 'anthropic', name: 'Anthropic', detail: 'Claude 原生 Messages API', mark: 'A', group: 'global', protocol: 'Anthropic Messages API' },
  { id: 'google', name: 'Google Gemini', detail: 'Gemini API', mark: 'G', group: 'global', protocol: 'OpenAI-compatible' },
  { id: 'xai', name: 'xAI', detail: 'Grok API', mark: 'x', group: 'global', protocol: 'OpenAI-compatible' },
  { id: 'mistral', name: 'Mistral AI', detail: 'La Plateforme', mark: 'M', group: 'global', protocol: 'OpenAI-compatible' },
  { id: 'groq', name: 'Groq', detail: '高速推理云', mark: 'G', group: 'global', protocol: 'OpenAI-compatible' },
  { id: 'openrouter', name: 'OpenRouter', detail: '多模型聚合平台', mark: 'OR', group: 'global', protocol: 'OpenAI-compatible' },
  { id: 'together', name: 'Together AI', detail: '开源模型推理平台', mark: 'T', group: 'global', protocol: 'OpenAI-compatible' },
  { id: 'fireworks', name: 'Fireworks AI', detail: '生成式 AI 推理平台', mark: 'F', group: 'global', protocol: 'OpenAI-compatible' },
  { id: 'perplexity', name: 'Perplexity', detail: 'Sonar 在线模型', mark: 'P', group: 'global', protocol: 'OpenAI-compatible' },
  { id: 'cohere', name: 'Cohere', detail: 'Command 系列模型', mark: 'C', group: 'global', protocol: 'OpenAI-compatible' },
  { id: 'cerebras', name: 'Cerebras', detail: '高速推理服务', mark: 'C', group: 'global', protocol: 'OpenAI-compatible' },
  { id: 'nvidia', name: 'NVIDIA NIM', detail: 'NVIDIA API Catalog', mark: 'N', group: 'global', protocol: 'OpenAI-compatible' },
  { id: 'huggingface', name: 'Hugging Face', detail: 'Inference Providers', mark: 'HF', group: 'global', protocol: 'OpenAI-compatible' },
  { id: 'azure', name: 'Azure OpenAI', detail: 'Azure AI Foundry', mark: 'Az', group: 'global', protocol: 'OpenAI-compatible v1' },
  { id: 'deepseek', name: 'DeepSeek', detail: 'DeepSeek 开放平台', mark: 'D', group: 'china', protocol: 'OpenAI-compatible' },
  { id: 'qwen', name: '通义千问', detail: '阿里云百炼', mark: 'Q', group: 'china', protocol: 'OpenAI-compatible' },
  { id: 'siliconflow', name: 'SiliconFlow', detail: '硅基流动模型广场', mark: 'S', group: 'china', protocol: 'OpenAI-compatible' },
  { id: 'moonshot', name: 'Moonshot AI', detail: 'Kimi 开放平台', mark: 'K', group: 'china', protocol: 'OpenAI-compatible' },
  { id: 'zhipu', name: '智谱 AI', detail: 'BigModel 开放平台', mark: 'Z', group: 'china', protocol: 'OpenAI-compatible' },
  { id: 'minimax', name: 'MiniMax', detail: 'MiniMax 开放平台', mark: 'M', group: 'china', protocol: 'OpenAI-compatible' },
  { id: 'baidu', name: '百度千帆', detail: '千帆大模型平台', mark: 'B', group: 'china', protocol: 'OpenAI-compatible' },
  { id: 'tencent', name: '腾讯混元', detail: '混元大模型 API', mark: 'T', group: 'china', protocol: 'OpenAI-compatible' },
  { id: 'volcengine', name: '火山方舟', detail: '豆包模型服务', mark: 'V', group: 'china', protocol: 'OpenAI-compatible' },
  { id: 'ollama', name: 'Ollama', detail: '本地模型运行时', mark: 'O', group: 'local', protocol: 'OpenAI-compatible' },
  { id: 'lmstudio', name: 'LM Studio', detail: '本地模型服务器', mark: 'LM', group: 'local', protocol: 'OpenAI-compatible' },
  { id: 'vllm', name: 'vLLM', detail: '高吞吐推理服务器', mark: 'vL', group: 'local', protocol: 'OpenAI-compatible' },
  { id: 'localai', name: 'LocalAI', detail: '本地 OpenAI 替代方案', mark: 'LA', group: 'local', protocol: 'OpenAI-compatible' },
  { id: 'compatible', name: 'OpenAI 兼容', detail: '自定义兼容端点', mark: '↗', group: 'custom', protocol: 'OpenAI-compatible' },
];

const providerDefaults: Record<ModelProviderId, { endpoint: string; models: string }> = {
  openai: { endpoint: 'https://api.openai.com/v1', models: 'gpt-5, gpt-5-mini' },
  anthropic: { endpoint: 'https://api.anthropic.com/v1', models: 'claude-sonnet-4-5, claude-haiku-4-5' },
  google: { endpoint: 'https://generativelanguage.googleapis.com/v1beta/openai', models: 'gemini-3.5-flash, gemini-3.5-pro' },
  xai: { endpoint: 'https://api.x.ai/v1', models: 'grok-4.5' },
  mistral: { endpoint: 'https://api.mistral.ai/v1', models: 'mistral-large-latest, mistral-small-latest' },
  groq: { endpoint: 'https://api.groq.com/openai/v1', models: 'openai/gpt-oss-120b, llama-3.3-70b-versatile' },
  openrouter: { endpoint: 'https://openrouter.ai/api/v1', models: 'openai/gpt-5, anthropic/claude-sonnet-4.5' },
  together: { endpoint: 'https://api.together.ai/v1', models: 'openai/gpt-oss-120b, meta-llama/Llama-3.3-70B-Instruct-Turbo' },
  fireworks: { endpoint: 'https://api.fireworks.ai/inference/v1', models: 'accounts/fireworks/models/llama-v3p3-70b-instruct' },
  perplexity: { endpoint: 'https://api.perplexity.ai', models: 'sonar, sonar-pro' },
  cohere: { endpoint: 'https://api.cohere.ai/compatibility/v1', models: 'command-a-plus-05-2026, command-a-03-2025' },
  cerebras: { endpoint: 'https://api.cerebras.ai/v1', models: 'gpt-oss-120b, llama3.1-8b' },
  nvidia: { endpoint: 'https://integrate.api.nvidia.com/v1', models: 'meta/llama-3.3-70b-instruct' },
  huggingface: { endpoint: 'https://router.huggingface.co/v1', models: 'openai/gpt-oss-120b' },
  azure: { endpoint: 'https://YOUR-RESOURCE.openai.azure.com/openai/v1', models: '' },
  deepseek: { endpoint: 'https://api.deepseek.com', models: 'deepseek-chat, deepseek-reasoner' },
  qwen: { endpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: 'qwen3.7-plus, qwen-plus' },
  siliconflow: { endpoint: 'https://api.siliconflow.cn/v1', models: 'deepseek-ai/DeepSeek-V3, Qwen/Qwen2.5-72B-Instruct' },
  moonshot: { endpoint: 'https://api.moonshot.cn/v1', models: 'kimi-k2.5, kimi-k2-turbo-preview' },
  zhipu: { endpoint: 'https://open.bigmodel.cn/api/paas/v4', models: 'glm-5, glm-4.5' },
  minimax: { endpoint: 'https://api.minimaxi.com/v1', models: 'MiniMax-M2.5, MiniMax-M2.1' },
  baidu: { endpoint: 'https://qianfan.baidubce.com/v2', models: 'ernie-4.5-8k-preview' },
  tencent: { endpoint: 'https://api.hunyuan.cloud.tencent.com/v1', models: 'hunyuan-turbos-latest' },
  volcengine: { endpoint: 'https://ark.cn-beijing.volces.com/api/v3', models: '' },
  ollama: { endpoint: 'http://127.0.0.1:11434/v1', models: 'qwen3, llama3.2' },
  lmstudio: { endpoint: 'http://127.0.0.1:1234/v1', models: '' },
  vllm: { endpoint: 'http://127.0.0.1:8001/v1', models: '' },
  localai: { endpoint: 'http://127.0.0.1:8080/v1', models: '' },
  compatible: { endpoint: 'http://127.0.0.1:11434/v1', models: '' },
};

const initialProviderConfigs: ModelProviderConfig[] = modelProviders.map((provider) => ({
  id: provider.id,
  name: provider.name,
  enabled: provider.id === 'openai',
  ...providerDefaults[provider.id],
  apiKey: '',
}));

function localStorageOrNull(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

function readLocalJson<T>(key: string): T | null {
  try {
    const value = localStorageOrNull()?.getItem(key);
    return value ? JSON.parse(value) as T : null;
  } catch {
    return null;
  }
}

function writeLocalJson(key: string, value: unknown) {
  try {
    localStorageOrNull()?.setItem(key, JSON.stringify(value));
  } catch { /* storage may be disabled or full */ }
}

function readLocalString(key: string) {
  try {
    return localStorageOrNull()?.getItem(key) ?? '';
  } catch {
    return '';
  }
}

function writeLocalString(key: string, value: string) {
  try {
    localStorageOrNull()?.setItem(key, value);
  } catch { /* storage may be disabled or full */ }
}

function clampSidebarWidth(width: number) {
  return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, width));
}

function loadSidebarWidth() {
  const saved = readLocalJson<number>(STORAGE_KEYS.sidebarWidth);
  return typeof saved === 'number' && Number.isFinite(saved) ? clampSidebarWidth(saved) : SIDEBAR_DEFAULT_WIDTH;
}

function loadProviderConfigs(): ModelProviderConfig[] {
  const saved = readLocalJson<ModelProviderConfig[]>(STORAGE_KEYS.modelProviders);
  if (!Array.isArray(saved)) return initialProviderConfigs;
  return initialProviderConfigs.map((defaults) => {
    const configured = saved.find((item) => item?.id === defaults.id);
    return configured ? { ...defaults, ...configured, id: defaults.id, name: defaults.name } : defaults;
  });
}

function loadConversationHistory(): ConversationEntry[] {
  const saved = readLocalJson<ConversationEntry[]>(STORAGE_KEYS.conversations);
  if (!Array.isArray(saved)) return [];
  return saved
    .filter((item) => item && typeof item.id === 'string' && item.run && typeof item.run.id === 'string' && Array.isArray(item.priorMessages))
    .map((item) => ({
      ...item,
      run: normalizeRunView(item.run!),
      priorMessages: item.priorMessages.map((message) => ({ ...message, metadata: message.metadata ?? {} })),
    }))
    .slice(0, HISTORY_LIMIT);
}

function loadProcessPanelDefaultOpen(): boolean {
  return readLocalJson<unknown>(STORAGE_KEYS.processPanelDefaultOpen) === true;
}

function parseModelIds(models: string) {
  return [...new Set(models.split(',').map((model) => model.trim()).filter(Boolean))];
}

function ModelManagement({ providers, onChange }: { providers: ModelProviderConfig[]; onChange: (providers: ModelProviderConfig[]) => void }) {
  const { t } = useI18n();
  const [selectedProvider, setSelectedProvider] = useState<ModelProviderId>('openai');
  const [showKey, setShowKey] = useState(false);
  const [query, setQuery] = useState('');
  const provider = providers.find((item) => item.id === selectedProvider)!;
  const providerMeta = modelProviders.find((item) => item.id === selectedProvider)!;
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleProviders = normalizedQuery
    ? modelProviders.filter((item) => `${item.name} ${item.detail} ${item.id}`.toLocaleLowerCase().includes(normalizedQuery))
    : modelProviders;
  const providerGroups: Array<{ id: ModelProviderGroup; label: string }> = [
    { id: 'global', label: '全球服务' },
    { id: 'china', label: '中国大陆' },
    { id: 'local', label: '本地运行时' },
    { id: 'custom', label: '自定义' },
  ];

  function selectProvider(id: ModelProviderId) {
    setSelectedProvider(id);
    setShowKey(false);
  }

  function updateProvider(patch: Partial<ModelProviderConfig>) {
    onChange(providers.map((item) => item.id === selectedProvider ? { ...item, ...patch } : item));
  }

  function toggleProvider() {
    updateProvider({ enabled: !provider.enabled });
  }

  return (
    <SettingsGroup title="模型管理" description="配置模型供应商连接、凭据和 Agent 可选模型。">
      <div className="provider-workspace">
        <aside className="provider-list" aria-label={t('模型供应商')}>
          <div className="provider-list-heading"><span>{t('供应商')}</span><button type="button" aria-label={t('添加供应商')} title={t('添加供应商')} onClick={() => selectProvider('compatible')}>+</button></div>
          <label className="provider-search"><span aria-hidden="true">⌕</span><input aria-label={t('搜索供应商')} placeholder={t('搜索供应商')} value={query} onChange={(event) => setQuery(event.target.value)} /></label>
          <div className="provider-list-scroll">
            {providerGroups.map((group) => {
              const items = visibleProviders.filter((item) => item.group === group.id);
              return items.length > 0 && <div className="provider-group" key={group.id}><div className="provider-group-label">{t(group.label)}</div>{items.map((item) => (
                <button className={`provider-item ${item.id === selectedProvider ? 'active' : ''}`} type="button" key={item.id} onClick={() => selectProvider(item.id)}>
                  <span className={`provider-mark provider-${item.id}`}>{item.mark}</span>
                  <span><strong>{item.name}</strong><small>{t(item.detail)}</small></span>
                  <i className={providers.find((configured) => configured.id === item.id)?.enabled ? 'connected' : ''} />
                </button>
              ))}</div>;
            })}
            {visibleProviders.length === 0 && <p className="provider-empty">{t('没有匹配的供应商')}</p>}
          </div>
        </aside>

        <section className="provider-editor">
          <header className="provider-editor-header">
            <div><span className={`provider-mark provider-${provider.id}`}>{providerMeta.mark}</span><div><h3>{provider.name}</h3><p>{t(providerMeta.detail)}</p></div></div>
            <label className="provider-enabled"><span>{t('启用')}</span><Toggle checked={provider.enabled} onChange={toggleProvider} /></label>
          </header>

          <div className="provider-form">
            <label><span>{t('API 地址')}</span><small>{t('供应商 API 的基础地址')}</small><input value={provider.endpoint} onChange={(event) => updateProvider({ endpoint: event.target.value })} spellCheck={false} /></label>
            <label><span>{t('API Key')}</span><small>{t('凭据保存在当前浏览器本地，不会写入运行记录')}</small><div className="secret-input"><input type={showKey ? 'text' : 'password'} value={provider.apiKey} onChange={(event) => updateProvider({ apiKey: event.target.value })} placeholder="sk-..." autoComplete="off" /><button type="button" onClick={() => setShowKey((visible) => !visible)}>{t(showKey ? '隐藏' : '显示')}</button></div></label>
            <label><span>{t('可用模型 ID')}</span><small>{t('使用逗号分隔，模型选择器将使用这些标识')}</small><textarea value={provider.models} onChange={(event) => updateProvider({ models: event.target.value })} placeholder="model-id-1, model-id-2" /></label>
          </div>

          <div className="provider-advanced">
            <div><strong>{t('请求协议')}</strong><small>{providerMeta.protocol}</small></div>
          </div>

          <footer className="provider-actions">
            <span>{t('更改会自动保存到当前浏览器。')}</span>
          </footer>
        </section>
      </div>
    </SettingsGroup>
  );
}

function TranslatedSelect({ defaultValue, options }: { defaultValue: string; options: Array<string | [string, string]> }) {
  const { t } = useI18n();
  return <select defaultValue={defaultValue}>{options.map((option) => { const [value, label] = Array.isArray(option) ? option : [option, option]; return <option value={value} key={value}>{t(label)}</option>; })}</select>;
}

function SettingsGroup({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  const { t } = useI18n();
  return <section className="settings-group"><header><h2>{t(title)}</h2><p>{t(description)}</p></header>{children}</section>;
}

function SettingRow({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  const { t } = useI18n();
  return <div className="setting-row"><div><strong>{t(title)}</strong><span>{t(description)}</span></div>{children}</div>;
}

function Toggle({ checked = false, onChange, disabled = false, label }: { checked?: boolean; onChange?: (checked: boolean) => void; disabled?: boolean; label?: string }) {
  const [localChecked, setLocalChecked] = useState(checked);
  const value = onChange ? checked : localChecked;
  return <button className={`toggle ${value ? 'on' : ''}`} type="button" role="switch" aria-checked={value} aria-label={label} disabled={disabled} onClick={() => onChange ? onChange(!value) : setLocalChecked(!value)}><span /></button>;
}

function ExecutionModeMenu({ value, onChange }: { value: 'plan' | 'default' | 'bypass'; onChange: (mode: 'plan' | 'default' | 'bypass') => void }) {
  const { t } = useI18n();
  const modes = [
    { id: 'plan' as const, title: '仅规划', detail: '可执行查询与临时计算，不执行持久化或外部副作用', icon: 'route' as const },
    { id: 'default' as const, title: '请求批准', detail: '无副作用行为自动执行，危险行为按影响范围确认', icon: 'requestApprove' as const },
    { id: 'bypass' as const, title: '自动批准', detail: '跳过可批准行为的确认，平台禁止项仍不可执行', icon: 'autoApprove' as const },
  ];
  return <div className="floating-menu execution-menu"><div className="menu-heading">{t('执行模式')}</div>{modes.map((mode) => <button className={value === mode.id ? 'selected' : ''} type="button" key={mode.id} onClick={() => onChange(mode.id)}><Icon name={mode.icon} /><div><strong>{t(mode.title)}</strong><small>{t(mode.detail)}</small></div><span className="mode-selected-mark">{value === mode.id ? '✓' : ''}</span></button>)}</div>;
}

function approvalResourceLabel(resource: string) {
  const workspaceMatch = resource.match(/^task:\/\/[^/]+\/workspace\/(.+)$/);
  if (workspaceMatch) return workspaceMatch[1];
  if (resource.startsWith('network://')) return '外部网络';
  if (resource.startsWith('external://')) return '外部服务';
  return '';
}

function friendlyApprovalContent(approval: PendingApproval) {
  const effects = new Set(approval.effect_kinds || []);
  const resources = (approval.affected_resources || []).map(approvalResourceLabel).filter(Boolean);
  const target = resources[0];
  if (effects.has('workspace_delete')) {
    return {
      title: target ? `删除 ${target}` : '删除任务文件',
      description: '删除后文件将不再出现在任务工作区中。',
      risk: '会删除文件',
      resources,
    };
  }
  if (effects.has('workspace_write') || effects.has('artifact_write')) {
    return {
      title: target ? `保存 ${target}` : '保存任务文件',
      description: '文件会保存在当前任务中，后续工具可以继续使用。',
      risk: '会创建或修改文件',
      resources,
    };
  }
  if (effects.has('external_write') || effects.has('network_write')) {
    return {
      title: target ? `修改 ${target}` : '修改外部内容',
      description: '这项操作会向任务工作区之外发送数据或修改外部系统。',
      risk: '会影响外部系统',
      resources,
    };
  }
  if (effects.has('credential_use') || effects.has('sensitive_data_read')) {
    return {
      title: effects.has('credential_use') ? '使用已连接的账户' : '读取敏感信息',
      description: 'Astra 只会在本次操作所需的最小范围内使用这些信息。',
      risk: effects.has('credential_use') ? '会使用账户权限' : '包含敏感信息',
      resources,
    };
  }
  if (effects.has('process_execute_unknown')) {
    return {
      title: '运行一项未完全识别的操作',
      description: 'Astra 无法可靠判断它是否会修改文件或环境。',
      risk: '影响范围不确定',
      resources,
    };
  }
  return {
    title: approval.action_summary || '执行这项操作',
    description: 'Astra 需要你的确认后才能继续。',
    risk: approval.impact === 'high' ? '风险较高' : '需要确认',
    resources,
  };
}

function ApprovalCard({ approval, submitting, onDecision }: { approval: PendingApproval; submitting: boolean; onDecision: (decision: 'approve_once' | 'allow_similar' | 'allow_task' | 'reject') => void }) {
  const { t } = useI18n();
  const content = friendlyApprovalContent(approval);
  return <section className="approval-card" role="group" aria-label={t('需要你的确认')}>
    <div className="approval-card-header">
      <Icon name="requestApprove" />
      <div><strong>{t('需要你的确认')}</strong><span>{t(content.title)}</span></div>
    </div>
    <p className="approval-friendly-description">{t(content.description)}</p>
    {approval.tool_name === 'bash_execute' && approval.preview && <div className="approval-command">
      <span>{t('将执行的命令')}</span>
      <pre>{approval.preview}</pre>
    </div>}
    <div className="approval-friendly-summary">
      <span className="approval-risk-pill">{t(content.risk)}</span>
      {content.resources.slice(0, 3).map((resource) => <span className="approval-resource-pill" key={resource}>{resource}</span>)}
    </div>
    <div className="approval-actions">
      <button type="button" disabled={submitting} onClick={() => onDecision('approve_once')}>{t('允许这次')}</button>
      {approval.decisions.includes('allow_similar') && <button type="button" disabled={submitting} onClick={() => onDecision('allow_similar')}>{t('当前运行内允许')}</button>}
      {approval.decisions.includes('allow_task') && <button type="button" disabled={submitting} onClick={() => onDecision('allow_task')}>{t('当前任务内允许')}</button>}
      <button className="approval-reject" type="button" disabled={submitting} onClick={() => onDecision('reject')}>{t('拒绝')}</button>
    </div>
    {submitting && <span className="approval-submitting" role="status">{t('正在提交批准决定…')}</span>}
  </section>;
}

function permissionToolLabel(toolName: string) {
  const labels: Record<string, string> = {
    bash_execute: '命令工具',
    chart_render: '图表工具',
    web_search: '网页搜索',
    web_fetch: '网页读取',
  };
  return labels[toolName] || toolName.replace(/_/g, ' ');
}

function permissionEffectLabel(effectKind: string) {
  const labels: Record<string, string> = {
    workspace_read: '读取任务文件',
    workspace_write: '创建或修改任务文件',
    workspace_delete: '删除任务文件',
    network_read: '读取公开网络内容',
    network_write: '向外部服务发送或修改数据',
    process_execute: '执行程序',
    process_execute_unknown: '执行未完全识别的程序',
    command_execute: '执行命令',
    sensitive_data_read: '读取敏感数据',
    credential_use: '使用服务凭据',
    external_write: '修改外部系统',
  };
  return labels[effectKind] || effectKind.replace(/_/g, ' ');
}

function permissionScopeLabel(scope: string) {
  return scope === 'task' ? '当前任务持续有效' : scope === 'run' ? '仅当前运行有效' : '临时授权';
}

function identityTypeLabel(type: string) {
  const labels: Record<string, string> = {
    main_agent: '主 Agent',
    reviewer: '审批用户',
    tool_provider: '工具提供方',
    tool_runtime: '工具运行时',
    subagent: '子 Agent',
  };
  return labels[type] || type.replace(/_/g, ' ');
}

function trustLevelLabel(level: string) {
  return level === 'platform' ? '平台内置' : level === 'user' ? '当前用户' : level === 'managed' ? '受管理' : level;
}

function identityRoleDescription(identity: IdentityGroup) {
  const descriptions: Record<string, string> = {
    main_agent: '理解任务并发起工具调用',
    subagent: '在授权范围内处理委派任务',
    tool_provider: '声明并提供受信任工具',
    external_provider: '提供外部扩展工具',
    tool_runtime: '以最小权限执行具体工具',
    reviewer: '确认或拒绝需要审批的操作',
  };
  return descriptions[identity.type] || '参与本次运行的审计主体';
}

function formatAuditTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function readableResourceMatcher(matcher: Record<string, unknown>) {
  const values = Object.values(matcher).flatMap((value) => Array.isArray(value) ? value : [value]).filter((value) => typeof value === 'string') as string[];
  if (!values.length) return '';
  return values.slice(0, 2).map((value) => value.replace(/^task:\/\/[^/]+\/workspace\//, '工作区/')).join('、');
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ControlCenterDialog({ run, onClose }: { run: RunView; onClose: () => void }) {
  const { t } = useI18n();
  const [permissions, setPermissions] = useState<PermissionCenterView | null>(null);
  const [loadError, setLoadError] = useState('');
  const refresh = useCallback(async () => {
    try {
      const permissionView = await getPermissionCenter(run.id);
      setPermissions(permissionView);
      setLoadError('');
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : t('加载安全信息失败'));
    }
  }, [run.id, t]);
  useEffect(() => { void refresh(); }, [refresh]);
  const activeGrants = permissions?.grants.filter((grant) => grant.status === 'active') ?? [];
  const identityPresentation = buildIdentityPresentation(permissions?.identities ?? [], run.id);
  const auditEntries = buildAuditLog(permissions?.policy_explanations ?? []);
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="control-center-modal" role="dialog" aria-modal="true" aria-label={t('任务安全')} onMouseDown={(event) => event.stopPropagation()}>
      <header><div><h2>{t('任务安全')}</h2><p>{t('管理 Astra 在这个任务中可以继续执行的操作，并查看安全审计记录。')}</p></div><button type="button" aria-label={t('关闭')} onClick={onClose}>×</button></header>
      {loadError && <p className="control-center-error">{loadError}</p>}
      {!permissions ? <p>{t('正在加载…')}</p> : <>
        <div className="control-center-overview security-only" aria-label={t('任务安全概览')}>
          <div><strong>{activeGrants.length}</strong><span>{t('项有效授权')}</span><small>{activeGrants.length ? t('可随时撤销') : t('危险操作仍会询问')}</small></div>
          <div><strong>{permissions.credentials.filter((item) => !item.revoked_at).length}</strong><span>{t('项凭据使用')}</span><small>{permissions.credentials.some((item) => !item.revoked_at) ? t('均为短期受限凭据') : t('未使用外部服务凭据')}</small></div>
        </div>

        <div className="control-center-primary security-only">
          <section className="control-center-panel grants-panel">
            <div className="control-center-section-heading"><div><h3>{t('允许的操作')}</h3><p>{t('这些操作在有效范围内再次发生时，不会重复询问。')}</p></div></div>
            {activeGrants.length ? <div className="permission-grant-list">{activeGrants.map((grant) => {
              const effects = grant.effect_kinds.length ? grant.effect_kinds.map(permissionEffectLabel) : [];
              const resource = readableResourceMatcher(grant.resource_matcher);
              return <article className="permission-grant-card" key={grant.id}>
                <div className="permission-grant-main">
                  <div className="permission-grant-icon" aria-hidden="true">✓</div>
                  <div><strong>{effects.length ? `${permissionToolLabel(grant.tool_name)}可以${effects.join('、')}` : `${permissionToolLabel(grant.tool_name)}的有限操作`}</strong>
                    <div className="permission-grant-badges"><span>{t(permissionScopeLabel(grant.scope))}</span>{grant.effect_kinds.length === 0 && <span className="legacy-note">{t('兼容旧授权')}</span>}</div>
                  </div>
                </div>
                <div className="permission-grant-details">
                  {resource && <span><b>{t('适用范围')}</b>{resource}</span>}
                  <span><b>{t('使用情况')}</b>{t('已使用 {count} 次').replace('{count}', String(grant.use_count))}{grant.max_uses ? ` / ${grant.max_uses}` : ''}</span>
                  {grant.expires_at && <span><b>{t('有效期')}</b>{new Date(grant.expires_at).toLocaleString()}</span>}
                  {!grant.effect_kinds.length && <p>{t('这条授权来自旧版本；范围不明确时 Astra 仍会再次询问。')}</p>}
                </div>
                <button className="permission-revoke-button" type="button" onClick={async () => { await revokePermissionGrant(grant.id); await refresh(); }}>{t('撤销授权')}</button>
              </article>;
            })}</div> : <div className="control-center-empty"><strong>{t('没有持续授权')}</strong><span>{t('Astra 遇到写文件、删除或外部修改等危险操作时会再次询问你。')}</span></div>}
          </section>

          <section className="control-center-panel permission-activity-panel">
            <div className="control-center-section-heading"><div><h3>{t('最近的安全活动')}</h3><p>{t('用自然语言说明最近为什么询问、允许或阻止操作。')}</p></div></div>
            {auditEntries.length ? <div className="permission-activity-list">{auditEntries.slice(0, 4).map((entry) => <div className="permission-activity-item" key={entry.id}><span className={`permission-activity-dot tone-${entry.tone}`} /><div><strong>{t(entry.title)}</strong><span>{entry.actor} · {formatAuditTime(entry.createdAt)}</span></div></div>)}</div> : <div className="control-center-empty compact"><span>{t('暂无需要说明的权限活动')}</span></div>}
          </section>

        </div>

        <details className="control-center-technical">
          <summary><span>{t('技术与审计详情')}</span><small>{t('身份链、工具来源、凭据和内部决策记录')}</small></summary>
          <div className="control-center-technical-grid">
            <section className="identity-chain-section"><div className="technical-section-heading"><div><h3>{t('本次运行身份链')}</h3><p>{t('从任务协调者到具体工具运行时的责任边界。')}</p></div><span>{identityPresentation.execution.length}</span></div>
              {identityPresentation.execution.length ? <div className="identity-chain">{identityPresentation.execution.map((identity, index) => <div className="identity-chain-step" key={`${identity.type}:${identity.principal}:${identity.parent_identity_id || ''}`}>
                {index > 0 && <span className="identity-chain-connector" aria-hidden="true">↓</span>}
                <div className={`identity-role-icon identity-${identity.type}`} aria-hidden="true">{index + 1}</div>
                <div className="identity-role-main"><strong>{t(identityTypeLabel(identity.type))}{identity.count > 1 && <small>×{identity.count}</small>}</strong><code>{identity.principal}</code><span>{t(identityRoleDescription(identity))}</span></div>
                <span className="identity-trust-badge">{t(trustLevelLabel(identity.trust_level))}</span>
              </div>)}</div> : <div className="control-center-empty compact"><span>{t('本次运行尚未登记执行身份')}</span></div>}
              {identityPresentation.reviewers.length > 0 && <div className="identity-reviewers"><span>{t('审批主体')}</span>{identityPresentation.reviewers.map((identity) => <div key={identity.id}><strong>{identity.principal}</strong><small>{t(trustLevelLabel(identity.trust_level))} · {t(identityRoleDescription(identity))}</small></div>)}</div>}
              {identityPresentation.historicalCount > 0 && <div className="identity-history-note">{t('已折叠当前任务其他运行的 {count} 条身份记录').replace('{count}', String(identityPresentation.historicalCount))}</div>}
            </section>
            <section><h3>{t('工具与凭据')}</h3>{permissions.credentials.map((credential) => <div className="control-center-item" key={credential.id}><strong>{credential.service}</strong><span>{credential.scopes.join(', ') || t('最小权限')} · {credential.revoked_at ? t('已撤销') : t('短期有效')}</span></div>)}{!permissions.credentials.length && <p>{t('未使用服务凭据')}</p>}{permissions.tool_catalog && <div className="control-center-item"><strong>{t('本次运行的工具目录')}</strong><span>{permissions.tool_catalog.catalog.length} {t('个工具')} · {t('完整性校验')} {permissions.tool_catalog.digest.slice(0, 12)}</span></div>}</section>
            <section className="technical-policy-events"><div className="technical-section-heading"><div><h3>{t('审计流')}</h3><p>{t('按时间倒序记录权限请求、判定、用户决策与阻止事件。')}</p></div><span>{auditEntries.length}</span></div>
              {auditEntries.length ? <div className="audit-log" role="log" aria-label={t('权限审计日志')}>{auditEntries.map((entry) => <article className={`audit-log-entry tone-${entry.tone}`} key={entry.id}>
                <time dateTime={entry.createdAt}>{formatAuditTime(entry.createdAt)}</time>
                <span className="audit-level">{entry.tone === 'danger' ? 'BLOCK' : entry.tone === 'warning' ? 'ASK' : entry.tone === 'success' ? 'ALLOW' : 'INFO'}</span>
                <div className="audit-log-message"><strong>{t(entry.title)}</strong><span>{t(entry.detail)}</span><small>{entry.actor} · <code>{entry.code}</code> · #{entry.id}</small></div>
              </article>)}</div> : <div className="control-center-empty compact"><span>{t('暂无权限审计事件')}</span></div>}
            </section>
          </div>
        </details>
      </>}
    </section>
  </div>;
}

function BypassConfirmation({ onCancel, onConfirm }: { onCancel: () => void; onConfirm: () => void }) {
  const { t } = useI18n();
  return <div className="modal-backdrop" role="presentation" onMouseDown={onCancel}><section className="confirmation-modal" role="alertdialog" aria-modal="true" aria-labelledby="bypass-title" onMouseDown={(event) => event.stopPropagation()}><div className="warning-mark">!</div><h2 id="bypass-title">{t('启用自动批准模式？')}</h2><p>{t('自动批准模式会跳过可批准行为的交互确认，但仍受平台禁止项、权限边界、预算和沙箱限制。')}</p><div className="confirmation-note"><strong>{t('仅在你信任当前任务和运行环境时启用。')}</strong></div><div className="confirmation-actions"><button className="secondary-button" type="button" onClick={onCancel}>{t('取消')}</button><button className="danger-confirm-button" type="button" onClick={onConfirm}>{t('确认启用自动批准')}</button></div></section></div>;
}

function ModelMenu({ selectedModelKey, onModelChange, modelOptions, trusted, reasoningEffort, onReasoningEffortChange, toolCallLimit, onToolCallLimitChange, planningStrategy, onPlanningStrategyChange, reflectionEnabled, onReflectionChange, reflectionTrigger, onReflectionTriggerChange, onOpenStrategyHelp }: {
  selectedModelKey: string;
  onModelChange: (modelKey: string) => void;
  modelOptions: Array<{ key: string; model: string; providerId: ModelProviderId; providerName: string }>;
  trusted: boolean;
  reasoningEffort: string;
  onReasoningEffortChange: (effort: string) => void;
  toolCallLimit: number;
  onToolCallLimitChange: (limit: number) => void;
  planningStrategy: string;
  onPlanningStrategyChange: (strategy: string) => void;
  reflectionEnabled: boolean;
  onReflectionChange: (enabled: boolean) => void;
  reflectionTrigger: string;
  onReflectionTriggerChange: (trigger: string) => void;
  onOpenStrategyHelp: () => void;
}) {
  const { t } = useI18n();
  const groups = modelOptions.reduce<Array<{ providerId: ModelProviderId; providerName: string; models: Array<{ key: string; model: string }> }>>((result, option) => {
    const group = result.find((item) => item.providerId === option.providerId);
    if (group) group.models.push({ key: option.key, model: option.model });
    else result.push({ providerId: option.providerId, providerName: option.providerName, models: [{ key: option.key, model: option.model }] });
    return result;
  }, []);
  const effort = reasoningEffortValue(reasoningEffort);
  const limitRange = TOOL_CALL_LIMITS[effort];
  return <div className="floating-menu model-menu"><div className="menu-heading">{t('模型')}</div>{groups.length ? groups.map((group) => <div className="model-provider-group" key={group.providerId}><div className="model-provider-heading"><span className={`provider-mark provider-${group.providerId}`}>{modelProviders.find((provider) => provider.id === group.providerId)?.mark}</span><span>{group.providerName}</span></div>{group.models.map((item) => <button className={`model-option ${selectedModelKey === item.key ? 'selected' : ''}`} type="button" key={item.key} onClick={() => onModelChange(item.key)}><div><strong>{item.model}</strong><small>{group.providerName}</small></div><span>{selectedModelKey === item.key ? '✓' : ''}</span></button>)}</div>) : <div className="model-menu-empty">{t('请先在模型管理中启用供应商并配置模型')}</div>}<div className="menu-divider" />{trusted ? <><div className="menu-heading menu-heading-with-help"><span>{t('可信对话策略')}</span><button className="strategy-help-button" type="button" aria-label={t('了解对话策略')} onClick={onOpenStrategyHelp}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7.6 7.3a2.6 2.6 0 1 1 3.15 2.54c-.75.23-1.25.72-1.25 1.46v.3" /><circle cx="9.5" cy="14.35" r=".72" fill="currentColor" stroke="none" /></svg></button></div><MenuChoice label="推理强度" value={reasoningEffort} options={['快速', '均衡', '深入']} onChange={onReasoningEffortChange} /><ToolCallLimitControl value={toolCallLimit} min={limitRange.min} max={limitRange.max} onChange={onToolCallLimitChange} /><MenuChoice label="规划策略" value={planningStrategy} options={['自适应', '先规划']} onChange={onPlanningStrategyChange} /><div className="menu-toggle"><div><strong>{t('反思循环')}</strong><small>{t('检查结果并修订下一步策略')}</small></div><Toggle checked={reflectionEnabled} onChange={onReflectionChange} label={t('反思循环')} /></div>{reflectionEnabled && <MenuChoice label="触发方式" value={reflectionTrigger} options={['失败时', '按需', '每轮']} onChange={onReflectionTriggerChange} />}</> : <div className="standard-mode-note"><Icon name="requestApprove" /><div><strong>{t('快速回答')}</strong><small>{t('开启可信模式后可配置完整对话策略与结果校验。')}</small></div></div>}</div>;
}

function StrategyHelpDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [onClose]);
  const groups = [
    { title: '推理强度', items: [
      ['快速', '允许 0–5 次工具调用，简单任务更快；启用反思时，提供轻量反思能力。'],
      ['均衡', '允许 6–15 次工具调用，兼顾速度与检查深度；启用反思时，提供基本的反思能力。'],
      ['深入', '允许 16–50 次工具调用，为复杂任务提供更多执行预算；启用反思时，允许更深层的反思能力。'],
    ] },
    { title: '规划策略', items: [
      ['自适应', '轻量启动，按结果决定是否调整计划。'],
      ['先规划', '执行前生成完整计划，适合多步骤任务。'],
    ] },
    { title: '反思循环', items: [
      ['关闭', '不调用额外反思；安全与完成检查仍保留。'],
      ['失败时', '只在工具、模型输出或完成检查失败时反思。'],
      ['按需', '失败、低置信度、冲突或无进展时反思。'],
      ['每轮', '每轮结束都反思，更审慎但更慢、更耗用量。'],
    ] },
  ];
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}><section className="usage-modal strategy-guide-modal" role="dialog" aria-modal="true" aria-labelledby="strategy-guide-title" onMouseDown={(event) => event.stopPropagation()}><header><div><span>{t('对话策略')}</span><h2 id="strategy-guide-title">{t('策略说明')}</h2></div><button className="close-button" type="button" aria-label={t('关闭策略说明')} onClick={onClose}>×</button></header><div className="strategy-guide-grid">{groups.map((group) => <section className="strategy-guide-group" key={group.title}><h3>{t(group.title)}</h3>{group.items.map(([label, detail]) => <div className="strategy-guide-item" key={label}><strong>{t(label)}</strong><p>{t(detail)}</p></div>)}</section>)}</div></section></div>;
}

function MenuChoice({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  const { t } = useI18n();
  return <div className="menu-choice"><span>{t(label)}</span><div className="segmented-control">{options.map((option) => <button className={value === option ? 'active' : ''} type="button" key={option} onClick={() => onChange(option)}>{t(option)}</button>)}</div></div>;
}

function ToolCallLimitControl({ value, min, max, onChange }: { value: number; min: number; max: number; onChange: (value: number) => void }) {
  const { t } = useI18n();
  return <div className="tool-limit-control"><div><span>{t('工具调用上限')}</span><output>{t('{count} 次').replace('{count}', String(value))}</output></div><input type="range" aria-label={t('工具调用上限')} min={min} max={max} value={value} onChange={(event) => onChange(Number(event.currentTarget.value))} /><small>{t('当前强度可调整范围：{min}–{max} 次').replace('{min}', String(min)).replace('{max}', String(max))}</small></div>;
}

function ErrorDialog({ error, onClose, onRetry }: { error: ApiErrorPayload; onClose: () => void; onRetry?: () => void }) {
  const { t } = useI18n();
  const technical = error.type.startsWith('infrastructure.') || error.type.startsWith('configuration.') || error.type.startsWith('dependency.') || error.type.startsWith('runtime.');
  const technicalTitle = error.type.startsWith('infrastructure.database') ? '数据存储不可用'
    : error.type.startsWith('configuration.model') ? '大模型尚未配置'
    : error.type.startsWith('dependency.model') ? '大模型服务异常'
    : error.type.startsWith('dependency.search') ? '搜索服务异常'
    : error.type.startsWith('dependency.fetch') ? '网页访问服务异常'
    : error.type === 'runtime.unclassified_response' ? '后端错误未分类'
    : '内部运行时异常';
  const title = error.code === 'GOAL_REQUIRED' ? '请输入任务目标' : technical ? technicalTitle : '无法完成此操作';
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}><section className="confirmation-modal error-dialog" role="alertdialog" aria-modal="true" aria-labelledby="error-title" onMouseDown={(event) => event.stopPropagation()}><div className="warning-mark">!</div><h2 id="error-title">{t(title)}</h2><p>{error.message}</p>{technical && <div className="confirmation-note">{t('错误类型：')}<code>{error.type}</code><br />{t('诊断编号：')}<code>{error.trace_id}</code></div>}<div className="confirmation-actions">{onRetry && <button className="secondary-button" type="button" onClick={onRetry}>{t('重试')}</button>}<button className="danger-confirm-button" type="button" onClick={onClose}>{t('知道了')}</button></div></section></div>;
}

type IconName = 'plus' | 'message' | 'link' | 'library' | 'chart' | 'settings' | 'sparkle' | 'tools' | 'terminal' | 'brain' | 'palette' | 'lock' | 'token' | 'check' | 'route' | 'refresh' | 'requestApprove' | 'autoApprove';

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    plus: <path d="M12 5v14M5 12h14" />,
    message: <path d="M20 11.5a7.5 7.5 0 0 1-8 7.48 8.9 8.9 0 0 1-3.63-.78L4 20l1.34-3.58A7.34 7.34 0 0 1 4 12a7.5 7.5 0 0 1 8-7.48A7.5 7.5 0 0 1 20 11.5Z" />,
    link: <><path d="M10 13a5 5 0 0 0 7.54.54l2-2a5 5 0 0 0-7.07-7.07l-1.15 1.15" /><path d="M14 11a5 5 0 0 0-7.54-.54l-2 2a5 5 0 0 0 7.07 7.07l1.15-1.15" /></>,
    library: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H9l2 2h6.5A2.5 2.5 0 0 1 20 7.5v9A2.5 2.5 0 0 1 17.5 19h-11A2.5 2.5 0 0 1 4 16.5v-11Z" /><path d="M4 8h16" /></>,
    chart: <><path d="M4 19V5M4 19h16" /><path d="m7 15 3-3 3 2 5-6" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.06 2.06-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1 1.55V20h-2.9v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.88.34l-.06.06-2.06-2.06.06-.06A1.7 1.7 0 0 0 7.3 14.8a1.7 1.7 0 0 0-1.55-1H5.7v-2.9h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.88l-.06-.06L9 5.9l.06.06a1.7 1.7 0 0 0 1.88.34 1.7 1.7 0 0 0 1-1.55V4.7h2.9v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.06 2.06-.06.06a1.7 1.7 0 0 0-.34 1.88 1.7 1.7 0 0 0 1.55 1h.09v2.9h-.09a1.7 1.7 0 0 0-1.55 1Z" /></>,
    sparkle: <path d="m12 3 .9 5.1L18 9l-5.1.9L12 15l-.9-5.1L6 9l5.1-.9L12 3Zm6 12 .45 2.55L21 18l-2.55.45L18 21l-.45-2.55L15 18l2.55-.45L18 15Z" />,
    tools: <><path d="M14 6a4 4 0 0 0-5.48 5.48L3.5 16.5a2.12 2.12 0 0 0 3 3l5.02-5.02A4 4 0 0 0 17 9l-3 1-2-2 1-3Z" /><path d="m15 15 4 4" /></>,
    terminal: <><path d="m5 7 4 4-4 4M12 17h7" /><rect x="3" y="4" width="18" height="16" rx="2" /></>,
    brain: <path d="M9 5.2A3.4 3.4 0 0 0 4.7 8.5 3.2 3.2 0 0 0 5 14.7 3.1 3.1 0 0 0 8 19h1.2V5.2Zm6 0a3.4 3.4 0 0 1 4.3 3.3 3.2 3.2 0 0 1-.3 6.2 3.1 3.1 0 0 1-3 4.3h-1.2V5.2ZM9 9H7m2 4H6m9-4h2m-2 4h3" />,
    palette: <path d="M12 3a9 9 0 1 0 0 18h1.1a1.9 1.9 0 0 0 .5-3.73 1.5 1.5 0 0 1 .4-2.95H16A5 5 0 0 0 21 9c0-3.3-4-6-9-6ZM7.5 11.5h.01M9 7.5h.01m6 0h.01m1.5 4h.01" />,
    lock: <><rect x="5" y="10" width="14" height="10" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 4v2" /></>,
    token: <><circle cx="12" cy="12" r="8" /><path d="M9 9h6v6H9zM12 6v3m0 6v3m-6-6h3m6 0h3" /></>,
    check: <><circle cx="12" cy="12" r="8" /><path d="m8.5 12 2.2 2.2 4.8-5" /></>,
    route: <><circle cx="6" cy="6" r="2" /><circle cx="18" cy="18" r="2" /><path d="M8 6h4a3 3 0 0 1 3 3v6" /></>,
    refresh: <><path d="M20 11a8 8 0 0 0-14.8-3M4 5v3h3" /><path d="M4 13a8 8 0 0 0 14.8 3M20 19v-3h-3" /></>,
    requestApprove: <><path d="M12 3 19 6v5c0 4.6-3 7.7-7 10-4-2.3-7-5.4-7-10V6l7-3Z" /><path d="M12 8v4m0 4h.01" /></>,
    autoApprove: <><circle cx="12" cy="12" r="9" /><path d="m8 12 2.5 2.5L16 9" /><path d="M17.5 4.5 19 3m.5 4H22" /></>,
  };
  return <svg className="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function MessageBubble({ message, run, processState, processPanelDefaultOpen, processPanelOpenByRun, onProcessPanelInitialize, onProcessPanelOpenChange }: { message: ChatMessage; run: RunView | null; processState: ProcessStreamState | null; processPanelDefaultOpen: boolean; processPanelOpenByRun: Record<string, boolean>; onProcessPanelInitialize: (runId: string) => void; onProcessPanelOpenChange: (runId: string, open: boolean) => void }) {
  const { t } = useI18n();
  const snapshot = (message.metadata.run_snapshot as RunView | undefined) ?? run;
  const presentation = String(message.metadata.presentation ?? '');

  if (message.role === 'user') {
    return <article className="bubble user" id={`message-${message.id}`}><span className="bubble-label">{t('你')}</span><p>{message.content}</p></article>;
  }

  if (message.role === 'assistant' && message.status === 'streaming') {
    return <article className="bubble assistant answer-message streaming-message" id={`message-${message.id}`}><span className="bubble-label">Astra</span><div className="answer-content"><MarkdownContent content={message.content} /></div></article>;
  }

  if (presentation === 'process' && snapshot) {
    const open = processPanelOpenByRun[snapshot.id] ?? processPanelDefaultOpen;
    return <ProcessPanel run={snapshot} messageId={message.id} liveState={processState?.runId === snapshot.id ? processState : null} open={open} onInitialize={onProcessPanelInitialize} onOpenChange={onProcessPanelOpenChange} />;
  }

  if (presentation === 'answer' && snapshot?.result) {
    return <article className="bubble assistant answer-message" id={`message-${message.id}`}><span className="bubble-label">Astra</span><FinalAnswer run={snapshot} fallback={message.content} /></article>;
  }

  if (!presentation) {
    if (message.role === 'assistant') return <article className="bubble assistant answer-message" id={`message-${message.id}`}><span className="bubble-label">Astra</span><div className="answer-content"><MarkdownContent content={message.content} /></div></article>;
    return null;
  }

  return null;
}

function ProcessPanel({ run, messageId, liveState, open, onInitialize, onOpenChange }: { run: RunView; messageId: string; liveState: ProcessStreamState | null; open: boolean; onInitialize: (runId: string) => void; onOpenChange: (runId: string, open: boolean) => void }) {
  const { t } = useI18n();
  const live = Boolean(liveState?.active);
  const processTitle = live ? t('思考中') : t('思考完成');
  const report = run.result?.verification_report;
  const notes = [...new Set([...(run.result?.verification_notes ?? []), ...(report?.notes ?? [])])];
  const processItems = liveState?.items ?? reconcileProcessSnapshot(null, run).items;
  const streamedVerificationText = processItems.filter((item) => item.kind === 'verification').map((item) => item.detail ?? '').join('；');
  const remainingNotes = notes.filter((note) => !streamedVerificationText.includes(note));
  useEffect(() => onInitialize(run.id), [onInitialize, run.id]);
  const toggle = (event: MouseEvent<HTMLElement>) => {
    event.preventDefault();
    onOpenChange(run.id, !open);
  };
  return <article className={`process-entry ${live ? 'live' : ''}`} id={`message-${messageId}`}><details className="process-panel" open={open}><summary onClick={toggle} aria-expanded={open}><Icon name="brain" /><span className="process-title">{processTitle}{live && <span className="process-thinking-dots" aria-hidden="true"><i /><i /><i /></span>}</span></summary><div className="process-timeline" aria-live={live ? 'polite' : undefined}>
    <ProcessTimeline items={processItems} run={run} />
    {!live && remainingNotes.map((note, index) => <div className="process-step verification" key={`verification-${index}`}><span className="process-dot"><Icon name="check" /></span><div><strong>{t('验证')}</strong><p>{note}</p></div></div>)}
    {!live && <ReasoningAuditSummary run={run} />}
  </div></details></article>;
}

function ProcessTimeline({ items, run }: { items: ProcessStreamItem[]; run: RunView }) {
  const groupedItems = new Map<string, ProcessStreamItem[]>();
  for (const item of items) {
    if (!item.groupId) continue;
    groupedItems.set(item.groupId, [...(groupedItems.get(item.groupId) ?? []), item]);
  }
  return <>{items.map((item) => {
    if (item.groupId) return null;
    if (!isDecisionGroup(item)) return <ProcessTimelineRow item={item} run={run} key={item.id} />;
    const children = groupedItems.get(item.id) ?? [];
    return <section className={`process-decision-group status-${item.status}`} aria-label={item.title} data-process-group={item.id} key={item.id}>
      <ProcessTimelineRow item={item} run={run} anchor />
      {children.length > 0 && <div className="process-decision-children">{children.map((child) => <ProcessTimelineRow item={child} run={run} key={child.id} />)}</div>}
    </section>;
  })}</>;
}

function ProcessTimelineRow({ item, run, anchor = false }: { item: ProcessStreamItem; run: RunView; anchor?: boolean }) {
  const { t } = useI18n();
  const call = item.kind === 'tool' && item.toolCallId ? run.tool_calls.find((candidate) => candidate.id === item.toolCallId) : undefined;
  const outputs = call ? visibleArtifacts(run.artifacts).filter((artifact) => artifact.tool_call_id === call.id) : [];
  const statusLabel = item.status === 'running' ? t('进行中') : item.status === 'failed' ? t('失败') : item.status === 'cancelled' ? t('已终止') : t('已完成');
  const callDetail = call ? `${toolCallStatusLabel(call.status, t)}${toolCallDetail(call.output)}` : undefined;
  const itemDetail = call && item.detail === call.status ? undefined : item.detail;
  const handoff = item.id.startsWith('phase-processing_result-');
  return <div className={`process-step process-${item.kind} status-${item.status} ${anchor ? 'process-group-anchor' : ''} ${handoff ? 'process-handoff' : ''}`}>
    <span className={`process-dot ${item.kind === 'tool' ? 'tool' : ''}`}><Icon name={item.kind === 'tool' ? 'tools' : item.kind === 'verification' ? 'check' : 'brain'} /></span>
    <div><strong>{t(item.title)}</strong>{itemDetail && <p>{itemDetail}</p>}<small>{callDetail ?? statusLabel}</small>{outputs.length > 0 && <a className="process-output-link" href={`#${artifactDomId(outputs[0].id)}`}>{t('{count} 个输出 · 查看输出').replace('{count}', String(outputs.length))}</a>}</div>
  </div>;
}

function toolCallStatusLabel(status: string, t: (value: string) => string) {
  if (status === 'running') return t('进行中');
  if (status === 'failed') return t('失败');
  if (status === 'rejected') return t('已拒绝');
  if (status === 'cancelled') return t('已终止');
  if (status === 'awaiting_approval' || status === 'approved') return t('等待批准');
  return t('已完成');
}

function FinalAnswer({ run, fallback }: { run: RunView; fallback: string }) {
  const { t } = useI18n();
  const result = run.result;
  if (!result) {
    return null;
  }
  const presentation = planAnswerPresentation(result.findings, run.artifacts);
  const notes = [...new Set(result.caveats)];
  const hasSupplementary = result.findings.length > 0 || presentation.supportingArtifacts.length > 0 || result.sources.length > 0 || notes.length > 0;
  const trustedStatus = run.answer_mode === 'trusted'
    ? run.status === 'completed'
      ? '已校验'
      : run.status === 'completed_with_warnings'
        ? '校验带警告'
        : ['blocked', 'failed'].includes(run.status)
          ? '未通过完整校验'
          : null
    : null;
  return (
    <div className="answer-content">
      {trustedStatus && <div className={`trusted-result-status status-${run.status}`}><Icon name="requestApprove" /><span>{t('可信模式')} · {t(trustedStatus)}</span></div>}
      <MarkdownContent content={result.summary || fallback} />
      {presentation.primaryArtifacts.length > 0 && <div className="primary-result-output"><ArtifactGallery artifacts={presentation.primaryArtifacts} label={t('主要结果')} /></div>}
      {hasSupplementary && <section className="answer-supplementary-flat" aria-label={t('附加信息')}><header><span>{t('附加信息')}</span></header>
        {result.findings.length > 0 && <div className="flat-support-row"><span className="flat-support-label">{t('依据')}</span><div className="flat-evidence-list">
          {result.findings.map((finding, index) => <div className="flat-evidence-item" key={index}><span>{index + 1}</span><div>
            {finding.text.trim() !== result.summary.trim() && <MarkdownContent content={finding.text} />}
            {finding.source_urls.length > 0 && <div className="finding-source-links">{finding.source_urls.map((url) => <a href={externalHref(url)} target="_blank" rel="noreferrer" key={url}>{t('关联来源')}</a>)}</div>}
            {finding.artifact_ids.some((artifactId) => presentation.primaryArtifacts.some((artifact) => artifact.id === artifactId)) && <a className="primary-output-reference" href={`#${artifactDomId(presentation.primaryArtifacts[0].id)}`}>{t('查看主要结果')}</a>}
          </div></div>)}
        </div></div>}
        {presentation.supportingArtifacts.length > 0 && <div className="flat-support-row"><span className="flat-support-label">{t('附件')}</span><ArtifactGallery artifacts={presentation.supportingArtifacts} label={t('附件')} /></div>}
        {result.sources.length > 0 && <div className="flat-support-row"><span className="flat-support-label">{t('来源')}</span><div className="source-grid">
          {result.sources.map((source) => {
            const quality = result.source_quality?.find((item) => item.url === source.url);
            return <a key={source.url} href={externalHref(source.url)} target="_blank" rel="noreferrer" className="source-card"><strong>{source.title || source.url}</strong>{quality && <span>{formatScore(quality.quality_score)} · {quality.extraction_strategy || 'unknown'}</span>}</a>;
          })}
        </div></div>}
        {notes.length > 0 && <div className="flat-support-row"><span className="flat-support-label">{t('说明')}</span><div className="answer-notes">{notes.map((item, index) => <p key={`note-${index}`}>{item}</p>)}</div></div>}
      </section>}
    </div>
  );
}

function visibleArtifacts(artifacts: RunView['artifacts']) {
  return artifacts.filter((artifact) => artifact.security_status === 'verified' && artifact.content_url);
}

function artifactDomId(artifactId: string) {
  return `artifact-output-${artifactId}`;
}

function planAnswerPresentation(findings: RunView['result'] extends infer _Result ? NonNullable<RunView['result']>['findings'] : never, artifacts: ArtifactView[]) {
  const visible = visibleArtifacts(artifacts);
  const byId = new Map(visible.map((artifact) => [artifact.id, artifact]));
  const referencedIds = [...new Set(findings.flatMap((finding) => finding.artifact_ids))];
  const referenced = referencedIds.flatMap((artifactId) => byId.get(artifactId) ?? []);
  const isImage = (artifact: ArtifactView) => ['image/png', 'image/svg+xml'].includes(artifact.mime_type ?? '');
  const isInteractive = (artifact: ArtifactView) => artifact.mime_type === 'text/html';
  const primary = referenced.find(isImage) ?? referenced.find(isInteractive) ?? visible.find(isImage) ?? visible.find(isInteractive);
  const primaryArtifacts = primary ? [primary] : [];
  const supportingArtifacts = [
    ...referenced.filter((artifact) => artifact.id !== primary?.id),
    ...visible.filter((artifact) => !referencedIds.includes(artifact.id) && artifact.id !== primary?.id)
      .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)),
  ]
    .filter((artifact, index, items) => items.findIndex((candidate) => candidate.id === artifact.id) === index)
    .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
  return { primaryArtifacts, supportingArtifacts };
}

function ArtifactGallery({ artifacts, label }: { artifacts: ArtifactView[]; label: string }) {
  if (!artifacts.length) return null;
  return <section className="artifact-gallery" aria-label={label}>{artifacts.map((artifact) => <ArtifactCard artifact={artifact} key={artifact.id} />)}</section>;
}

function ArtifactCard({ artifact }: { artifact: ArtifactView }) {
  const { t } = useI18n();
  const artifactLabel = String(artifact.metadata?.filename ?? artifact.type);
  if (artifact.mime_type === 'image/png' || artifact.mime_type === 'image/svg+xml') {
    return <figure className="artifact-card" id={artifactDomId(artifact.id)}><img src={artifact.content_url ?? ''} alt={artifactLabel} onError={(event) => event.currentTarget.parentElement?.classList.add('load-failed')} /><span className="artifact-error" role="status">{t('预览加载失败')}</span><figcaption><strong>{artifactLabel}</strong><span>{artifact.size_bytes?.toLocaleString() ?? 0} bytes</span></figcaption></figure>;
  }
  if (artifact.mime_type === 'text/html') {
    return <figure className="artifact-card interactive" id={artifactDomId(artifact.id)}><iframe src={artifact.content_url ?? ''} title={artifactLabel} sandbox="allow-scripts" referrerPolicy="no-referrer" /><figcaption><strong>{artifactLabel}</strong><span>{t('隔离预览')}</span></figcaption></figure>;
  }
  return <a className="artifact-card file" id={artifactDomId(artifact.id)} href={artifact.content_url ?? ''} target="_blank" rel="noreferrer"><strong>{artifactLabel}</strong><span>{artifact.mime_type ?? artifact.type}</span></a>;
}

function MarkdownContent({ content }: { content: string }) {
  return <div className="markdown-content"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    a: ({ node: _node, href, ...props }) => <a {...props} href={externalHref(href ?? '')} target="_blank" rel="noreferrer" />,
  }}>{content}</ReactMarkdown></div>;
}

function externalHref(value: string) {
  const href = value.trim();
  if (!href || href.startsWith('#') || /^(https?:|mailto:|tel:)/i.test(href)) return href;
  const embeddedUrl = href.match(/https?:\/\/[^\s，。；、）)\]]+/i)?.[0];
  if (embeddedUrl) return embeddedUrl;
  if (href.startsWith('//')) return `https:${href}`;
  const embeddedDomain = href.match(/(?:[a-z0-9-]+\.)+[a-z]{2,}(?:\/[^\s，。；、）)\]]*)?/i)?.[0];
  return `https://${(embeddedDomain ?? href).replace(/^\/+/, '')}`;
}

function ReasoningAuditSummary({ run }: { run: RunView }) {
  const { t } = useI18n();
  const policy = run.reasoning_policy as { effective?: Record<string, unknown>; adjustments?: Array<Record<string, unknown>> } | undefined;
  const criteria = Array.isArray(run.task_contract?.success_criteria) ? run.task_contract.success_criteria as Array<Record<string, unknown>> : [];
  if (!policy?.effective && !criteria.length && !run.terminal_reason) return null;
  return <div className="reasoning-audit-grid">
    {policy?.effective && <div><strong>{t('生效策略')}</strong><span>{String(policy.effective.reasoning_effort ?? 'balanced')} · {String(policy.effective.planning_strategy ?? 'adaptive')} · {String(policy.effective.execution_mode ?? 'request_approval')}</span></div>}
    <div><strong>{t('状态版本')}</strong><span>State {run.state_version ?? 0} · Plan {String(run.plan_graph?.version ?? 1)}</span></div>
    {(run.steps ?? []).map((step) => <div className="plan-node-audit" key={step.id}><strong>{step.index}. {step.title}</strong><span>{planNodeStatusLabel(step.status)}{step.depends_on?.length ? ` · 依赖 ${step.depends_on.join(', ')}` : ''}{step.plan_version ? ` · v${step.plan_version}` : ''}</span>{step.failure && <small>{String(step.failure.category ?? '节点执行失败')}</small>}</div>)}
    {criteria.map((criterion) => <div key={String(criterion.id)}><strong>{String(criterion.description)}</strong><span>{String(criterion.status ?? 'pending')}</span></div>)}
    {policy?.adjustments?.map((adjustment, index) => <div key={`adjust-${index}`}><strong>{t('策略调整')}</strong><span>{String(adjustment.reason ?? adjustment.rule)}</span></div>)}
    {run.terminal_reason && <div><strong>{t('终态原因')}</strong><span>{String(run.terminal_reason.reason ?? run.status)}</span></div>}
    {(run.sandbox_jobs ?? []).map((job) => <div key={job.id}><strong>Sandbox · {job.status}</strong><span>{job.runtime_name ?? job.executor} · {job.image_digest ?? String(job.runtime_profile.image ?? t('未记录镜像'))} · {job.output_artifact_ids.length} artifacts{job.exit_reason ? ` · ${job.exit_reason}` : ''}</span>{(job.stdout_summary || job.stderr_summary) && <details><summary>{t('截断日志')}</summary><pre>{job.stderr_summary || job.stdout_summary}</pre></details>}</div>)}
  </div>;
}

function planNodeStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: '等待执行',
    running: '正在执行',
    completed: '已完成',
    failed: '失败',
    blocked: '已阻塞',
    skipped: '已跳过',
  };
  return labels[status] ?? status;
}

function activeState(run: RunView) {
  const latest = [...(run.turns ?? [])].sort((a, b) => b.turn_index - a.turn_index)[0];
  if (latest?.selected_tool === 'web_search') return '正在搜索候选来源...';
  if (latest?.selected_tool === 'web_fetch') return '正在阅读和验证来源...';
  if (latest?.decision_type === 'reflect') return '正在反思并调整策略...';
  if (run.status === 'verifying') return '正在验证证据...';
  return '正在处理...';
}

function statusLabel(status?: string) {
  const labels: Record<string, string> = {
    idle: '空闲',
    created: '等待开始',
    planning: '正在规划',
    executing: '正在执行',
    verifying: '正在验证',
    waiting_user: '等待回复',
    completed: '已完成',
    completed_with_warnings: '已完成 · 有提醒',
    blocked: '已阻塞',
    failed: '失败',
    cancelled: '已终止',
  };
  return labels[status ?? 'idle'] ?? status ?? labels.idle;
}

function formatScore(score?: number | null) {
  if (typeof score !== 'number') {
    return 'n/a';
  }
  return `${Math.round(score * 100)}%`;
}

function toolCallDetail(output?: Record<string, unknown> | null) {
  if (!output) {
    return '';
  }
  if (typeof output.candidate_count === 'number') {
    return ` · ${output.candidate_count} candidates`;
  }
  if (typeof output.quality_score === 'number' || typeof output.extraction_strategy === 'string') {
    const strategy = typeof output.extraction_strategy === 'string' ? output.extraction_strategy : 'read';
    const score = typeof output.quality_score === 'number' ? ` · ${formatScore(output.quality_score)}` : '';
    return ` · ${strategy}${score}`;
  }
  return '';
}
