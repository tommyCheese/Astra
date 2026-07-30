import type { GroundedClaim, GroundingCitation, RunResult } from './types';

export type PresentedCitation = GroundingCitation & {
  claim: GroundedClaim;
  sourceIndex: number;
  ordinal: number;
};

export function sourceAnchor(index: number) {
  return `grounded-source-${index + 1}`;
}

function comparableSourceUrl(value: string) {
  try {
    const url = new URL(value);
    for (const key of [...url.searchParams.keys()]) {
      const normalized = key.toLocaleLowerCase();
      if (normalized.startsWith('utm_') || ['fbclid', 'gclid', 'msclkid'].includes(normalized)) {
        url.searchParams.delete(key);
      }
    }
    url.searchParams.sort();
    url.hash = '';
    url.hostname = url.hostname.toLocaleLowerCase();
    url.pathname = url.pathname.replace(/\/+$/u, '') || '/';
    return url.toString();
  } catch {
    return value.trim();
  }
}

export function validatedCitations(result: RunResult): PresentedCitation[] {
  const claims = new Map(result.claims.map((claim) => [claim.id, claim]));
  const sourceKeys = result.sources.map((source) => comparableSourceUrl(source.url));
  const seen = new Set<string>();
  return result.citations.flatMap((citation, index) => {
    const claim = claims.get(citation.claim_id);
    if (!claim || !claim.evidence_refs.includes(citation.evidence_ref) || !citation.url) {
      return [];
    }
    const sourceIndex = sourceKeys.indexOf(comparableSourceUrl(citation.url));
    if (sourceIndex < 0) return [];
    const key = `${citation.claim_id}:${citation.evidence_ref}`;
    if (seen.has(key)) return [];
    seen.add(key);
    return [{
      ...citation,
      claim,
      sourceIndex,
      ordinal: citation.ordinal ?? index + 1,
    }];
  });
}

export function citationsForClaim(
  citations: PresentedCitation[],
  claimId: string,
): PresentedCitation[] {
  return citations.filter((citation) => citation.claim_id === claimId);
}
