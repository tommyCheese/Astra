import { describe, expect, it } from 'vitest';
import { sourceAnchor, validatedCitations } from '../src/groundingPresentation';
import type { RunResult } from '../src/types';

function result(): RunResult {
  return {
    summary: 'Grounded result',
    findings: [],
    claims: [{
      id: 'claim-1',
      text: 'Grounded result',
      evidence_refs: ['ev-1'],
      material: true,
      support_status: 'supported',
    }],
    citations: [{
      id: 'citation-1',
      claim_id: 'claim-1',
      evidence_ref: 'ev-1',
      url: 'https://example.com/source',
      ordinal: 1,
    }],
    sources: [{ url: 'https://example.com/source' }],
    failed_sources: [],
    source_quality: [],
    conflicts: [],
    caveats: [],
    verification_notes: [],
    memory_references: [],
    audit_refs: {
      evidence_record_count: 2,
      agent_turn_count: 1,
      referenced_artifact_ids: [],
    },
    verification_report: null,
    completion_decision: null,
    error: null,
  };
}

describe('grounding presentation', () => {
  it('keeps only citations bound to declared claims, evidence, and sources', () => {
    const value = result();
    value.citations.push({
      id: 'invented',
      claim_id: 'claim-1',
      evidence_ref: 'missing',
      url: 'https://malicious.example',
    });
    expect(validatedCitations(value)).toHaveLength(1);
    expect(validatedCitations(value)[0].sourceIndex).toBe(0);
  });

  it('creates stable source anchors', () => {
    expect(sourceAnchor(0)).toBe('grounded-source-1');
  });

  it('binds canonical citations to source cards with tracking URLs', () => {
    const value = result();
    value.sources[0].url = 'https://EXAMPLE.com/source/?utm_source=search';
    value.citations[0].url = 'https://example.com/source';

    expect(validatedCitations(value)[0].sourceIndex).toBe(0);
  });
});
