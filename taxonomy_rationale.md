# Taxonomy rationale

This is the agent's own reasoning, kept as a build artifact. The wording
is the agent's: nothing is added, removed, or reordered. The only change
is that em and en dashes are normalized to commas, because this repo
forbids them in prose. The point of keeping this file is to show what the
agent actually argued, including anything a reviewer would push back on.

## The agent's reasoning

I organized the corpus around four domains that reflect distinct operational ownership in a B2B SaaS support org: identity/authentication, roles-permissions-provisioning, billing/subscription, and platform reliability/product usage. Within identity, I split by the specific broken flow (SSO redirect, lockout, MFA, password reset) rather than lumping all 'can't log in' tickets together, because each recurs multiple times verbatim in the sample and each has a distinct remedy path for support; the key boundary I enforced is symptom-first classification (redirect loop vs failed unlock vs missing code vs missing email) rather than root-cause, since customers rarely know the root cause. In the roles domain I deliberately separated three things that could easily blur into one 'permissions' bucket: role_permission_mismatch (a defect claim that current behavior violates documented role behavior), user_provisioning_failure (automated pipelines silently not creating/syncing accounts), and access_configuration_request (forward-looking how-to/setup asks). I rejected merging role_permission_mismatch and access_configuration_request because the corpus has a clean split between 'this is broken' (T-0085, T-0121, T-0201, T-0308, T-0313) and 'how do I set this up' (T-0030, T-0075, T-0100) tickets that annotators would otherwise conflate. For billing, I rejected a single catch-all 'billing_issue' leaf because the sample cleanly separates tickets that explicitly demand a refund (refund_request) from tickets that dispute an invoice's accuracy without demanding a refund (billing_discrepancy) from tickets managing plan tier/seats prospectively (plan_tier_management); the refund vs discrepancy line is the most likely drift point, so I anchored it to the literal presence of a refund/credit ask, using T-0273's explicit rejection of a credit note as the calibrating example. I merged what could have been two leaves (plan change requests and seat-count informational questions) into plan_tier_management because both are non-disputed, forward-looking subscription administration and splitting them would have pushed the leaf count over budget without adding annotator value. In the fourth domain, I merged webhook failures, connector sync failures, and API/rate-limit errors into a single integration_api_failure leaf rather than three separate leaves, since each appears only once or twice and they share a common annotator-facing question ('is an external system/endpoint the point of failure?'); I kept performance_degradation separate because it is UI/latency-facing rather than integration-contract-facing, and used the reused TIMEOUT_5041 error code across T-0217/T-0222/T-0274 as the deliberate stress test showing that leaf assignment must follow the failing surface, not the error code. Finally, I split product_howto_question from feature_request along the explicit existing-vs-not-existing framing, using the near-identical 'scheduled reports' topic in both T-0092 (assumed existing capability) and T-0288 (explicitly requested as new/roadmap) as the calibrating boundary pair, since this is the most likely confusion in the whole corpus given how often 'scheduling reports' recurs. Total: 4 domains, 14 leaves, each domain has 3-4 leaves, satisfying the size constraints while keeping every leaf tied to a real, recurring pattern in the sampled tickets.

## Structure

### identity_authentication

Tickets about users being unable to authenticate or complete login-adjacent security flows (SSO, lockout, MFA, password reset). Distinguished from the permissions domain, which covers users who can log in but whose role-based access is wrong.

- **sso_login_loop**: Reports that the SSO integration redirects users in a loop, preventing login, with no changes made to the customer's identity configuration.
- **account_lockout**: Users locked out after failed login attempts where the unlock flow itself fails, typically surfacing a specific error code, and the customer asks for a session reset.
- **mfa_delivery_failure**: The multi-factor authentication code never arrives to the user despite the identity provider reporting a successful assertion.
- **password_reset_failure**: Password reset emails are not being delivered, or the reset link has expired, blocking any user on the domain from regaining access via the reset flow.

### roles_permissions_provisioning

Tickets about who can access what once a user is already authenticated: role behavior bugs, automated account provisioning pipelines, and manual configuration requests for roles/access.

- **role_permission_mismatch**: A bug report where a user's actual access diverges from what their assigned role should allow, per the documented role description, or a role that silently reverts/fails to update.
- **user_provisioning_failure**: Automated account creation or sync pipelines (SCIM push, bulk import) fail to create or update accounts even though the source system reports success.
- **access_configuration_request**: How-to or setup requests about configuring roles and access boundaries going forward, rather than a report that existing role behavior is broken.

### billing_subscription

Tickets about money and plan structure: disputing charges, requesting refunds, and managing subscription tier or seat count, separate from product usage questions.

- **refund_request**: Tickets that explicitly ask for a refund or credit as the remedy for a billing problem.
- **billing_discrepancy**: Tickets disputing the accuracy of an invoice, charge, or usage report against a contract, quote, or prior invoices, seeking explanation or reconciliation rather than a refund.
- **plan_tier_management**: Requests to change subscription tier or seat count, and informational questions about how seats or plan tiers work, independent of any disputed charge.

### platform_reliability_product_usage

Tickets about the technical health of the platform (performance, integrations) and non-billing product interaction: how-to questions and feature requests.

- **performance_degradation**: Reports of slowness, hanging, or timeouts in using core product surfaces (console, queries, report rendering), where the complaint is speed/latency rather than an integration or API contract failure.
- **integration_api_failure**: Failures in external-facing technical integrations: webhook delivery, connector data sync, API request validation, or rate limiting against documented limits.
- **product_howto_question**: Informational or setup questions about using an existing, documented product feature, with no defect reported and no new capability requested.
- **feature_request**: Explicit proposals for new functionality not currently supported, often self-labeled as a request or asking about roadmap status.

## Coverage on the induction sample

- sample size: 120
- unmappable: 4 (3.3%, ceiling 5%)
- structural repair attempts needed: 0

| Leaf | Tickets in sample |
|---|---|
| billing_discrepancy | 17 |
| plan_tier_management | 13 |
| integration_api_failure | 12 |
| access_configuration_request | 11 |
| performance_degradation | 10 |
| product_howto_question | 10 |
| feature_request | 10 |
| role_permission_mismatch | 9 |
| refund_request | 5 |
| password_reset_failure | 5 |
| user_provisioning_failure | 5 |
| account_lockout | 4 |
| unmappable | 4 |
| sso_login_loop | 3 |
| mfa_delivery_failure | 2 |
