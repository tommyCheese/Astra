## ADDED Requirements

### Requirement: Learned behavior remains a governed candidate
The system SHALL represent learned procedures and policy recommendations as immutable, versioned evolution candidates and SHALL NOT directly modify active Skills, prompts, routing policy, permissions, or security configuration when a candidate is created.

#### Scenario: Successful Runs yield a procedure
- **WHEN** repeated verified Runs support a reusable workflow
- **THEN** the system may create a draft procedure candidate linked to those Runs, evaluations, Memory records, and environment constraints
- **THEN** no active Agent behavior changes

#### Scenario: Candidate proposes security relaxation
- **WHEN** candidate content attempts to lower an approval, sandbox, permission, credential, retention, or evidence requirement
- **THEN** the system rejects or quarantines the candidate

### Requirement: Candidate lifecycle is constrained and auditable
The system SHALL enforce draft, evaluating, rejected, approved, shadow, canary, promoted, and rolled-back states with expected-version checks and actor, reason, and timestamp audit data.

#### Scenario: Stale approval request
- **WHEN** an approval targets an older candidate version
- **THEN** the system rejects the transition without changing the current version

#### Scenario: Rejected candidate is revised
- **WHEN** new evidence addresses a rejected candidate's failure
- **THEN** the system creates a new candidate version rather than editing the rejected version

### Requirement: Evaluation is required before promotion
The system SHALL require a versioned evaluation manifest with baseline and candidate results, representative and held-out cases, safety checks, sample size, cost and latency effects, and configured regression thresholds before a candidate can enter Shadow, Canary, or promoted state.

#### Scenario: Candidate improves training cases but regresses safety
- **WHEN** evaluation shows task improvement and any protected safety metric regression
- **THEN** the candidate cannot advance to Shadow, Canary, or promotion

#### Scenario: Evaluation lacks baseline
- **WHEN** a candidate has results without a comparable baseline manifest
- **THEN** the system keeps it in draft or evaluating state

### Requirement: Promotion cannot expand authority
The system SHALL derive the candidate's executable ceiling from current runtime policy and SHALL prohibit evolution from enabling unavailable Tools, increasing permission scope, bypassing approvals, accessing new credentials, or changing security floors.

#### Scenario: Procedure references disabled tool
- **WHEN** an approved procedure references a Tool disabled for the current Run
- **THEN** runtime eligibility excludes that action
- **THEN** the candidate does not enable or authorize the Tool

### Requirement: Rollout and rollback are measurable
The system SHALL associate Shadow, Canary, promotion, and rollback with a frozen candidate version, evaluation manifest, audience or traffic boundary, observed metrics, and rollback criteria.

#### Scenario: Canary exceeds regression threshold
- **WHEN** a Canary candidate exceeds its configured failure, cost, latency, or safety regression threshold
- **THEN** the system records a rollback decision and stops selecting that candidate
- **THEN** historical executions remain linked to the exact candidate version used

### Requirement: Initial rollout does not mutate production behavior
The initial implementation SHALL support candidate creation, inspection, evaluation attachment, approval or rejection, and rollback metadata while keeping automatic production promotion disabled.

#### Scenario: Approved candidate exists
- **WHEN** a candidate is approved but production promotion is disabled
- **THEN** it remains non-executable or limited to an explicitly invoked evaluation environment

#### Scenario: Unsupported rollout transition is requested
- **WHEN** a request attempts to move a candidate into Shadow, Canary, or promoted state while production promotion is disabled
- **THEN** the system rejects the transition with a typed disabled or conflict result
- **THEN** it does not imply that the candidate affects serving behavior

### Requirement: Evolution evidence follows source deletion
The system SHALL track the source Memory, Runs, evaluations, and case references supporting each evolution candidate and SHALL revoke, redact, or revalidate derived candidates before a source is deleted.

#### Scenario: Candidate loses its only supporting source
- **WHEN** conversation deletion removes the only valid source supporting a draft, evaluating, or approved candidate
- **THEN** the system revokes or rejects the candidate before source deletion completes
- **THEN** candidate retrieval and evaluation no longer expose deleted private content

#### Scenario: Candidate retains independent support
- **WHEN** a deleted source is one of multiple independent valid sources
- **THEN** the system removes the source reference and revalidates the candidate
- **THEN** it remains available only if the remaining manifest still satisfies evidence requirements
