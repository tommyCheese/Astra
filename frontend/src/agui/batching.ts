import type { AgUiProjectedEvent } from './store';

const CRITICAL = new Set(['RUN_FINISHED', 'RUN_ERROR', 'ACTIVITY_SNAPSHOT']);

export class AgUiFrameBatcher {
  private queue: AgUiProjectedEvent[] = [];
  private frame: number | null = null;
  private displayed = false;

  constructor(private readonly commit: (events: AgUiProjectedEvent[]) => void) {}

  push(event: AgUiProjectedEvent): void {
    const firstDisplayable = !this.displayed && ['TEXT_MESSAGE_CONTENT', 'ACTIVITY_SNAPSHOT'].includes(event.type);
    if (firstDisplayable || CRITICAL.has(event.type)) {
      this.flush();
      this.displayed = this.displayed || firstDisplayable;
      this.commit([event]);
      return;
    }
    this.queue.push(event);
    if (this.frame === null) this.frame = requestAnimationFrame(() => this.flush());
  }

  flush(): void {
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    if (!this.queue.length) return;
    const events = this.queue;
    this.queue = [];
    this.commit(events);
  }

  close(): void {
    this.flush();
  }
}
