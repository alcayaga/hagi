## Summary

<!-- Concisely describe what this PR changes and why. Focus on impact and
urgency. -->

## Details

<!-- Add any extra context and design decisions. Keep it brief but complete. -->

## Related Issues

<!-- Use keywords to auto-close issues (Closes #123, Fixes #456). If this PR is
only related to an issue or is a partial fix, simply reference the issue number
without a keyword (Related to #123). -->

## How to Validate

<!-- List exact steps for reviewers to validate the change. Include commands,
expected results, and edge cases. -->

## Pre-Merge Checklist

<!-- You MUST mark EVERY checkbox below with an [x]. 
Do not leave any checkbox empty. By checking a box, you are verifying that you 
have either completed the task or confirmed it is not applicable. -->

- [ ] **Tests:** I have added/updated tests. <!-- (REQUIRED if this PR adds new functionality. If no new functionality was added, check this box to confirm tests are not applicable).  -->
- [ ] **Breaking Changes:** I have evaluated the code for breaking changes and explicitly declared them in the PR details (or verified there are none).
- [ ] **Documentation:** I have updated relevant documentation and README (or verified no updates are needed).
- [ ] **Validation:** I have validated on the required platforms/methods:
  - [ ] MacOS
    - [ ] `PYTHONPATH=. conda run -n hagi pytest`
