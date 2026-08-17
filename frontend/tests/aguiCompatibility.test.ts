import {
  AgentCapabilitiesSchema,
  EventSchemas,
  RunAgentInputSchema,
} from '@ag-ui/core';
import { describe, expect, it } from 'vitest';
import golden from '../../backend/tests/fixtures/ag_ui/golden-contract.json';
import profile from '../../contracts/ag-ui/profile-v1.json';
import packageJson from '../package.json';
import {
  ASTRA_AG_UI_ACTIVITY_SCHEMA_VERSIONS,
  ASTRA_AG_UI_PACKAGE_VERSION,
  ASTRA_AG_UI_PROFILE_VERSION,
} from '../src/agui/compatibility';
import { selectAstraTransport, type AstraAgentTransport } from '../src/agui/transport';
import { shouldRenderAgUiPreview } from '../src/agui/entryMode';

describe('Astra AG-UI compatibility contract', () => {
  it('pins one reviewed profile and exact package set', () => {
    expect(profile.profileVersion).toBe(ASTRA_AG_UI_PROFILE_VERSION);
    expect(profile.activitySchemas).toEqual(ASTRA_AG_UI_ACTIVITY_SCHEMA_VERSIONS);
    expect(packageJson.dependencies['@ag-ui/core']).toBe(ASTRA_AG_UI_PACKAGE_VERSION);
    expect(packageJson.dependencies['@ag-ui/client']).toBe(ASTRA_AG_UI_PACKAGE_VERSION);
  });

  it('keeps golden input and every public event compatible with @ag-ui/core', () => {
    expect(RunAgentInputSchema.safeParse(golden.input).success).toBe(true);
    for (const event of Object.values(golden.events)) {
      expect(EventSchemas.safeParse(event), JSON.stringify(event)).toMatchObject({ success: true });
    }
  });

  it('keeps the advertised standard capability fields schema-compatible', () => {
    expect(AgentCapabilitiesSchema.safeParse(golden.capabilities).success).toBe(true);
  });

  it('retains the native transport as an immediate rollback path', () => {
    const agUi = { name: 'ag-ui' } as unknown as AstraAgentTransport;
    const native = { name: 'native' } as unknown as AstraAgentTransport;
    expect(selectAstraTransport(true, agUi, native)).toBe(agUi);
    expect(selectAstraTransport(false, agUi, native)).toBe(native);
  });

  it('never replaces the product UI on an ordinary application route', () => {
    expect(shouldRenderAgUiPreview({ mode: 'development', enabled: 'true', pathname: '/' })).toBe(false);
    expect(shouldRenderAgUiPreview({ mode: 'production', enabled: 'true', pathname: '/__dev/ag-ui' })).toBe(false);
    expect(shouldRenderAgUiPreview({ mode: 'development', enabled: undefined, pathname: '/__dev/ag-ui' })).toBe(false);
    expect(shouldRenderAgUiPreview({ mode: 'development', enabled: 'true', pathname: '/__dev/ag-ui' })).toBe(true);
  });
});
