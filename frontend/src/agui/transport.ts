import { AgentCapabilitiesSchema, HttpAgent, type AgentCapabilities, type BaseEvent, type RunAgentInput } from '@ag-ui/client';

export interface AstraStreamCallbacks {
  onEvent(event: BaseEvent): void;
  onError(error: Error): void;
  onComplete(): void;
}

export interface AstraAgentStream {
  close(): void;
}

export interface AstraAgentTransport {
  start(input: RunAgentInput, callbacks: AstraStreamCallbacks): AstraAgentStream;
  resume(input: RunAgentInput, callbacks: AstraStreamCallbacks): AstraAgentStream;
  cancel(protocolRunId: string): Promise<void>;
  getCapabilities(): Promise<AgentCapabilities>;
}

export function selectAstraTransport(
  agUiEnabled: boolean,
  agUiTransport: AstraAgentTransport,
  nativeTransport: AstraAgentTransport,
): AstraAgentTransport {
  return agUiEnabled ? agUiTransport : nativeTransport;
}

export class AgUiHttpTransport implements AstraAgentTransport {
  constructor(
    private readonly endpoint = '/api/ag-ui',
    private readonly cancellationEndpoint?: (protocolRunId: string) => string,
  ) {}

  start(input: RunAgentInput, callbacks: AstraStreamCallbacks): AstraAgentStream {
    const agent = new HttpAgent({
      url: this.endpoint,
      threadId: input.threadId,
      initialMessages: input.messages,
      initialState: input.state,
    });
    const subscription = agent.run(input).subscribe({
      next: callbacks.onEvent,
      error: (error: unknown) => callbacks.onError(error instanceof Error ? error : new Error(String(error))),
      complete: callbacks.onComplete,
    });
    return {
      close: () => {
        subscription.unsubscribe();
        agent.abortRun();
      },
    };
  }

  resume(input: RunAgentInput, callbacks: AstraStreamCallbacks): AstraAgentStream {
    if (!input.resume?.length) {
      throw new Error('恢复 AG-UI 运行时必须提供 interrupt 响应。');
    }
    return this.start(input, callbacks);
  }

  async cancel(protocolRunId: string): Promise<void> {
    if (!this.cancellationEndpoint) {
      throw new Error('当前 AG-UI profile 尚未启用显式取消。');
    }
    const response = await fetch(this.cancellationEndpoint(protocolRunId), { method: 'POST' });
    if (!response.ok) {
      throw new Error(`取消运行失败（HTTP ${response.status}）。`);
    }
  }

  async getCapabilities(): Promise<AgentCapabilities> {
    const response = await fetch(`${this.endpoint}/capabilities`);
    if (!response.ok) throw new Error(`读取 AG-UI 能力失败（HTTP ${response.status}）。`);
    return AgentCapabilitiesSchema.parse(await response.json());
  }
}
