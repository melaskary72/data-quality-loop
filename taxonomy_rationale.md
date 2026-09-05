# Taxonomy rationale

This is the agent's own reasoning, kept verbatim as a build artifact.
It is not edited, because the point of keeping it is to show what the
agent actually argued, including anything a reviewer would push back on.

## The agent's reasoning

Taxonomy reduced from 5 domains and 17 leaves to 4 domains and 12 leaves by consolidating overlapping categories. Key merges: (1) Merged 'refund_request' and 'duplicate_or_erroneous_charge' into 'billing_dispute_or_refund' because both require investigation and often result in refunds or credits—the boundary examples now cover both scenarios (duplicate charges, post-cancellation charges, outage-based refunds, and line item disputes). (2) Merged 'performance_degradation' and 'rate_limit_exceeded' into 'performance_or_rate_limit_issue' because both manifest as blocking/slow operations and share identical remediation paths (infrastructure investigation, quota adjustment, etc.)—boundary examples now distinguish timeout/hang scenarios from explicit rate limit errors. (3) Merged 'access_control_configuration' and 'user_provisioning_request' into 'user_provisioning_and_access' because they represent the same operational workflow (configuring who has access and to what)—boundary examples cover adding users and restricting feature visibility. (4) Moved 'session_or_account_unlock' criteria into 'login_failure' since both are authentication blocking issues requiring the same resolution path; boundary examples now clarify that account lockouts (after failed attempts, requiring admin reset) fall under login_failure. This consolidation reduces cognitive load for annotators while maintaining coverage of all ticket types in the corpus.

## Structure

### authentication_and_access

Issues related to user authentication, login, password management, permission enforcement, and access control to platform features

- **login_failure**: Users cannot authenticate or are blocked from logging in due to redirect loops, error codes, or session issues, including account lockouts after failed attempts
- **password_reset_broken**: Password reset emails are not being sent or received, or reset links expire before users can use them
- **permission_not_enforced**: Users have access to data or features they should not based on their assigned role, or permissions granted in role definitions are not taking effect
- **role_assignment_or_removal**: Issues with assigning, modifying, or removing user roles and permissions, including bulk operations and post-migration role problems

### billing_and_subscription

Issues related to invoicing, charges, payment processing, subscription changes, and billing discrepancies

- **billing_dispute_or_refund**: Customer reports being charged incorrectly, requests refund or credit, or disputes charge accuracy including duplicate charges, unauthorized charges, or charges not matching agreements
- **subscription_change_request**: Customer wants to modify their subscription plan, seat count, or billing frequency (upgrade, downgrade, or switch to different plan/tier)
- **subscription_plan_question**: Customer asks clarifying questions about how their current plan works, what is included, or how charges are calculated, without requesting a change

### product_and_feature_issues

Issues related to bugs, performance problems, broken functionality, and integrations in the platform

- **performance_or_rate_limit_issue**: Platform or feature is unusually slow, hangs, times out, or user is being rate limited on API endpoints with limits lower than documented or expected
- **data_sync_or_integration_broken**: Integrations with external systems are not working, data is not syncing, or connectors are failing
- **documentation_or_setup_question**: User is asking how to set up, configure, or use a feature; documentation is conflicting or unclear
- **feature_request**: Customer is requesting new functionality, enhancements, or capabilities that do not currently exist

### account_management_and_operations

Issues related to workspace configuration, user provisioning, account operations, and administrative tasks

- **user_provisioning_and_access**: Customer needs to add, provision, or configure access for new user accounts in bulk or for specific purposes, or modify access control settings and permission boundaries

## Coverage on the induction sample

- sample size: 120
- unmappable: 2 (1.7%, ceiling 5%)
- structural repair attempts needed: 1

| Leaf | Tickets in sample |
|---|---|
| billing_dispute_or_refund | 22 |
| documentation_or_setup_question | 16 |
| performance_or_rate_limit_issue | 13 |
| feature_request | 12 |
| login_failure | 11 |
| data_sync_or_integration_broken | 10 |
| subscription_change_request | 8 |
| permission_not_enforced | 8 |
| user_provisioning_and_access | 5 |
| role_assignment_or_removal | 5 |
| subscription_plan_question | 5 |
| password_reset_broken | 3 |
| unmappable | 2 |
