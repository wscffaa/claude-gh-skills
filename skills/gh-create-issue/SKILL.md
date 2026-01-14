---
name: gh-create-issue
description: This skill should be used when creating GitHub issues from PRD documents or user requirements. Automatically assesses task complexity and creates either single issues for simple tasks or epic issues with sub-issues for complex tasks. Applies PM-level task breakdown, prioritization, and dependency management using gh CLI.
---

# GitHub Issue Creator

Create structured GitHub issues with automatic complexity assessment and PM-level task breakdown.

## Purpose

Transform requirements or PRD documents into well-structured GitHub issues. For simple tasks, create a single focused issue. For complex tasks, create an epic issue with properly scoped sub-issues, complete with labels, priorities, and dependency tracking.

## When to Use

Trigger this skill when:
- User provides a PRD document or feature requirements
- User requests "create issue for [description]"
- User asks to break down a complex feature into trackable tasks
- User mentions "epic", "sub-issues", or "task breakdown"

## Workflow

### Phase 1: Complexity Assessment

Analyze the request to determine task complexity:

**Simple Task Indicators:**
- Single feature or bug fix
- Affects 1-3 files
- Clear acceptance criteria
- No cross-team dependencies
- Completable in one session

**Complex Task Indicators:**
- Multiple features or architectural changes
- Affects 4+ files or services
- Cross-team coordination needed
- Unclear requirements or multiple approaches
- Needs phased rollout

**Decision:** If 2+ complex indicators present, proceed with Epic mode. Otherwise, use Simple mode.

### Phase 2A: Simple Issue Creation

For simple tasks:

1. **Gather Requirements** (use `AskUserQuestion` if needed):
   - Problem statement and user impact
   - Expected outcome and scope
   - Acceptance criteria (testable)
   - Technical constraints

2. **Structure Issue:**
   ```markdown
   ## Problem Statement
   [Why this matters and who is impacted]

   ## Proposed Solution
   [High-level approach]

   ## Acceptance Criteria
   - [ ] [Testable criterion 1]
   - [ ] [Testable criterion 2]

   ## Technical Notes
   [Constraints, dependencies, risks]
   ```

3. **Create Issue:**
   ```bash
   gh issue create --title "[Type] Brief description" \
     --body "<markdown body>" \
     --label "type:feature,priority:p1"
   ```

4. Return the created issue URL.

### Phase 2B: Epic Issue Creation

For complex tasks:

1. **Requirements Discovery:**
   - Use `AskUserQuestion` to clarify scope, goals, and constraints
   - Identify affected components and teams
   - Define success metrics
   - Load `references/pm-methodology.md` for detailed guidance

2. **Task Decomposition:**
   - Break down into independently deliverable sub-tasks
   - Each sub-task should be completable in 1-3 days
   - Identify dependencies between sub-tasks
   - Assign priorities (P0/P1/P2/P3)

3. **Create Epic Label:**
   ```bash
   # Generate unique epic identifier
   EPIC_NAME="epic:$(echo "$TITLE" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | cut -c1-20)"
   gh label create "$EPIC_NAME" --description "Epic: $TITLE" --color "0366d6" || true
   ```

4. **Create Epic Issue:**
   ```markdown
   ## Overview
   [High-level description]

   ## Goals
   - [Primary goal]
   - [Secondary goals]

   ## Sub-Issues
   [Will be populated after sub-issues are created]

   ## Success Criteria
   - [Measurable outcome 1]
   - [Measurable outcome 2]

   ## Technical Notes
   [Architecture decisions, constraints, risks]
   ```

   ```bash
   EPIC_NUMBER=$(gh issue create --title "[Epic] $TITLE" \
     --body "<markdown body>" \
     --label "epic,priority:p1" \
     --json number -q .number)
   ```

5. **Create Sub-Issues:**
   For each sub-task:
   ```bash
   gh issue create --title "[Sub-task] $SUBTASK_TITLE" \
     --body "Part of #$EPIC_NUMBER\n\n$SUBTASK_BODY" \
     --label "$EPIC_NAME,type:feature,priority:p2"
   ```

   Track created sub-issue numbers.

6. **Update Epic with Sub-Issue Links:**
   ```bash
   # Build sub-issues list
   SUB_ISSUES_LIST="## Sub-Issues\n"
   for issue in $SUB_ISSUE_NUMBERS; do
     SUB_ISSUES_LIST+="- [ ] #$issue - [Title] (Priority, Dependencies)\n"
   done

   # Update epic body
   gh issue edit $EPIC_NUMBER --body "<updated markdown with sub-issues>"
   ```

7. Return epic URL and summary of created sub-issues.

### Phase 3: Validation

Before finalizing:
- Verify all issues have clear acceptance criteria
- Confirm dependencies are documented
- Ensure labels are appropriate
- For epics: validate sub-issue scope and order

## Label Strategy

**Standard Labels:**
- `epic` - Parent issue
- `epic:<name>` - Links sub-issues to epic
- `priority:p0/p1/p2/p3` - Priority level
- `type:feature/bug/enhancement/refactor` - Issue type
- `area:<component>` - Affected codebase area

Create labels as needed:
```bash
gh label create "priority:p1" --description "High priority" --color "d93f0b" || true
```

## Error Handling

- If `gh` command fails, surface stderr and stop
- If requirements are unclear, use `AskUserQuestion` to clarify
- If epic creation fails, fall back to simple issue mode
- Maximum 2 clarification rounds before proceeding with best assumptions

## References

For detailed PM methodology, task breakdown strategies, and prioritization frameworks, refer to `references/pm-methodology.md`.
