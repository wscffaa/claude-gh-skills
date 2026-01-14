# PM Methodology for Task Breakdown

## Core Principles

### Discovery-Driven Approach
- Requirements emerge from understanding user needs, not template filling
- Ask "WHY?" repeatedly to uncover true requirements
- Ship the smallest thing that validates assumptions - iteration over perfection
- User value first, technical feasibility is a constraint not the driver

### Complexity Assessment

**Simple Task Indicators:**
- Single feature or bug fix
- Affects 1-3 files
- Clear acceptance criteria
- No cross-team dependencies
- Can be completed in one development session

**Complex Task Indicators:**
- Multiple features or significant architectural changes
- Affects 4+ files or multiple services
- Requires coordination across teams
- Has unclear requirements or multiple valid approaches
- Needs phased rollout or feature flags

## Task Breakdown Process

### Phase 1: Requirements Discovery
1. Understand the problem statement and user impact
2. Identify stakeholders and affected users
3. Define success metrics and acceptance criteria
4. Document technical constraints and dependencies

### Phase 2: Decomposition Strategy

**For Simple Tasks:**
- Create single issue with clear acceptance criteria
- Include technical notes and implementation hints
- Add appropriate labels (bug, feature, enhancement, etc.)

**For Complex Tasks:**
- Create Epic issue as parent
- Break down into sub-issues (stories/tasks)
- Each sub-issue should be independently deliverable
- Establish dependency order and priority

### Phase 3: Prioritization Framework

**Priority Levels:**
- **P0 (Critical)**: Blocking production, security issues, data loss
- **P1 (High)**: Core functionality, significant user impact
- **P2 (Medium)**: Important but not urgent, quality improvements
- **P3 (Low)**: Nice-to-have, technical debt, optimizations

**Dependency Types:**
- **Blocking**: Must complete before dependent tasks can start
- **Related**: Should be aware of but not blocking
- **Parallel**: Can be worked on simultaneously

### Phase 4: Epic Structure

**Epic Issue Template:**
```markdown
## Overview
[High-level description of the feature/project]

## Goals
- [Primary goal]
- [Secondary goals]

## Sub-Issues
- [ ] #123 - [Sub-issue title] (P1, Blocking)
- [ ] #124 - [Sub-issue title] (P2, Depends on #123)
- [ ] #125 - [Sub-issue title] (P2, Parallel)

## Success Criteria
- [Measurable outcome 1]
- [Measurable outcome 2]

## Technical Notes
[Architecture decisions, constraints, risks]
```

**Sub-Issue Guidelines:**
- Each sub-issue should be completable in 1-3 days
- Include clear acceptance criteria
- Reference parent epic with label
- Document dependencies explicitly

## Labels Strategy

**Standard Labels:**
- `epic` - Parent issue tracking multiple sub-issues
- `epic:<name>` - Links sub-issues to specific epic
- `priority:p0/p1/p2/p3` - Priority level
- `type:feature/bug/enhancement/refactor` - Issue type
- `status:blocked/in-progress/review` - Current state
- `area:<component>` - Affected codebase area

## Validation Checklist

Before creating issues, verify:
- [ ] Problem statement is clear and specific
- [ ] Acceptance criteria are testable
- [ ] Dependencies are identified
- [ ] Priority is justified
- [ ] Labels are appropriate
- [ ] For epics: sub-issues are properly scoped
- [ ] For epics: dependency order is logical
