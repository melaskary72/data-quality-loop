# Induction failure report

Induction failed 1 validation check(s). The taxonomy was not locked and no downstream stage will run.

## Problems

- assignments used leaves outside the taxonomy: ['product_and_feature_issues']

Unmappable rate: 1.7% over 2 tickets.

## Proposal as returned

```json
{
  "domains": [
    {
      "name": "authentication_and_access",
      "description": "Issues related to user authentication, login, password management, permission enforcement, and access control to platform features",
      "leaves": [
        {
          "name": "login_failure",
          "definition": "Users cannot authenticate or are blocked from logging in due to redirect loops, error codes, or session issues, including account lockouts after failed attempts",
          "inclusion_criteria": [
            "Users receive errors during login attempt and are returned to login screen",
            "SSO integration or standard authentication flow is broken",
            "User is locked out after multiple failed attempts with no recovery available"
          ],
          "exclusion_criteria": [
            "User simply forgot password and reset flow exists (use password_reset_broken)",
            "User is asking how to invite someone; this is a configuration question"
          ],
          "boundary_examples": [
            "T-0013: Cannot log in since this morning with PERM_3310 error, multiple users affected → login_failure; platform-wide auth issue",
            "T-0177: Locked out after several failed attempts but unlock flow available → login_failure; account locked due to security measure requiring admin intervention"
          ]
        },
        {
          "name": "password_reset_broken",
          "definition": "Password reset emails are not being sent or received, or reset links expire before users can use them",
          "inclusion_criteria": [
            "Password reset emails fail to arrive for users across a domain",
            "Reset link expiration occurs before user can complete password reset",
            "Issue affects multiple users or entire organizations"
          ],
          "exclusion_criteria": [
            "User has a valid password and can log in normally",
            "Issue is specific to a single user's email configuration"
          ],
          "boundary_examples": [
            "T-0059: Password reset emails not arriving for any user on our domain → password_reset_broken; systemic delivery failure",
            "T-0030: User asks How do I invite someone as Workspace Admin → not password_reset_broken; this is a permissions question"
          ]
        },
        {
          "name": "permission_not_enforced",
          "definition": "Users have access to data or features they should not based on their assigned role, or permissions granted in role definitions are not taking effect",
          "inclusion_criteria": [
            "User with restricted role (Read Only, Approver) can access data outside their scope",
            "Role change does not apply until user is removed and re-added",
            "User has permissions that contradict their role definition"
          ],
          "exclusion_criteria": [
            "User is asking how to assign or invite someone to a role",
            "Issue is a feature request for more granular permissions"
          ],
          "boundary_examples": [
            "T-0085: Someone with Read Only role is seeing data they should not → permission_not_enforced; access control failed",
            "T-0121: Changing user's role does not take effect until removed and re-added → permission_not_enforced; permissions not updating"
          ]
        },
        {
          "name": "role_assignment_or_removal",
          "definition": "Issues with assigning, modifying, or removing user roles and permissions, including bulk operations and post-migration role problems",
          "inclusion_criteria": [
            "User needs to be added, removed, or have their role changed in bulk",
            "Role assignment failed during migration or batch operation",
            "Deprovision or remove access for users who have left"
          ],
          "exclusion_criteria": [
            "User is asking a how-to question about the role assignment UI",
            "Issue is that the assigned role is not being enforced (use permission_not_enforced instead)"
          ],
          "boundary_examples": [
            "T-0138: Several people have left and accounts still active, please deprovision them → role_assignment_or_removal; remove access for leavers",
            "T-0201: After migration everyone in finance team given wrong role, needs corrected → role_assignment_or_removal; bulk role fix needed"
          ]
        }
      ]
    },
    {
      "name": "billing_and_subscription",
      "description": "Issues related to invoicing, charges, payment processing, subscription changes, and billing discrepancies",
      "leaves": [
        {
          "name": "billing_dispute_or_refund",
          "definition": "Customer reports being charged incorrectly, requests refund or credit, or disputes charge accuracy including duplicate charges, unauthorized charges, or charges not matching agreements",
          "inclusion_criteria": [
            "Customer reports being charged twice in same cycle or after cancellation",
            "Charge amount does not match contract or quote provided",
            "Customer requests full or partial refund due to billing error or service unavailability",
            "Customer disputes the accuracy of invoice line items"
          ],
          "exclusion_criteria": [
            "Customer is asking how seat counts are calculated (use subscription_plan_question)",
            "Customer is requesting a planned subscription change with new pricing"
          ],
          "boundary_examples": [
            "T-0010: Duplicate payment went through on our card, please refund → billing_dispute_or_refund; unauthorized duplicate charge needs investigation and refund",
            "T-0055: Cancelled before renewal but were charged anyway, requesting full refund → billing_dispute_or_refund; charge after cancellation warrants refund",
            "T-0262: Service was unavailable for significant window, requesting partial refund for that period → billing_dispute_or_refund; outage justifies credit"
          ]
        },
        {
          "name": "subscription_change_request",
          "definition": "Customer wants to modify their subscription plan, seat count, or billing frequency (upgrade, downgrade, or switch to different plan/tier)",
          "inclusion_criteria": [
            "Customer requests to add or remove seats from their subscription",
            "Customer wants to change billing frequency (monthly to annual or vice versa)",
            "Customer requests to upgrade or downgrade to a different plan tier"
          ],
          "exclusion_criteria": [
            "Customer is asking how their current plan handles seats (use subscription_plan_question)",
            "Customer is disputing a charge or requesting a refund"
          ],
          "boundary_examples": [
            "T-0126: Please add 12 more seats to our subscription → subscription_change_request; adding seats is a subscription modification",
            "T-0027: Need to reduce seat count by 18 before renewal → subscription_change_request; reducing seats requires subscription change"
          ]
        },
        {
          "name": "subscription_plan_question",
          "definition": "Customer asks clarifying questions about how their current plan works, what is included, or how charges are calculated, without requesting a change",
          "inclusion_criteria": [
            "Customer asks how seat counts are calculated on their plan",
            "Customer asks what features are included in a plan tier",
            "Customer asks how usage is billed or what the difference is between plans"
          ],
          "exclusion_criteria": [
            "Customer is requesting to change to a different plan (use subscription_change_request)",
            "Customer is reporting a billing error or discrepancy"
          ],
          "boundary_examples": [
            "T-0142: Could you explain how seats are counted on the Scale plan → subscription_plan_question; asking for clarification",
            "T-0144: Evaluating downgrade from Enterprise, what functionality would data team lose → subscription_plan_question; comparison question, not a change request"
          ]
        }
      ]
    },
    {
      "name": "product_and_feature_issues",
      "description": "Issues related to bugs, performance problems, broken functionality, and integrations in the platform",
      "leaves": [
        {
          "name": "performance_or_rate_limit_issue",
          "definition": "Platform or feature is unusually slow, hangs, times out, or user is being rate limited on API endpoints with limits lower than documented or expected",
          "inclusion_criteria": [
            "Query, report, or operation that was previously fast now hangs or is very slow",
            "User receives rate limit error on API endpoint with limit hit before documented threshold",
            "Timeouts or RATE_429 errors are returned",
            "Loading times have increased substantially or rate limiting is blocking expected operations"
          ],
          "exclusion_criteria": [
            "User is asking how to use a feature or configure settings",
            "User is asking about API rate limit policy without reporting hitting a limit"
          ],
          "boundary_examples": [
            "T-0222: Audit log takes over 15 minutes to load, was fast until this morning → performance_or_rate_limit_issue; loading time increased substantially",
            "T-0123: Rate limited on bulk endpoint after 12 requests, documented limit is much higher → performance_or_rate_limit_issue; hitting limit below documented threshold",
            "T-0295: Client gets RATE_429 from records endpoint after latest release → performance_or_rate_limit_issue; rate limit error from endpoint"
          ]
        },
        {
          "name": "data_sync_or_integration_broken",
          "definition": "Integrations with external systems are not working, data is not syncing, or connectors are failing",
          "inclusion_criteria": [
            "Records or data from external system not being reflected after sync",
            "Integration with third-party system broken or returning errors",
            "Bulk upload of data is failing partially or completely"
          ],
          "exclusion_criteria": [
            "User is asking how to set up an integration (use documentation_or_setup_question)",
            "User is requesting a new connector be built (use feature_request)"
          ],
          "boundary_examples": [
            "T-0091: Records updated in Workday not reflected on our side, last successful sync before migration → data_sync_or_integration_broken; sync failing",
            "T-0096: Bulk user upload fails partway with PERM_3310, some users created some not → data_sync_or_integration_broken; batch operation failing mid-way"
          ]
        },
        {
          "name": "documentation_or_setup_question",
          "definition": "User is asking how to set up, configure, or use a feature; documentation is conflicting or unclear",
          "inclusion_criteria": [
            "User asks How do I or What is the correct way to set up a feature",
            "Documentation pages contradict each other",
            "User needs guidance on configuration or feature usage"
          ],
          "exclusion_criteria": [
            "User is reporting that a feature is broken or not working",
            "User is asking about plan details or billing terms"
          ],
          "boundary_examples": [
            "T-0133: What is the correct way to connect BigQuery, found two different docs that disagree → documentation_or_setup_question; conflicting setup guidance",
            "T-0258: What is correct way to connect Salesforce, found two different docs that disagree → documentation_or_setup_question; conflicting documentation"
          ]
        },
        {
          "name": "feature_request",
          "definition": "Customer is requesting new functionality, enhancements, or capabilities that do not currently exist",
          "inclusion_criteria": [
            "Customer asks to add a new connector or integration type",
            "Customer requests new export or reporting capability",
            "Customer wants new product feature or enhancement"
          ],
          "exclusion_criteria": [
            "Customer is asking how to use an existing feature",
            "Customer is reporting that an existing feature is broken"
          ],
          "boundary_examples": [
            "T-0237: Request for exportable audit trail to close compliance reviews faster → feature_request; asking for new capability",
            "T-0288: Please consider adding native Snowflake connector → feature_request; requesting new integration"
          ]
        }
      ]
    },
    {
      "name": "account_management_and_operations",
      "description": "Issues related to workspace configuration, user provisioning, account operations, and administrative tasks",
      "leaves": [
        {
          "name": "user_provisioning_and_access",
          "definition": "Customer needs to add, provision, or configure access for new user accounts in bulk or for specific purposes, or modify access control settings and permission boundaries",
          "inclusion_criteria": [
            "Customer requests to add multiple new users to workspace",
            "Customer needs users provisioned before a deadline",
            "Customer needs to configure who can see specific dashboards or features",
            "Customer needs to restrict feature visibility for compliance or organizational reasons"
          ],
          "exclusion_criteria": [
            "Customer is asking how to invite users (use documentation_or_setup_question)",
            "User has been assigned a role but it is not being enforced (use permission_not_enforced)",
            "Customer is removing or deprovisioning users (separate from provisioning new access)"
          ],
          "boundary_examples": [
            "T-0217: Need 40 new users added to staging tenant before Monday → user_provisioning_and_access; bulk add request with deadline",
            "T-0100: Need to restrict Insights dashboard so only Read Only can view it → user_provisioning_and_access; setting up feature visibility controls",
            "T-0030: How do I invite someone as Workspace Admin without billing access → not user_provisioning_and_access; asking how-to, not requesting provisioning"
          ]
        }
      ]
    }
  ],
  "rationale": "Taxonomy reduced from 5 domains and 17 leaves to 4 domains and 12 leaves by consolidating overlapping categories. Key merges: (1) Merged 'refund_request' and 'duplicate_or_erroneous_charge' into 'billing_dispute_or_refund' because both require investigation and often result in refunds or credits—the boundary examples now cover both scenarios (duplicate charges, post-cancellation charges, outage-based refunds, and line item disputes). (2) Merged 'performance_degradation' and 'rate_limit_exceeded' into 'performance_or_rate_limit_issue' because both manifest as blocking/slow operations and share identical remediation paths (infrastructure investigation, quota adjustment, etc.)—boundary examples now distinguish timeout/hang scenarios from explicit rate limit errors. (3) Merged 'access_control_configuration' and 'user_provisioning_request' into 'user_provisioning_and_access' because they represent the same operational workflow (configuring who has access and to what)—boundary examples cover adding users and restricting feature visibility. (4) Moved 'session_or_account_unlock' criteria into 'login_failure' since both are authentication blocking issues requiring the same resolution path; boundary examples now clarify that account lockouts (after failed attempts, requiring admin reset) fall under login_failure. This consolidation reduces cognitive load for annotators while maintaining coverage of all ticket types in the corpus."
}
```
