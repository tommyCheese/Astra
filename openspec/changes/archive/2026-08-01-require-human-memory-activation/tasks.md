## 1. Candidate lifecycle and activation API

- [x] 1.1 Change ordinary Memory extraction to persist new and changed stable-key records as candidates without automatic activation, while retaining safe deduplication
- [x] 1.2 Implement atomic human activation for standalone and replacement candidates with source validation, state-version conflicts, supersession, and audit records
- [x] 1.3 Add the activation request schema and `POST /api/memories/{id}/activate` endpoint with stable validation and error responses
- [x] 1.4 Add repository, runtime, recall, and API tests covering pending exclusion, activation, rejection, inaccessible sources, concurrent decisions, and replacement behavior

## 2. Human confirmation list

- [x] 2.1 Add frontend activation types/client support and extend the MemoryWorkbench client contract
- [x] 2.2 Build separate pending-confirmation and all-record views with candidate count, candidate-first details, and refresh-safe selection
- [x] 2.3 Add activation and rejection confirmation flows requiring an audit reason and showing conflicts/errors without optimistic state corruption
- [x] 2.4 Add frontend tests for pending listing, detail review, activation, rejection, and active/history isolation

## 3. Product guidance and verification

- [x] 3.1 Update in-app Memory documentation, settings descriptions, event/status labels, and translations to explain candidate production, human activation, and recall timing
- [x] 3.2 Run focused backend and frontend suites, fix regressions, and validate the OpenSpec change artifacts
