---
name: gh-pr-review
description: PR 代码审查、问题修复、CI 验证与合并。
---

# GitHub PR Review

Comprehensive PR review workflow with automated code analysis, issue resolution, and merge execution.

## Purpose

Drive thorough PR review cycles: analyze code changes using codeagent, verify CI status, fix all identified issues, and merge when ready. Supports both single PR review and epic batch review modes.

## When to Use

Trigger this skill when:
- User provides a PR number to review (e.g., "review PR #123")
- User requests "review issue:45" for epic batch review
- User asks to "merge PR" or "check PR status"
- User mentions "code review" with a PR reference

## Workflow Modes

### Mode 1: Single PR Review

For reviewing individual pull requests.

**Input:** PR number (e.g., "123")

### Mode 2: Epic Batch Review

For reviewing all PRs linked to an epic issue.

**Input:** "issue:N" format (e.g., "issue:45")

## Workflow

### Phase 0: Input Detection

1. **Parse Input:**
   - If `$ARGUMENTS` starts with `issue:` → Epic mode (go to Phase 0.1)
   - If `$ARGUMENTS` is a number → Single PR mode (go to Phase 1)
   - If no argument → List open PRs and ask user to select

2. **List Open PRs (if no argument):**
   ```bash
   gh pr list --json number,title,author,updatedAt
   ```
   Display formatted list and use `AskUserQuestion` to select PR.

### Phase 0.1: Epic Batch Review Mode

For epic issues with multiple sub-issues and linked PRs:

1. **Extract Epic Issue Number:**
   ```bash
   EPIC_NUMBER=$(echo "$ARGUMENTS" | sed 's/issue://')
   ```

2. **Fetch Epic Details:**
   ```bash
   gh issue view $EPIC_NUMBER --json title,body,labels
   ```

3. **Parse Sub-Issues:**
   Look for patterns in issue body:
   - `- [ ] #123` or `- [x] #123`
   - `Depends on #123`
   - `Closes #123`

4. **Find Linked PRs:**
   For each sub-issue:
   ```bash
   gh pr list --search "linked:issue:$SUB_ISSUE_NUMBER" --json number,title,state
   ```
   Or parse PR links from sub-issue body/comments.

5. **Build PR Queue:**
   Order PRs by sub-issue dependency (blocking issues first).

6. **Display Summary:**
   ```
   Found N sub-issues with M linked PRs. Proceeding with batch review:
   - Sub-issue #X → PR #Y
   - Sub-issue #A → PR #B
   ```

7. **Process Each PR:**
   Execute Phase 1-6 for each PR in the queue.

8. **Post Epic Summary:**
   After all PRs reviewed:
   ```bash
   gh issue comment $EPIC_NUMBER --body "## Epic Review Complete

   | Sub-Issue | PR | Status |
   |-----------|-----|--------|
   | #sub1 | #pr1 | ✅ Merged |
   | #sub2 | #pr2 | ✅ Merged |

   All PRs reviewed and merged successfully.

   Reviewed by Claude Code"
   ```

### Phase 1: PR Discovery & Context

1. **Fetch PR Details:**
   ```bash
   gh pr view $PR_NUMBER --json title,body,author,baseRefName,headRefName,url
   ```

2. **Get PR Diff:**
   ```bash
   gh pr diff $PR_NUMBER
   ```

3. **Check CI Status:**
   ```bash
   gh pr checks $PR_NUMBER --json name,status,conclusion
   ```

4. **Check for Linked Issues:**
   Parse PR body for "Closes #N" or "Fixes #N" patterns.
   If found, fetch issue details for context.

### Phase 2: Deep Code Review via Codeagent

Delegate comprehensive code review to codeagent:

```bash
# Use codeagent skill for review
# Pass the PR diff and context
```

Review focus areas:
- **Correctness:** Logic errors, edge cases, null handling
- **Conventions:** Project patterns, naming, structure
- **Performance:** Inefficient algorithms, unnecessary operations
- **Tests:** Coverage gaps, missing test cases
- **Security:** Input validation, auth checks, data exposure

Categorize findings:
- **[Critical]:** Must fix before merge (bugs, security issues)
- **[Suggestions]:** Nice-to-have improvements
- **[Approved]:** No issues found

### Phase 3: CI Analysis

If any checks failed:

1. **Get Failed Run Details:**
   ```bash
   gh run view $RUN_ID --log-failed
   ```

2. **Diagnose Root Cause:**
   Categorize failure type:
   - Test failure (which tests, why)
   - Lint error (which rules, where)
   - Build error (compilation, dependencies)
   - Other (deployment, integration)

3. **Document Findings:**
   Add to issues list with specific error messages.

### Phase 4: Issue Resolution Loop

If [Critical] issues or failed checks exist:

1. **Fix Each Issue:**
   Use codeagent skill to fix:
   ```bash
   # Invoke codeagent with specific fix instructions
   ```

2. **Commit and Push:**
   ```bash
   git add -A
   git commit -m "fix: [specific issue description]"
   git push
   ```

3. **Wait for CI:**
   Monitor CI re-run:
   ```bash
   gh pr checks $PR_NUMBER --watch
   ```

4. **Iterate:**
   Repeat until all checks pass.
   Maximum 3 fix iterations before escalating to user.

5. **Track Progress:**
   Use `TodoWrite` to track fix progress.

### Phase 5: Review Summary & Approval

Once all issues resolved and CI green:

1. **Post Review Comment:**
   ```bash
   gh pr review $PR_NUMBER --approve --body "$(cat <<'EOF'
   ## Code Review Summary

   ### Changes Reviewed
   - [List key changes from diff]

   ### Review Findings
   - [Findings or "No issues found"]

   ### CI Status
   - ✅ All checks passing

   ### Verdict
   Approved for merge.

   Reviewed by Claude Code
   EOF
   )"
   ```

2. **If Fixes Were Made:**
   Add additional comment detailing fixes:
   ```bash
   gh pr comment $PR_NUMBER --body "## Fixes Applied

   - Fixed [issue 1]
   - Fixed [issue 2]

   All issues resolved. Ready for merge."
   ```

### Phase 6: Merge Execution

1. **Verify Merge Readiness:**
   ```bash
   gh pr view $PR_NUMBER --json mergeable,mergeStateStatus
   ```

2. **Check for Conflicts:**
   If conflicts exist:
   - Notify user
   - Provide rebase instructions
   - Stop (do not merge)

3. **Select Merge Strategy:**
   Default to squash merge (clean history).
   Only ask user if they want different strategy.

4. **Execute Merge:**
   ```bash
   gh pr merge $PR_NUMBER --squash --delete-branch
   ```

5. **Return Result:**
   Display merged PR URL and summary.

### Phase 7: Error Handling

**If CI Keeps Failing (after 3 iterations):**
- Summarize blockers clearly
- Post comment on PR with findings
- Ask user for guidance
- Do not merge

**If PR Has Conflicts:**
- Notify user immediately
- Provide rebase command:
  ```bash
  git fetch origin
  git rebase origin/main
  git push --force-with-lease
  ```
- Stop workflow

**If PR Already Merged/Closed:**
- Report current status
- Exit gracefully

**If gh Command Fails:**
- Surface stderr message
- Check permissions and repository access
- Stop workflow

## Integration with Codeagent

This skill uses the `codeagent` skill for:
- Comprehensive code review analysis
- Automated issue fixing
- Test coverage verification
- Code quality validation

The codeagent skill provides deep code understanding and can execute fixes autonomously while this skill manages the GitHub workflow.

## Best Practices

**Review Comments:**
- Be specific about issues found
- Provide context and reasoning
- Suggest concrete improvements
- Acknowledge good practices

**Merge Strategy:**
- Squash merge for feature branches (clean history)
- Merge commit for release branches (preserve history)
- Rebase for linear history (when appropriate)

**CI Verification:**
- Always wait for CI to complete
- Never merge with failing checks
- Investigate flaky tests before merging

## Example Usage

**Single PR Review:**
```
User: "Review PR #42"

Skill Actions:
1. Fetch PR #42 details and diff
2. Run codeagent code review
3. Check CI status (2 tests failing)
4. Fix failing tests with codeagent
5. Wait for CI to pass
6. Post approval comment
7. Squash merge and delete branch
```

**Epic Batch Review:**
```
User: "Review issue:10"

Skill Actions:
1. Fetch epic issue #10
2. Find 3 sub-issues with linked PRs
3. Review PR #15 (sub-issue #11) → Merge
4. Review PR #16 (sub-issue #12) → Merge
5. Review PR #17 (sub-issue #13) → Merge
6. Post summary on epic issue #10
```
