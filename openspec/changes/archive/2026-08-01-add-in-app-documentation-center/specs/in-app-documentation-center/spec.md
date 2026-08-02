## ADDED Requirements

### Requirement: Global help documentation entry
The Astra application SHALL expose a clearly labelled help documentation control in the persistent application navigation and SHALL indicate when the documentation center is the active view.

#### Scenario: Open documentation from the application shell
- **WHEN** a user activates the “帮助文档” control from any primary application view
- **THEN** Astra displays the in-app documentation center and marks the help control as active

### Requirement: Context-preserving documentation navigation
The documentation center SHALL open without navigating to an external site or clearing the current task state, and SHALL return the user to the view from which it was opened when closed.

#### Scenario: Return to the previous view
- **WHEN** a user opens the documentation center from settings and then activates the close control
- **THEN** Astra returns to settings with the existing application state preserved

#### Scenario: Direct fallback
- **WHEN** the documentation center has no valid previous view and the user closes it
- **THEN** Astra returns to the chat view

### Requirement: Memory is the initial documentation topic
The documentation center SHALL use an extensible topic navigation model and SHALL select “记忆” as its initial and default topic.

#### Scenario: First visit
- **WHEN** a user opens the documentation center
- **THEN** the topic navigation identifies “记忆” as selected and the memory article is visible

### Requirement: Memory documentation explains the complete lifecycle
The memory article SHALL explain the background and user problem, the distinction between `MEMORY.md`, runtime settings, saved memory records, audit activity, and AutoDream, memory production, activation and recall timing, recall modes and safeguards, supported scopes, AutoDream supersession behavior, and common misconceptions.

#### Scenario: Understand when saved memory affects an answer
- **WHEN** a user reads the memory article
- **THEN** the article distinguishes production, active storage, eligible retrieval, and prompt injection instead of implying that every saved memory affects every answer

#### Scenario: Understand scope
- **WHEN** a user consults the scope section
- **THEN** the article explains run, task, workspace, and user scope with both matching boundaries and practical examples

#### Scenario: Understand AutoDream
- **WHEN** a user consults the AutoDream section
- **THEN** the article explains that successful consolidation creates a replacement version and supersedes source memories rather than hard-deleting them

### Requirement: Documentation center is readable and accessible
The documentation center SHALL provide semantic navigation and article landmarks, keyboard-operable controls, visible focus states, and a responsive single-column layout on narrow screens.

#### Scenario: Narrow viewport
- **WHEN** the documentation center is rendered at a narrow viewport width
- **THEN** topic navigation and article content remain readable without requiring horizontal page scrolling

#### Scenario: Assistive navigation
- **WHEN** a user navigates the documentation center with a keyboard or assistive technology
- **THEN** the help entry, topic selection, article sections, and close control expose meaningful accessible labels and states
