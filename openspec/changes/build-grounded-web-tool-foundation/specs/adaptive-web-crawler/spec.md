## ADDED Requirements

### Requirement: Adaptive extraction emits immutable snapshot metadata
The adaptive crawler SHALL emit canonical URL, normalized content digest, segmentation version, source identity, snapshot identity, bounded passages, and extraction signals for every successful supported response.

#### Scenario: HTML extraction succeeds
- **WHEN** the crawler extracts readable HTML content
- **THEN** the output includes immutable snapshot metadata and at least one passage when normalized content is non-empty

### Requirement: Full content remains bounded and artifact compatible
The crawler SHALL keep model-visible content and passage text within configured bounds while preserving enough digest and offset metadata to bind the output to an immutable Artifact snapshot.

#### Scenario: Source exceeds content limit
- **WHEN** normalized source content exceeds the configured maximum
- **THEN** returned content is bounded, truncation is disclosed, and snapshot identity is based on the normalized retained snapshot
