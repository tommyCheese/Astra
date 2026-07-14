import { FormEvent, MouseEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AstraApiError, ApiErrorPayload, buildRuntime, cancelRun, cancelRuntimeBuild, createConversationShare, createRun, deleteConversation, getConversation, getConversationStrategy, getRun, getRuntimeProfile, getToolSettings, listConversations, listRuns, resumeRun, revokeConversationShare, streamRunEvents, updateConversation, updateConversationStrategy, updateToolSettings, type ConversationStrategyPreferences, type ToolSetting } from './api';
import { I18nProvider, useI18n } from './i18n';
import { ThemeProvider, useTheme } from './theme';
import type { ArtifactView, ChatMessage, ConversationShare, ConversationSummary, RunView } from './types';
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
};
const DEFAULT_CONVERSATION_STRATEGY: ConversationStrategyPreferences = {
  reasoning_effort: 'balanced',
  planning_strategy: 'adaptive',
  reflection_enabled: true,
  reflection_trigger: 'adaptive',
};

function reasoningEffortLabel(value: ConversationStrategyPreferences['reasoning_effort']): string {
  return value === 'fast' ? '快速' : value === 'deep' ? '深入' : '均衡';
}

function planningStrategyLabel(value: ConversationStrategyPreferences['planning_strategy']): string {
  return value === 'direct' ? '直接' : value === 'plan_first' ? '先规划' : '自适应';
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
  const [streamingAnswer, setStreamingAnswer] = useState('');
  const [answerComplete, setAnswerComplete] = useState(false);
  const [answerSettling, setAnswerSettling] = useState(false);
  const [processState, setProcessState] = useState<ProcessStreamState | null>(null);
  const [processPanelDefaultOpen, setProcessPanelDefaultOpen] = useState(loadProcessPanelDefaultOpen);
  const [processPanelOpenByRun, setProcessPanelOpenByRun] = useState<Record<string, boolean>>({});
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [error, setError] = useState<ApiErrorPayload | null>(null);
  const [view, setView] = useState<'chat' | 'settings'>('chat');
  const [usageOpen, setUsageOpen] = useState(false);
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
  const [reasoningEffort, setReasoningEffort] = useState('均衡');
  const [planningStrategy, setPlanningStrategy] = useState('自适应');
  const [reflectionTrigger, setReflectionTrigger] = useState('按需');
  const [conversationStrategyReady, setConversationStrategyReady] = useState(false);
  const [settingsCategory, setSettingsCategory] = useState('模型管理');
  const attachMenuRef = useRef<HTMLDivElement>(null);
  const executionMenuRef = useRef<HTMLDivElement>(null);
  const modelMenuRef = useRef<HTMLDivElement>(null);
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
  const cancelRequestedRef = useRef(false);
  const availableModels = useMemo(() => providerConfigs
    .filter((provider) => provider.enabled)
    .flatMap((provider) => parseModelIds(provider.models).map((model) => ({ key: `${provider.id}:${model}`, model, providerId: provider.id, providerName: provider.name }))), [providerConfigs]);
  const selectedModel = availableModels.find((item) => item.key === selectedModelKey)?.model ?? '';

  useEffect(() => writeLocalJson(STORAGE_KEYS.conversations, conversationHistory.slice(0, HISTORY_LIMIT)), [conversationHistory]);
  useEffect(() => writeLocalJson(STORAGE_KEYS.processPanelDefaultOpen, processPanelDefaultOpen), [processPanelDefaultOpen]);
  useEffect(() => writeLocalJson(STORAGE_KEYS.modelProviders, providerConfigs), [providerConfigs]);
  useEffect(() => writeLocalString(STORAGE_KEYS.selectedModel, selectedModelKey), [selectedModelKey]);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    void getConversationStrategy(controller.signal).then((strategy) => {
      if (!active || conversationStrategyTouchedRef.current) return;
      conversationStrategyRef.current = strategy;
      setReasoningEffort(reasoningEffortLabel(strategy.reasoning_effort));
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

  function rememberConversation(nextRun: RunView, previousMessages: ChatMessage[] = priorMessages) {
    const conversationId = activeConversationId ?? nextRun.task_id;
    setActiveConversationId(conversationId);
    setConversationHistory((items) => {
      const existing = items.find((item) => item.id === conversationId);
      return [{ ...existing, id: conversationId, run: nextRun, priorMessages: previousMessages, title: existing?.title ?? conversationTitle(nextRun, t('当前 Web Agent 会话')), updated_at: new Date().toISOString() }, ...items.filter((item) => item.id !== conversationId)].slice(0, HISTORY_LIMIT);
    });
  }

  async function openConversation(conversation: ConversationEntry) {
    setActiveConversationId(conversation.id);
    try {
      const detail = await getConversation(conversation.id);
      const runs = detail.runs.map(normalizeRunView);
      const latest = runs[runs.length - 1];
      if (!latest) throw new Error('conversation has no runs');
      setPriorMessages(runs.slice(0, -1).flatMap(buildPresentation));
      setRun(latest);
      setProcessState(reconcileProcessSnapshot(null, latest));
      setConversationHistory((items) => items.map((item) => item.id === conversation.id ? { ...item, run: latest, title: detail.title, pinned_at: detail.pinned_at, updated_at: detail.updated_at, has_active_share: detail.has_active_share } : item));
    } catch {
      if (conversation.run) {
        setPriorMessages(conversation.priorMessages);
        setRun(normalizeRunView(conversation.run));
        setProcessState(reconcileProcessSnapshot(null, normalizeRunView(conversation.run)));
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
    setReasoningEffort(reasoningEffortLabel(next.reasoning_effort));
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
    if (loading) return;
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
      const created = run?.status === 'waiting_user'
        ? await resumeRun(run.id, trimmedGoal, typeof run.waiting_state?.continuation_token === 'string' ? run.waiting_state.continuation_token : undefined)
        : await createRun(trimmedGoal, run?.task_id, {
        reasoning_effort: conversationStrategyRef.current.reasoning_effort,
        planning_strategy: conversationStrategyRef.current.planning_strategy,
        reflection_enabled: conversationStrategyRef.current.reflection_enabled,
        reflection_trigger: conversationStrategyRef.current.reflection_trigger,
        execution_mode: executionMode === 'plan' ? 'plan_only' : executionMode === 'bypass' ? 'auto_approval' : 'request_approval',
        verification_level: 'standard',
        }, selectedOption && selectedProvider ? {
          provider: selectedProvider.id,
          name: selectedOption.model,
          api_key: selectedProvider.apiKey,
          base_url: selectedProvider.endpoint,
        } : undefined);
      const current = normalizeRunView({
        id: created.run_id,
        task_id: created.task_id,
        status: created.status,
        mode: 'general-agent',
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
  }, [run?.id]);

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

  function changeView(nextView: 'chat' | 'settings') {
    setView(nextView);
  }

  function startNewChat() {
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
  }

  return (
    <main className="app-layout">
      <Sidebar
        run={run}
        activeConversationId={activeConversationId}
        conversations={conversationHistory}
        activeView={view}
        onNewChat={startNewChat}
        onSelectConversation={(conversation) => { void openConversation(conversation); }}
        onConversationAction={(kind, conversation) => setConversationAction({ kind, conversation })}
        onTogglePin={(conversation) => { void updateConversation(conversation.id, { pinned: !conversation.pinned_at }).then((updated) => setConversationHistory((items) => items.map((item) => item.id === updated.id ? { ...item, title: updated.title, pinned_at: updated.pinned_at, updated_at: updated.updated_at } : item))); }}
        onOpenSettings={() => {
          setSettingsCategory('模型管理');
          changeView('settings');
        }}
        onOpenUsage={() => setUsageOpen(true)}
      />

      <section className="workspace">
        {view === 'settings' ? (
          <SettingsView
            activeCategory={settingsCategory}
            onCategoryChange={setSettingsCategory}
            onClose={() => changeView('chat')}
            providerConfigs={providerConfigs}
            onProviderConfigsChange={setProviderConfigs}
          />
        ) : <>
        <section className="chat-topbar">
          <div>
            <h1>Astra</h1>
            <p>{t('Web Agent · 可审计搜索与抓取')}</p>
          </div>
          <span className={`status status-${run?.status ?? 'idle'}`}>{statusLabel(run?.status)}</span>
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
          <form className="chat-composer" onSubmit={submit}>
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
                  <button type="button"><span>↥</span><div><strong>{t('上传文件')}</strong><small>{t('文档、代码与数据')}</small></div></button>
                  <button type="button"><span>▧</span><div><strong>{t('添加图片')}</strong><small>{t('分析图像内容')}</small></div></button>
                  <button type="button"><span>⌁</span><div><strong>{t('连接来源')}</strong><small>{t('即将支持')}</small></div></button>
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
              value={goal}
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
                <span>{selectedModel || t('未配置模型')}</span><small>{t(reasoningEffort)} · {reflectionEnabled ? `${t(reflectionTrigger)} ${t('反思')}` : t('反思关闭')}</small><b>⌄</b>
              </button>
              {modelOpen && (
                <ModelMenu
                  selectedModelKey={selectedModelKey}
                  onModelChange={setSelectedModelKey}
                  modelOptions={availableModels}
                  reasoningEffort={reasoningEffort}
                  onReasoningEffortChange={(value) => persistConversationStrategy({ reasoning_effort: value === '快速' ? 'fast' : value === '深入' ? 'deep' : 'balanced' })}
                  planningStrategy={planningStrategy}
                  onPlanningStrategyChange={(value) => persistConversationStrategy({ planning_strategy: value === '直接' ? 'direct' : value === '先规划' ? 'plan_first' : 'adaptive' })}
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
              <button className="send-button" type="submit" aria-label={t('发送')} disabled={!conversationStrategyReady}>↑</button>
            )}
          </form>
          {error && <ErrorDialog error={error} onClose={() => setError(null)} onRetry={error.retryable ? () => document.querySelector<HTMLFormElement>('.chat-composer')?.requestSubmit() : undefined} />}
        </section>

        </>}
      </section>
      {usageOpen && <UsageDashboard taskId={run?.task_id} runId={run?.id} onClose={() => setUsageOpen(false)} />}
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

function Sidebar({ run, activeConversationId, conversations, activeView, onNewChat, onSelectConversation, onConversationAction, onTogglePin, onOpenSettings, onOpenUsage }: {
  run: RunView | null;
  activeConversationId: string | null;
  conversations: ConversationEntry[];
  activeView: 'chat' | 'settings';
  onNewChat: () => void;
  onSelectConversation: (conversation: ConversationEntry) => void;
  onConversationAction: (kind: 'rename' | 'share' | 'delete', conversation: ConversationEntry) => void;
  onTogglePin: (conversation: ConversationEntry) => void;
  onOpenSettings: () => void;
  onOpenUsage: () => void;
}) {
  const { t } = useI18n();
  const [menuId, setMenuId] = useState<string | null>(null);
  const pinned = conversations.filter((item) => item.pinned_at);
  const recent = conversations.filter((item) => !item.pinned_at);
  const renderConversation = (conversation: ConversationEntry) => <div className={`history-item ${activeConversationId === conversation.id ? 'active' : ''}`} key={conversation.id}>
    <button className="history-select" type="button" onClick={() => onSelectConversation(conversation)}><Icon name="message" /><span>{conversation.title ?? (conversation.run ? conversationTitle(conversation.run, t('当前 Web Agent 会话')) : t('未命名对话'))}</span><small>{conversation.run ? statusLabel(conversation.run.status) : ''}</small></button>
    <button className="history-more" type="button" aria-label={`${t('更多操作')} ${conversation.title ?? ''}`} aria-expanded={menuId === conversation.id} onClick={(event) => { event.stopPropagation(); setMenuId((current) => current === conversation.id ? null : conversation.id); }}>•••</button>
    {menuId === conversation.id && <div className="history-menu" role="menu">
      <button type="button" onClick={() => { setMenuId(null); onConversationAction('rename', conversation); }}>{t('重命名')}</button>
      <button type="button" onClick={() => { setMenuId(null); onTogglePin(conversation); }}>{conversation.pinned_at ? t('取消置顶') : t('置顶')}</button>
      <button type="button" onClick={() => { setMenuId(null); onConversationAction('share', conversation); }}>{t('分享')}</button>
      <button className="danger" type="button" onClick={() => { setMenuId(null); onConversationAction('delete', conversation); }}>{t('删除')}</button>
    </div>}
  </div>;
  return (
    <aside className="sidebar">
      <div className="brand">
        <AstraBrandIcon />
        <div>
          <strong>Astra</strong>
          <span>Agent Console</span>
        </div>
      </div>

      <button className="new-chat-button" type="button" onClick={onNewChat}>
        <span className="button-icon"><Icon name="plus" /></span>
        {t('新对话')}
      </button>

      <nav className="side-section">
        <div className="history-heading"><span className="side-title">{t('历史对话')}</span><small>{t('最多保留最近 {count} 个会话').replace('{count}', String(HISTORY_LIMIT))}</small></div>
        <div className="history-list">
          {pinned.length > 0 && <><span className="history-group-title">{t('置顶')}</span>{pinned.map(renderConversation)}</>}
          {recent.length > 0 && pinned.length > 0 && <span className="history-group-title">{t('最近')}</span>}
          {recent.map(renderConversation)}
          {!conversations.length && <div className="history-empty">{t('暂无对话')}</div>}
        </div>
        {conversations.length >= HISTORY_LIMIT && <p className="history-retention-note">{t('较早的会话会自动移出此列表')}</p>}
      </nav>

      <div className="sidebar-bottom">
        <button className="side-action" type="button" onClick={onOpenUsage}>
          <Icon name="chart" />
          <span>{t('用量统计')}</span>
          <small>{run?.tool_calls.length ?? 0} calls</small>
        </button>
        <button className={`side-action ${activeView === 'settings' ? 'active' : ''}`} type="button" onClick={onOpenSettings}>
          <Icon name="settings" />
          <span>{t('设置')}</span>
          <small>{t('本地配置')}</small>
        </button>
      </div>
    </aside>
  );
}

function conversationTitle(run: RunView, fallback: string) {
  return run.summary?.trim() || run.chat_messages?.find((message) => message.role === 'user')?.content || fallback;
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

const settingCategories = ['模型管理', '工具', '运行时', '记忆', '验证与安全', '界面', '数据与隐私'];
const settingCategoryIcons: Record<string, IconName> = {
  '模型管理': 'sparkle',
  '工具': 'tools',
  '运行时': 'terminal',
  '记忆': 'brain',
  '验证与安全': 'shield',
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
  if (category === '验证与安全') return <SettingsGroup title="验证与安全" description="定义 Agent 在报告完成前必须满足的通用验证要求。"><SettingRow title="完成前验证" description="提交结果前运行与任务类型匹配的验证器"><Toggle checked /></SettingRow><SettingRow title="验证强度" description="控制验证覆盖范围以及失败后的检查深度"><TranslatedSelect defaultValue="standard" options={[['basic', '基础'], ['standard', '标准'], ['strict', '严格']]} /></SettingRow><SettingRow title="验证失败处理" description="验证未通过时决定继续修复、带警告返回或停止任务"><TranslatedSelect defaultValue="repair" options={[['repair', '自动修复'], ['warn', '带警告返回'], ['block', '停止任务']]} /></SettingRow></SettingsGroup>;
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

type ModelProviderId = 'openai' | 'anthropic' | 'google' | 'deepseek' | 'qwen' | 'siliconflow' | 'azure' | 'compatible';
type ModelProviderConfig = {
  id: ModelProviderId;
  name: string;
  enabled: boolean;
  endpoint: string;
  models: string;
  organization: string;
  apiKey: string;
};

const modelProviders: Array<{ id: ModelProviderId; name: string; detail: string; mark: string }> = [
  { id: 'openai', name: 'OpenAI', detail: 'Responses API', mark: 'O' },
  { id: 'anthropic', name: 'Anthropic', detail: 'Claude API', mark: 'A' },
  { id: 'google', name: 'Google Gemini', detail: 'Generative Language API', mark: 'G' },
  { id: 'deepseek', name: 'DeepSeek', detail: 'DeepSeek 开放平台', mark: 'D' },
  { id: 'qwen', name: '通义千问', detail: '阿里云百炼', mark: 'Q' },
  { id: 'siliconflow', name: 'SiliconFlow', detail: '硅基流动模型广场', mark: 'S' },
  { id: 'azure', name: 'Azure OpenAI', detail: 'Azure AI Foundry', mark: 'Az' },
  { id: 'compatible', name: 'OpenAI 兼容', detail: 'Ollama、vLLM、OpenRouter', mark: '↗' },
];

const providerDefaults: Record<ModelProviderId, { endpoint: string; models: string; organization: string }> = {
  openai: { endpoint: 'https://api.openai.com/v1', models: 'gpt-5, gpt-5-mini', organization: '' },
  anthropic: { endpoint: 'https://api.anthropic.com', models: 'claude-sonnet-4, claude-opus-4', organization: '' },
  google: { endpoint: 'https://generativelanguage.googleapis.com', models: 'gemini-2.5-pro, gemini-2.5-flash', organization: '' },
  deepseek: { endpoint: 'https://api.deepseek.com', models: 'deepseek-v4-pro, deepseek-v4-flash', organization: '' },
  qwen: { endpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: 'qwen3.7-plus, qwen-plus', organization: '' },
  siliconflow: { endpoint: 'https://api.siliconflow.cn/v1', models: 'deepseek-ai/DeepSeek-V3, Qwen/Qwen2.5-72B-Instruct', organization: '' },
  azure: { endpoint: '', models: '', organization: '2025-04-01-preview' },
  compatible: { endpoint: 'http://127.0.0.1:11434/v1', models: '', organization: '' },
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
  const [connectionState, setConnectionState] = useState('未验证');
  const provider = providers.find((item) => item.id === selectedProvider)!;
  const providerMeta = modelProviders.find((item) => item.id === selectedProvider)!;

  function selectProvider(id: ModelProviderId) {
    setSelectedProvider(id);
    setShowKey(false);
    setConnectionState('未验证');
  }

  function updateProvider(patch: Partial<ModelProviderConfig>) {
    onChange(providers.map((item) => item.id === selectedProvider ? { ...item, ...patch } : item));
    setConnectionState('未验证');
  }

  function toggleProvider() {
    updateProvider({ enabled: !provider.enabled });
  }

  return (
    <SettingsGroup title="模型管理" description="配置模型供应商连接、凭据和 Agent 可选模型。">
      <div className="provider-workspace">
        <aside className="provider-list" aria-label={t('模型供应商')}>
          <div className="provider-list-heading"><span>{t('供应商')}</span><button type="button" aria-label={t('添加供应商')} title={t('添加供应商')} onClick={() => selectProvider('compatible')}>+</button></div>
          {modelProviders.map((item) => (
            <button className={`provider-item ${item.id === selectedProvider ? 'active' : ''}`} type="button" key={item.id} onClick={() => selectProvider(item.id)}>
              <span className={`provider-mark provider-${item.id}`}>{item.mark}</span>
              <span><strong>{item.name}</strong><small>{t(item.detail)}</small></span>
              <i className={providers.find((provider) => provider.id === item.id)?.enabled ? 'connected' : ''} />
            </button>
          ))}
        </aside>

        <section className="provider-editor">
          <header className="provider-editor-header">
            <div><span className={`provider-mark provider-${provider.id}`}>{providerMeta.mark}</span><div><h3>{provider.name}</h3><p>{t(providerMeta.detail)}</p></div></div>
            <label className="provider-enabled"><span>{t('启用')}</span><Toggle checked={provider.enabled} onChange={toggleProvider} /></label>
          </header>

          <div className="provider-form">
            <label><span>{t('API 地址')}</span><small>{t('供应商 API 的基础地址')}</small><input value={provider.endpoint} onChange={(event) => updateProvider({ endpoint: event.target.value })} spellCheck={false} /></label>
            <label><span>{t('API Key')}</span><small>{t('凭据保存在当前浏览器本地，不会写入运行记录')}</small><div className="secret-input"><input type={showKey ? 'text' : 'password'} value={provider.apiKey} onChange={(event) => updateProvider({ apiKey: event.target.value })} placeholder={selectedProvider === 'google' ? 'AIza...' : 'sk-...'} autoComplete="off" /><button type="button" onClick={() => setShowKey((visible) => !visible)}>{t(showKey ? '隐藏' : '显示')}</button></div></label>
            <label><span>{t(selectedProvider === 'azure' ? 'API 版本' : '组织或项目 ID')}</span><small>{t(selectedProvider === 'azure' ? 'Azure OpenAI 请求使用的 API 版本' : '可选，用于供应商侧的项目隔离与计费')}</small><input value={provider.organization} onChange={(event) => updateProvider({ organization: event.target.value })} placeholder={selectedProvider === 'azure' ? '2025-04-01-preview' : t('可选')} /></label>
            <label><span>{t('可用模型 ID')}</span><small>{t('使用逗号分隔，模型选择器将使用这些标识')}</small><textarea value={provider.models} onChange={(event) => updateProvider({ models: event.target.value })} placeholder="model-id-1, model-id-2" /></label>
          </div>

          <div className="provider-advanced">
            <div><strong>{t('请求兼容性')}</strong><small>{t(selectedProvider === 'anthropic' ? 'Anthropic Messages API' : selectedProvider === 'google' ? 'Google generateContent API' : 'OpenAI Responses / Chat Completions')}</small></div>
            <TranslatedSelect defaultValue="auto" options={[['auto', '自动检测'], ['responses', 'Responses API'], ['chat', 'Chat Completions']]} />
          </div>

          <footer className="provider-actions">
            <span className={`connection-state ${connectionState === '连接正常' ? 'success' : ''}`}><i />{t(connectionState)}</span>
            <button className="secondary-button" type="button" onClick={() => setConnectionState(provider.endpoint && (provider.apiKey || selectedProvider === 'compatible') ? '连接正常' : '缺少连接信息')}>{t('测试连接')}</button>
            <button className="primary-button" type="button" onClick={() => setConnectionState('配置已更新')}>{t('保存配置')}</button>
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
    { id: 'plan' as const, title: '仅规划', detail: '只规划任务，不调用工具或执行命令', icon: 'route' as const },
    { id: 'default' as const, title: '请求批准', detail: '自动执行低风险操作，高风险权限需要确认', icon: 'requestApprove' as const },
    { id: 'bypass' as const, title: '自动批准', detail: '自动执行所有命令和工具，不再请求确认', icon: 'autoApprove' as const },
  ];
  return <div className="floating-menu execution-menu"><div className="menu-heading">{t('执行模式')}</div>{modes.map((mode) => <button className={value === mode.id ? 'selected' : ''} type="button" key={mode.id} onClick={() => onChange(mode.id)}><Icon name={mode.icon} /><div><strong>{t(mode.title)}</strong><small>{t(mode.detail)}</small></div><span className="mode-selected-mark">{value === mode.id ? '✓' : ''}</span></button>)}</div>;
}

function BypassConfirmation({ onCancel, onConfirm }: { onCancel: () => void; onConfirm: () => void }) {
  const { t } = useI18n();
  return <div className="modal-backdrop" role="presentation" onMouseDown={onCancel}><section className="confirmation-modal" role="alertdialog" aria-modal="true" aria-labelledby="bypass-title" onMouseDown={(event) => event.stopPropagation()}><div className="warning-mark">!</div><h2 id="bypass-title">{t('启用自动批准模式？')}</h2><p>{t('自动批准模式将允许 Agent 自动执行所有命令和工具，包括可能修改文件、访问网络或影响外部系统的高风险操作。')}</p><div className="confirmation-note"><strong>{t('仅在你信任当前任务和运行环境时启用。')}</strong></div><div className="confirmation-actions"><button className="secondary-button" type="button" onClick={onCancel}>{t('取消')}</button><button className="danger-confirm-button" type="button" onClick={onConfirm}>{t('确认启用自动批准')}</button></div></section></div>;
}

function ModelMenu({ selectedModelKey, onModelChange, modelOptions, reasoningEffort, onReasoningEffortChange, planningStrategy, onPlanningStrategyChange, reflectionEnabled, onReflectionChange, reflectionTrigger, onReflectionTriggerChange, onOpenStrategyHelp }: {
  selectedModelKey: string;
  onModelChange: (modelKey: string) => void;
  modelOptions: Array<{ key: string; model: string; providerId: ModelProviderId; providerName: string }>;
  reasoningEffort: string;
  onReasoningEffortChange: (effort: string) => void;
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
  return <div className="floating-menu model-menu"><div className="menu-heading">{t('模型')}</div>{groups.length ? groups.map((group) => <div className="model-provider-group" key={group.providerId}><div className="model-provider-heading"><span className={`provider-mark provider-${group.providerId}`}>{modelProviders.find((provider) => provider.id === group.providerId)?.mark}</span><span>{group.providerName}</span></div>{group.models.map((item) => <button className={`model-option ${selectedModelKey === item.key ? 'selected' : ''}`} type="button" key={item.key} onClick={() => onModelChange(item.key)}><div><strong>{item.model}</strong><small>{group.providerName}</small></div><span>{selectedModelKey === item.key ? '✓' : ''}</span></button>)}</div>) : <div className="model-menu-empty">{t('请先在模型管理中启用供应商并配置模型')}</div>}<div className="menu-divider" /><div className="menu-heading menu-heading-with-help"><span>{t('对话策略')}</span><button className="strategy-help-button" type="button" aria-label={t('了解对话策略')} onClick={onOpenStrategyHelp}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7.6 7.3a2.6 2.6 0 1 1 3.15 2.54c-.75.23-1.25.72-1.25 1.46v.3" /><circle cx="9.5" cy="14.35" r=".72" fill="currentColor" stroke="none" /></svg></button></div><MenuChoice label="推理强度" value={reasoningEffort} options={['快速', '均衡', '深入']} onChange={onReasoningEffortChange} /><MenuChoice label="规划策略" value={planningStrategy} options={['直接', '自适应', '先规划']} onChange={onPlanningStrategyChange} /><div className="menu-toggle"><div><strong>{t('反思循环')}</strong><small>{t('检查结果并修订下一步策略')}</small></div><Toggle checked={reflectionEnabled} onChange={onReflectionChange} label={t('反思循环')} /></div>{reflectionEnabled && <MenuChoice label="触发方式" value={reflectionTrigger} options={['失败时', '按需', '每轮']} onChange={onReflectionTriggerChange} />}</div>;
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
      ['快速', '减少思考轮次与工具预算，简单任务更快。'],
      ['均衡', '兼顾速度与检查深度，适合多数任务。'],
      ['深入', '增加思考、工具与反思预算，复杂任务更稳。'],
    ] },
    { title: '规划策略', items: [
      ['直接', '生成单步计划后立即处理，启动最快。'],
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

type IconName = 'plus' | 'message' | 'chart' | 'settings' | 'sparkle' | 'tools' | 'terminal' | 'brain' | 'shield' | 'palette' | 'lock' | 'token' | 'check' | 'route' | 'refresh' | 'requestApprove' | 'autoApprove';

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    plus: <path d="M12 5v14M5 12h14" />,
    message: <path d="M20 11.5a7.5 7.5 0 0 1-8 7.48 8.9 8.9 0 0 1-3.63-.78L4 20l1.34-3.58A7.34 7.34 0 0 1 4 12a7.5 7.5 0 0 1 8-7.48A7.5 7.5 0 0 1 20 11.5Z" />,
    chart: <><path d="M4 19V5M4 19h16" /><path d="m7 15 3-3 3 2 5-6" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.06 2.06-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1 1.55V20h-2.9v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.88.34l-.06.06-2.06-2.06.06-.06A1.7 1.7 0 0 0 7.3 14.8a1.7 1.7 0 0 0-1.55-1H5.7v-2.9h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.88l-.06-.06L9 5.9l.06.06a1.7 1.7 0 0 0 1.88.34 1.7 1.7 0 0 0 1-1.55V4.7h2.9v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.06 2.06-.06.06a1.7 1.7 0 0 0-.34 1.88 1.7 1.7 0 0 0 1.55 1h.09v2.9h-.09a1.7 1.7 0 0 0-1.55 1Z" /></>,
    sparkle: <path d="m12 3 .9 5.1L18 9l-5.1.9L12 15l-.9-5.1L6 9l5.1-.9L12 3Zm6 12 .45 2.55L21 18l-2.55.45L18 21l-.45-2.55L15 18l2.55-.45L18 15Z" />,
    tools: <><path d="M14 6a4 4 0 0 0-5.48 5.48L3.5 16.5a2.12 2.12 0 0 0 3 3l5.02-5.02A4 4 0 0 0 17 9l-3 1-2-2 1-3Z" /><path d="m15 15 4 4" /></>,
    terminal: <><path d="m5 7 4 4-4 4M12 17h7" /><rect x="3" y="4" width="18" height="16" rx="2" /></>,
    brain: <path d="M9 5.2A3.4 3.4 0 0 0 4.7 8.5 3.2 3.2 0 0 0 5 14.7 3.1 3.1 0 0 0 8 19h1.2V5.2Zm6 0a3.4 3.4 0 0 1 4.3 3.3 3.2 3.2 0 0 1-.3 6.2 3.1 3.1 0 0 1-3 4.3h-1.2V5.2ZM9 9H7m2 4H6m9-4h2m-2 4h3" />,
    shield: <path d="M12 3 19 6v5c0 4.6-3 7.7-7 10-4-2.3-7-5.4-7-10V6l7-3Zm-3 9 2 2 4-4" />,
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
  const turns = [...(run.turns ?? [])].sort((a, b) => a.turn_index - b.turn_index);
  const live = Boolean(liveState?.active);
  const processSummary = live
    ? t('实时更新')
    : run.tool_calls.length > 0
    ? t('{steps} 个步骤 · {tools} 次工具调用').replace('{steps}', String(turns.length)).replace('{tools}', String(run.tool_calls.length))
    : t('{steps} 个步骤').replace('{steps}', String(turns.length));
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
  return <article className={`process-entry ${live ? 'live' : ''}`} id={`message-${messageId}`}><details className="process-panel" open={open}><summary onClick={toggle} aria-expanded={open}><Icon name="brain" /><span>{t('思考过程')}</span><small>{processSummary}</small></summary><div className="process-timeline" aria-live={live ? 'polite' : undefined}>
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
  const callDetail = call ? `${call.status}${toolCallDetail(call.output)}` : undefined;
  const handoff = item.id.startsWith('phase-processing_result-');
  return <div className={`process-step process-${item.kind} status-${item.status} ${anchor ? 'process-group-anchor' : ''} ${handoff ? 'process-handoff' : ''}`}>
    <span className={`process-dot ${item.kind === 'tool' ? 'tool' : ''}`}><Icon name={item.kind === 'tool' ? 'tools' : item.kind === 'verification' ? 'check' : 'brain'} /></span>
    <div><strong>{t(item.title)}</strong>{item.detail && <p>{item.detail}</p>}<small>{callDetail ?? statusLabel}</small>{outputs.length > 0 && <a className="process-output-link" href={`#${artifactDomId(outputs[0].id)}`}>{t('{count} 个输出 · 查看输出').replace('{count}', String(outputs.length))}</a>}</div>
  </div>;
}

function FinalAnswer({ run, fallback }: { run: RunView; fallback: string }) {
  const { t } = useI18n();
  const result = run.result;
  if (!result) {
    return null;
  }
  const placements = planArtifactPlacements(result.findings, run.artifacts);
  const notes = [...new Set(result.caveats)];
  return (
    <div className="answer-content">
      <MarkdownContent content={result.summary || fallback} />
      {placements.findings.length > 0 && <details className="answer-support evidence-support"><summary>{t('数据与证据 · {count}').replace('{count}', String(placements.findings.length))}</summary><div className="evidence-list">
        {placements.findings.map(({ finding, artifacts, repeatedArtifactIds }, index) => (
          <section className="finding-block" key={index}>
            <span className="evidence-index" aria-hidden="true">{index + 1}</span>
            <div className="evidence-content">
              {finding.text.trim() !== result.summary.trim() && <MarkdownContent content={finding.text} />}
              {finding.source_urls.length > 0 && <div className="finding-source-links">{finding.source_urls.map((url) => <a href={externalHref(url)} target="_blank" rel="noreferrer" key={url}>{t('关联来源')}</a>)}</div>}
              <ArtifactGallery artifacts={artifacts} label={t('关联输出')} />
              {repeatedArtifactIds.length > 0 && <div className="artifact-repeat-links">{repeatedArtifactIds.map((artifactId) => <a href={`#${artifactDomId(artifactId)}`} key={artifactId}>{t('查看上方已展示的输出')}</a>)}</div>}
            </div>
          </section>
        ))}
      </div></details>}
      <OtherArtifacts artifacts={placements.otherArtifacts} />
      {result.sources.length ? (
        <details className="answer-support"><summary>{t('来源 · {count}').replace('{count}', String(result.sources.length))}</summary><div className="source-grid">
          {result.sources.map((source) => {
            const quality = result.source_quality?.find((item) => item.url === source.url);
            return (
              <a key={source.url} href={externalHref(source.url)} target="_blank" rel="noreferrer" className="source-card">
                <strong>{source.title || source.url}</strong>
                {quality && (
                  <span>{formatScore(quality.quality_score)} · {quality.extraction_strategy || 'unknown'}</span>
                )}
              </a>
            );
          })}
        </div></details>
      ) : null}
      {notes.length > 0 && <div className="answer-notes">{notes.map((item, index) => <p key={`note-${index}`}>{item}</p>)}</div>}
    </div>
  );
}

function visibleArtifacts(artifacts: RunView['artifacts']) {
  return artifacts.filter((artifact) => artifact.security_status === 'verified' && artifact.content_url);
}

function artifactDomId(artifactId: string) {
  return `artifact-output-${artifactId}`;
}

function planArtifactPlacements(findings: RunView['result'] extends infer _Result ? NonNullable<RunView['result']>['findings'] : never, artifacts: ArtifactView[]) {
  const visible = visibleArtifacts(artifacts);
  const byId = new Map(visible.map((artifact) => [artifact.id, artifact]));
  const rendered = new Set<string>();
  const plannedFindings = findings.map((finding) => {
    const localArtifacts: ArtifactView[] = [];
    const repeatedArtifactIds: string[] = [];
    for (const artifactId of finding.artifact_ids) {
      const artifact = byId.get(artifactId);
      if (!artifact) continue;
      if (rendered.has(artifactId)) {
        if (!repeatedArtifactIds.includes(artifactId)) repeatedArtifactIds.push(artifactId);
        continue;
      }
      rendered.add(artifactId);
      localArtifacts.push(artifact);
    }
    return { finding, artifacts: localArtifacts, repeatedArtifactIds };
  });
  const otherArtifacts = visible
    .filter((artifact) => !rendered.has(artifact.id))
    .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
  return { findings: plannedFindings, otherArtifacts };
}

function OtherArtifacts({ artifacts }: { artifacts: ArtifactView[] }) {
  const { t } = useI18n();
  if (!artifacts.length) return null;
  if (artifacts.length <= 2) {
    return <section className="other-artifacts"><h3>{t('其他输出')}</h3><ArtifactGallery artifacts={artifacts} label={t('其他输出')} /></section>;
  }
  return <details className="other-artifacts collapsible"><summary>{t('其他输出 · {count}').replace('{count}', String(artifacts.length))}</summary><ArtifactGallery artifacts={artifacts} label={t('其他输出')} /></details>;
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
    {criteria.map((criterion) => <div key={String(criterion.id)}><strong>{String(criterion.description)}</strong><span>{String(criterion.status ?? 'pending')}</span></div>)}
    {policy?.adjustments?.map((adjustment, index) => <div key={`adjust-${index}`}><strong>{t('策略调整')}</strong><span>{String(adjustment.reason ?? adjustment.rule)}</span></div>)}
    {run.terminal_reason && <div><strong>{t('终态原因')}</strong><span>{String(run.terminal_reason.reason ?? run.status)}</span></div>}
    {(run.sandbox_jobs ?? []).map((job) => <div key={job.id}><strong>Sandbox · {job.status}</strong><span>{job.runtime_name ?? job.executor} · {job.image_digest ?? String(job.runtime_profile.image ?? t('未记录镜像'))} · {job.output_artifact_ids.length} artifacts{job.exit_reason ? ` · ${job.exit_reason}` : ''}</span>{(job.stdout_summary || job.stderr_summary) && <details><summary>{t('截断日志')}</summary><pre>{job.stderr_summary || job.stdout_summary}</pre></details>}</div>)}
  </div>;
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
