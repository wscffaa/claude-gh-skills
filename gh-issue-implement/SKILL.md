---
name: gh-issue-implement
description: 实现单个 Issue：获取详情→开发→创建 PR。
---

# GitHub Issue Implementation

Implement GitHub issues with full development lifecycle from issue analysis to PR creation.

## Purpose

Drive the complete issue-to-PR workflow: fetch issue details, analyze requirements, execute development via codeagent-wrapper, track progress, and create a pull request that closes the issue.

## When to Use

Trigger this skill when:
- User provides an issue number to implement (e.g., "implement issue #123")
- User requests "work on issue [number]"
- User asks to "develop" or "fix" a specific GitHub issue
- User mentions "implement gh issue" or similar phrases

## Workflow

### Phase 1: Issue Analysis

1. **Fetch Issue Details:**
   ```bash
   gh issue view $ISSUE_NUMBER --json title,body,labels,comments,assignees
   ```

2. **Parse Requirements:**
   - Extract problem statement from issue body
   - Identify acceptance criteria (look for checkbox lists)
   - Note technical constraints from "Technical Notes" section
   - Review comments for clarifications or updates
   - Check labels for priority and type information

3. **Codebase Exploration:**
   - Identify affected files and components
   - Understand existing patterns and architecture
   - Locate relevant tests and documentation
   - Use Glob/Grep to find related code

4. **Derive Task List:**
   - Break down acceptance criteria into concrete tasks
   - Identify files that need modification
   - Plan test coverage requirements
   - Note any dependencies or blockers

### Phase 2: Clarification (if needed)

Use `AskUserQuestion` when:
- Requirements are ambiguous or incomplete
- Multiple valid implementation approaches exist
- Scope boundaries are unclear
- Testing strategy needs confirmation

Present lean implementation options with trade-offs when choices exist. Confirm approach before proceeding to development.

### Phase 3: Development

**For Simple/Focused Issues:**
Invoke codeagent-wrapper:
```bash
# Create prompt file with requirements
cat > /tmp/codeagent-prompt.txt << PROMPT
实现 Issue #{issue_number}: {issue_title}

Requirements:
- [从 issue 解析出的需求列表]

Deliverables:
- 代码实现
- 单元测试 (覆盖率 ≥90%)
- 更新相关文档
PROMPT

# Execute codeagent-wrapper with input file
codeagent-wrapper --backend codex - < /tmp/codeagent-prompt.txt
```

Pass the parsed requirements and acceptance criteria into the wrapper payload. The wrapper will:
- Execute development with the specified backend
- Enforce 90% test coverage
- Validate all acceptance criteria

**For Complex Features:**
Use the same wrapper approach with a more detailed requirements list and acceptance criteria, splitting deliverables into incremental batches as needed.

### Phase 4: Progress Updates

After each significant milestone:
```bash
gh issue comment $ISSUE_NUMBER --body "✅ Completed: [milestone description]

[Brief summary of changes made]"
```

Update frequency:
- After completing each acceptance criterion
- When encountering blockers or issues
- Before creating the PR

### Phase 5: PR Creation

1. **Verify Completion:**
   - All acceptance criteria met
   - Tests passing (≥90% coverage)
   - Code follows project conventions
   - No outstanding blockers

2. **Create Branch (if not already on one):**
   ```bash
   git checkout -b issue-$ISSUE_NUMBER
   ```

3. **Commit Changes:**
   ```bash
   git add -A
   git commit -m "feat: implement issue #$ISSUE_NUMBER

   [Brief description of changes]

   Closes #$ISSUE_NUMBER"
   ```

4. **Push and Create PR:**
   ```bash
   git push -u origin issue-$ISSUE_NUMBER

   # Create PR body file
   cat > /tmp/pr-body.md << BODY
   ## Summary
   Implements #$ISSUE_NUMBER

   ## Changes
   - [Key change 1]
   - [Key change 2]

   ## Testing
   - [Test coverage summary]
   - All acceptance criteria verified

   ## Closes
   Closes #$ISSUE_NUMBER
   BODY

   gh pr create \
     --title "Fix #$ISSUE_NUMBER: [Issue title]" \
     --body-file /tmp/pr-body.md
   ```

5. **Return PR URL:**
   Display the created PR URL and summary of changes.

### Phase 6: Error Handling

**If Issue Fetch Fails:**
- Verify issue number is correct
- Check repository access permissions
- Surface gh CLI error message

**If Development Blocked:**
- Post comment on issue explaining blocker
- Ask user for guidance or clarification
- Document blocker in PR description if proceeding

**If PR Creation Fails:**
- Check for merge conflicts
- Verify branch is up to date with base
- Ensure all changes are committed
- Surface error message and suggest resolution

## Integration with Codeagent Wrapper

This skill delegates actual development to `codeagent-wrapper` with a structured prompt that includes the issue context, requirements, and deliverables. Use the wrapper to specify the backend (e.g., `codex`) and keep all development work, tests, and documentation updates encapsulated in the wrapper execution.

The wrapper handles code generation, testing, and quality assurance, while this skill manages the GitHub-specific workflow (issue tracking, PR creation, progress updates).

## Best Practices

**Issue Comments:**
- Keep updates concise and actionable
- Use checkboxes to show progress
- Tag relevant stakeholders when needed
- Document any deviations from original plan

**PR Description:**
- Link to issue with "Closes #N" syntax
- Summarize key changes clearly
- Include testing approach and coverage
- Note any breaking changes or migrations

**Branch Naming:**
- Use `issue-N` or `fix-N` format
- Keep branch names short and descriptive
- Delete branch after PR merge

## Example Usage

```
User: "Implement issue #42"

Skill Actions:
1. Fetch issue #42 details
2. Parse: "Add user authentication with JWT"
3. Explore codebase for auth patterns
4. Invoke codeagent-wrapper with requirements
5. Post progress updates to issue
6. Create PR linking to issue #42
```
