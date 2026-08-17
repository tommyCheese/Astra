export interface AgUiEntryEnvironment {
  mode: string;
  enabled: string | undefined;
  pathname: string;
}

export function shouldRenderAgUiPreview(environment: AgUiEntryEnvironment): boolean {
  return environment.mode === 'development'
    && environment.enabled === 'true'
    && environment.pathname === '/__dev/ag-ui';
}
