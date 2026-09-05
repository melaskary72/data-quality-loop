"""Content templates for the corpus generator.

Kept separate from the generation logic so the logic stays readable and the
content stays auditable. Nothing here performs a model call. Every string is
fixed, and variety comes from seeded combination in `dql/generate.py`.

The taxonomy below is the *hidden ground-truth taxonomy*. It is what the
generator labels against. It is not the operating vocabulary: that one is
induced from unlabeled tickets in `dql/induce.py`, and the two are bridged by a
hand written alignment in `eval/taxonomy_alignment.yaml`.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# hidden ground-truth taxonomy: 4 domains, 12 leaves
# --------------------------------------------------------------------------

GROUND_TRUTH_TAXONOMY: dict[str, list[str]] = {
    "billing": ["invoice_dispute", "refund_request", "plan_change"],
    "technical": [
        "login_auth_failure",
        "data_sync_error",
        "performance_degradation",
        "api_integration_error",
    ],
    "account": ["user_provisioning", "permissions_rbac", "data_export_request"],
    "product": ["feature_request", "how_to_question"],
}

LEAVES: list[str] = [leaf for leaves in GROUND_TRUTH_TAXONOMY.values() for leaf in leaves]

# Deliberately uneven. A uniform support corpus is the least realistic thing
# this generator could produce, and it would hide the frequent-class bias that
# the quality framework exists to surface.
CLASS_WEIGHTS: dict[str, float] = {
    "how_to_question": 0.14,
    "login_auth_failure": 0.11,
    "data_sync_error": 0.10,
    "api_integration_error": 0.09,
    "invoice_dispute": 0.09,
    "permissions_rbac": 0.08,
    "performance_degradation": 0.08,
    "user_provisioning": 0.07,
    "feature_request": 0.07,
    "refund_request": 0.06,
    "plan_change": 0.06,
    "data_export_request": 0.05,
}

# Plausible confusions only. A random wrong label is trivially catchable and
# would inflate seeded-error recall into a meaningless number.
CONFUSION_MAP: dict[str, list[str]] = {
    "invoice_dispute": ["refund_request", "plan_change"],
    "refund_request": ["invoice_dispute", "plan_change"],
    "plan_change": ["invoice_dispute", "feature_request"],
    "login_auth_failure": ["permissions_rbac", "user_provisioning"],
    "data_sync_error": ["api_integration_error", "performance_degradation"],
    "performance_degradation": ["data_sync_error", "api_integration_error"],
    "api_integration_error": ["data_sync_error", "how_to_question"],
    "user_provisioning": ["permissions_rbac", "login_auth_failure"],
    "permissions_rbac": ["user_provisioning", "login_auth_failure"],
    "data_export_request": ["how_to_question", "api_integration_error"],
    "feature_request": ["how_to_question", "plan_change"],
    "how_to_question": ["feature_request", "api_integration_error"],
}

# --------------------------------------------------------------------------
# slot fillers
# --------------------------------------------------------------------------

FILLERS: dict[str, list[str]] = {
    "surface": [
        "the Reports workspace", "the Pipelines view", "the Admin console",
        "the Insights dashboard", "the Connections page", "the Billing portal",
        "the Audit log", "the Workflow builder",
    ],
    "integration": [
        "Salesforce", "NetSuite", "Snowflake", "HubSpot", "Workday",
        "SAP Concur", "Zendesk", "BigQuery",
    ],
    "env": ["production", "our sandbox", "the staging tenant", "the EU workspace"],
    "region": ["eu-west-1", "us-east-2", "ap-south-1", "eu-central-1"],
    "plan": ["Growth", "Scale", "Enterprise", "Team"],
    "err": [
        "SYNC_4012", "AUTH_0091", "RATE_429", "MAP_5503",
        "TIMEOUT_5041", "PERM_3310", "EXP_2207",
    ],
    "n": ["3", "5", "12", "18", "40", "127", "300"],
    "minutes": ["4", "9", "15", "22", "45"],
    "role": ["Analyst", "Approver", "Workspace Admin", "Read Only", "Finance Lead"],
    "team": [
        "the finance team", "our data team", "the ops group",
        "the revenue desk", "the onboarding squad",
    ],
    "when": [
        "since Tuesday", "since the last release", "for the past three days",
        "since we migrated", "since this morning",
    ],
}

# --------------------------------------------------------------------------
# per-leaf templates
# --------------------------------------------------------------------------

TEMPLATES: dict[str, dict[str, list[str]]] = {
    "invoice_dispute": {
        "subjects": [
            "Invoice {n} looks wrong",
            "Charged twice on this month's invoice",
            "Line item on our invoice does not match the contract",
            "Overbilled for seats we do not have",
            "Question about an unexpected charge",
        ],
        "bodies": [
            "Our latest invoice shows {n} seats but we only provisioned a fraction of that. Can someone reconcile this against the signed order form.",
            "We were billed twice in the same cycle for the {plan} plan. The second charge appeared {when} and we have not authorised it.",
            "There is a line item on the invoice we cannot account for. The amount does not match what {team} agreed during renewal.",
            "The invoice total is higher than the quote we approved. Please break down the difference before we release payment.",
            "We are disputing this month's charge. The usage report does not support the amount billed for {surface}.",
        ],
    },
    "refund_request": {
        "subjects": [
            "Refund for the unused portion",
            "Refund for duplicate payment",
            "Still owed the difference after downgrade",
            "Charged after cancelling",
            "Partial refund for the outage window",
        ],
        "bodies": [
            "We paid for the full period but stopped using the platform {when}. We would like a refund for the unused portion.",
            "A duplicate payment went through on our card. Please refund the second one and confirm when it is processed.",
            "Following our downgrade we are still owed the difference for the remaining term on the {plan} plan. Please issue a refund.",
            "We cancelled before the renewal date but were charged anyway. Requesting a full refund back to the original payment method.",
            "The service was unavailable for a significant window {when}. We are requesting a partial refund for that period.",
        ],
    },
    "plan_change": {
        "subjects": [
            "Upgrade us to {plan}",
            "Downgrade request before renewal",
            "Adding seats to our current plan",
            "Switching billing to annual",
            "Change our subscription tier",
        ],
        "bodies": [
            "We would like to move to the {plan} plan effective next cycle. Please confirm the prorated amount before you apply it.",
            "Our headcount dropped and we need to reduce our seat count by {n}. What is the process before the renewal date.",
            "Please add {n} more seats to our subscription. {team} is onboarding new people next week.",
            "We want to switch from monthly to annual billing. Can you confirm the discount and the new renewal date.",
            "We are evaluating a downgrade from {plan}. What functionality would {team} lose if we moved down a tier.",
        ],
    },
    "login_auth_failure": {
        "subjects": [
            "Cannot log in since this morning",
            "SSO login loop",
            "MFA code never arrives",
            "Password reset email not received",
            "Locked out of {env}",
        ],
        "bodies": [
            "None of us can sign in to {env}. The page returns error {err} and then redirects back to the login screen.",
            "Our SSO integration puts users in a redirect loop. It has been happening {when} and affects {team}.",
            "The multi factor code never arrives. We have waited {minutes} minutes and tried three separate accounts.",
            "Password reset emails are not arriving for any user on our domain. The reset link we already had has now expired.",
            "I am locked out after several failed attempts and the unlock flow returns {err}. Please reset the session.",
        ],
    },
    "data_sync_error": {
        "subjects": [
            "{integration} sync failing with {err}",
            "Partial sync leaving stale data",
            "Sync job stuck",
            "Records stopped syncing {when}",
            "Mismatch between {integration} and {surface}",
        ],
        "bodies": [
            "The {integration} connector has been failing with {err} {when}. About {n} records are now stale in {surface}.",
            "Our nightly sync completes but only writes a fraction of the rows. {team} is seeing yesterday's numbers in {surface}.",
            "The sync job has been stuck in a running state for {minutes} minutes and will not cancel. Nothing new has landed.",
            "Records updated in {integration} are not reflected on our side. The last successful sync was {when}.",
            "We are seeing a mismatch between {integration} and {surface} after the connector retried. Field mapping looks off.",
        ],
    },
    "performance_degradation": {
        "subjects": [
            "{surface} extremely slow",
            "Timeouts loading reports",
            "Slow response times in {region}",
            "Platform sluggish {when}",
            "Dashboard takes minutes to render",
        ],
        "bodies": [
            "{surface} takes over {minutes} minutes to load. It was fast until {when} and nothing changed on our side.",
            "We are getting timeouts with {err} whenever a report spans more than a quarter of data.",
            "Queries that used to return instantly now hang. Users in {region} are worst affected.",
            "The platform has been sluggish {when}. {team} is unable to work at a normal pace and we have no error to point at.",
            "Page loads in {surface} have degraded badly. Browser console shows repeated retries before anything renders.",
        ],
    },
    "api_integration_error": {
        "subjects": [
            "API returning {err}",
            "Webhook deliveries failing",
            "Rate limited on the bulk endpoint",
            "Integration broke after your release",
            "{err} on every POST",
        ],
        "bodies": [
            "Our integration started returning {err} on every call {when}. Nothing changed in our client code.",
            "Webhook deliveries to our endpoint are failing. We see retries but no successful delivery {when}.",
            "We are being rate limited on the bulk endpoint after only {n} requests. The documented limit is much higher.",
            "Since your latest release our client gets {err} from the records endpoint. The same payload worked before.",
            "Every POST to the ingestion endpoint fails validation with {err}, though the payload matches your schema.",
        ],
    },
    "user_provisioning": {
        "subjects": [
            "Add {n} new users",
            "New starter cannot be invited",
            "Deprovision leavers",
            "Bulk user upload failing",
            "SCIM provisioning not creating accounts",
        ],
        "bodies": [
            "We need {n} new users added to {env} before Monday. They should sit in the same workspace as {team}.",
            "Invitations to a new starter are not going out. Their account never appears in the {surface}.",
            "Several people have left and their accounts are still active. Please deprovision them and confirm.",
            "Our bulk user upload fails partway with {err}. Some users are created and some are not, which leaves us inconsistent.",
            "SCIM provisioning is enabled but no accounts are being created. The directory push reports success on our side.",
        ],
    },
    "permissions_rbac": {
        "subjects": [
            "{role} cannot see {surface}",
            "Wrong role assigned after migration",
            "Restrict access to financial data",
            "Role change not taking effect",
            "User can see data they should not",
        ],
        "bodies": [
            "Users with the {role} role cannot open {surface}, though the role description says they should. They get {err}.",
            "After the migration everyone in {team} was given the wrong role. We need this corrected before month end.",
            "We need to restrict {surface} so only {role} can view it. Right now the whole workspace can see everything.",
            "Changing a user's role does not take effect. They keep the old permissions until they are removed and re-added.",
            "Someone with the {role} role is seeing data they should not have access to. This is a compliance concern for {team}.",
        ],
    },
    "data_export_request": {
        "subjects": [
            "Export all records for audit",
            "Need a full data dump",
            "Scheduled export to {integration}",
            "CSV export truncated",
            "Export our history before renewal",
        ],
        "bodies": [
            "We need a complete export of our records for an audit. CSV or Parquet both work for {team}.",
            "Please provide a full data dump of our workspace including the audit log for the last twelve months.",
            "We would like a scheduled export into {integration} rather than a manual download every week.",
            "Our CSV export stops at {n} rows and the rest is missing. This makes the file unusable for reconciliation.",
            "Before we decide on renewal we want a copy of everything we have put into the platform.",
        ],
    },
    "feature_request": {
        "subjects": [
            "Please add bulk edit to {surface}",
            "Feature request: saved filters",
            "Add {integration} as a native connector",
            "Would like an audit trail export",
            "Scheduled report delivery",
        ],
        "bodies": [
            "It would help a lot if {surface} supported bulk editing. Right now {team} does the same change {n} times by hand.",
            "We would like to save filter combinations and share them across the workspace. Rebuilding them daily wastes time.",
            "Please consider adding a native {integration} connector. We currently glue it together with scripts.",
            "A request rather than a bug: an exportable audit trail would let us close our compliance reviews much faster.",
            "Any chance of adding scheduled delivery of reports by email. It is the one thing {team} keeps asking for.",
        ],
    },
    "how_to_question": {
        "subjects": [
            "How do I set up {integration}",
            "Where do I find the audit log",
            "How to invite someone as {role}",
            "Question about scheduling reports",
            "How does the {plan} plan handle seats",
        ],
        "bodies": [
            "What is the correct way to connect {integration}. I found two different pages in the docs and they disagree.",
            "Where do I find the audit log for {surface}. I have Workspace Admin but cannot see the menu entry.",
            "How do I invite someone as {role} without giving them access to billing.",
            "Is there a way to schedule a report to run weekly and send the output to {team}.",
            "Could you explain how seats are counted on the {plan} plan. We are trying to plan next quarter's budget.",
        ],
    },
}

# --------------------------------------------------------------------------
# ambiguous blends: two defensible leaves each
# --------------------------------------------------------------------------

AMBIGUOUS_BLENDS: list[dict[str, str]] = [
    {
        "primary": "invoice_dispute", "alt": "refund_request",
        "subject": "Wrong amount charged, please correct it",
        "body": "This month's invoice is higher than agreed and the money has already left our account. We need the amount corrected and the difference returned.",
    },
    {
        "primary": "refund_request", "alt": "plan_change",
        "subject": "Downgraded but still charged the old rate",
        "body": "We moved down from the {plan} plan {when} but the old rate was charged again. Please put us on the right tier and return the excess.",
    },
    {
        "primary": "login_auth_failure", "alt": "permissions_rbac",
        "subject": "User can sign in but sees nothing",
        "body": "A member of {team} authenticates successfully but every page in {surface} is empty and the console shows {err}. We cannot tell whether this is the login integration or their role.",
    },
    {
        "primary": "permissions_rbac", "alt": "user_provisioning",
        "subject": "New starter has the wrong access",
        "body": "A new starter was created but landed with no role at all. They cannot open {surface}. It is unclear whether the account was provisioned incorrectly or the role assignment failed.",
    },
    {
        "primary": "data_sync_error", "alt": "api_integration_error",
        "subject": "{integration} data missing and API returning {err}",
        "body": "Records from {integration} stopped appearing {when} and our own API calls to fetch them return {err}. We cannot tell if the connector or the endpoint is at fault.",
    },
    {
        "primary": "performance_degradation", "alt": "data_sync_error",
        "subject": "Reports slow and showing stale numbers",
        "body": "{surface} takes {minutes} minutes to load and when it finally renders the figures are from yesterday. Either the sync is behind or the platform is struggling.",
    },
    {
        "primary": "how_to_question", "alt": "feature_request",
        "subject": "Is bulk edit possible, and if not can you add it",
        "body": "Is there a way to bulk edit in {surface} that I have missed. If there genuinely is not, please treat this as a request for it.",
    },
    {
        "primary": "how_to_question", "alt": "api_integration_error",
        "subject": "Is {err} expected on this endpoint",
        "body": "We are getting {err} from the bulk endpoint after {n} requests. Is that the documented limit and we are using it wrong, or is something broken on your side.",
    },
    {
        "primary": "data_export_request", "alt": "how_to_question",
        "subject": "Best way to get all our data out",
        "body": "We need a full copy of our workspace data. Is there a supported export path for that, or should we be asking your team to produce it for us.",
    },
    {
        "primary": "plan_change", "alt": "feature_request",
        "subject": "Which tier unlocks scheduled delivery",
        "body": "{team} needs scheduled report delivery. If that is a paid tier we will upgrade, and if it does not exist yet please log it as a request.",
    },
    {
        "primary": "user_provisioning", "alt": "login_auth_failure",
        "subject": "Invited users never manage to get in",
        "body": "We invited {n} people. Some never received the invitation and the ones who did cannot complete sign in, failing with {err}.",
    },
    {
        "primary": "api_integration_error", "alt": "performance_degradation",
        "subject": "API timing out under normal load",
        "body": "Calls that used to return quickly now time out with {err} at the same request volume as before. It reads like a limit, but it could equally be slowness on your side.",
    },
]

# --------------------------------------------------------------------------
# paraphrase transform, for near-duplicate pairs
# --------------------------------------------------------------------------

OPENERS: list[str] = [
    "Hi team,", "Hello,", "Good morning,", "Hi there,", "Hi support,",
]

CLOSERS: list[str] = [
    "Thanks in advance.", "Appreciate the help.", "Please advise.",
    "Let us know as soon as you can.", "Thank you.",
]

# Applied to the duplicate member only. Kept small on purpose: real paraphrases
# of the same incident keep most of their token set, which is exactly what
# makes token_set_ratio the right detector.
SYNONYMS: list[tuple[str, str]] = [
    ("cannot", "are unable to"),
    ("Please", "Could you please"),
    ("issue", "problem"),
    ("since", "starting from"),
    ("We are", "We're"),
    ("does not", "doesn't"),
    ("failing", "erroring"),
    ("need", "require"),
]

# --------------------------------------------------------------------------
# multilingual fragments: Modern Standard Arabic, support register
# --------------------------------------------------------------------------

ARABIC_FRAGMENTS: list[str] = [
    "لا يمكننا الوصول إلى النظام منذ صباح اليوم.",
    "حاولنا إعادة المحاولة عدة مرات دون جدوى.",
    "المشكلة تؤثر على الفريق بالكامل ونحتاج حلاً عاجلاً.",
    "هل يمكنكم توضيح الخطوات المطلوبة من جانبنا.",
    "أرجو المتابعة مع الفريق التقني في أقرب وقت.",
    "لم نتلق أي رد على التذكرة السابقة.",
    "نحتاج إلى تأكيد كتابي قبل نهاية الأسبوع.",
    "الرجاء إفادتنا بالمدة المتوقعة للإصلاح.",
]

# --------------------------------------------------------------------------
# synthetic PII, unmistakably fake by construction
# --------------------------------------------------------------------------

# 555 numbers are reserved for fiction. example.com and example.org are
# reserved by RFC 2606. The card prefixes below are the publicly published
# test numbers that every payment processor documents as non functional.
PII_EMAILS: list[str] = [
    "dana.reyes@example.com", "m.okafor@example.org", "finance.lead@example.com",
    "priya.nair@example.org", "j.almeida@example.com", "sam.hollis@example.org",
    "noor.haddad@example.com", "t.berg@example.org",
]

PII_PHONES: list[str] = [
    "(555) 019-4472", "555-0134", "+1 555 0177 202", "555.0142.88",
    "(555) 0188-311", "+1-555-0166", "555 0155 907",
]

PII_CARDS: list[str] = [
    "4111 1111 1111 1111", "4242424242424242", "5555 5555 5555 4444",
    "4000 0566 5566 5556", "5105 1051 0510 5100",
]

PII_CARRIERS: list[str] = [
    "You can reach me directly on {value}.",
    "My contact is {value} if that is easier.",
    "Copying my details here for reference: {value}.",
    "The card we were charged on ends with this number: {value}.",
    "Please confirm to {value} once this is resolved.",
    "For follow up use {value}.",
]

# --------------------------------------------------------------------------
# per-ticket detail sentences
# --------------------------------------------------------------------------

# Pairing subject and body by index made tickets coherent but collapsed
# within-leaf lexical diversity: with 12 leaves and 5 variants each, two
# unrelated tickets sharing a leaf and a variant differed only in filler values
# and scored as near duplicates. These sentences are drawn independently per
# ticket to restore divergence between unrelated tickets, while a planted
# paraphrase still carries its partner's detail sentences and stays similar.
DETAIL_SENTENCES: list[str] = [
    "This started after we rolled out the change to {team}.",
    "We have already tried clearing the cache and reconnecting.",
    "Our reference for this is ticket {n} raised last week.",
    "It is only affecting users in {region} as far as we can tell.",
    "The same behaviour shows up in {env} as well.",
    "Nothing in our own logs looks unusual around that time.",
    "We are on the {plan} plan if that is relevant.",
    "This is blocking a deadline at the end of the week.",
    "Screenshots are attached to the original thread.",
    "Our account manager suggested we raise this here.",
    "It worked correctly until roughly {minutes} minutes before we noticed.",
    "We have {n} people affected across two workspaces.",
    "Happy to jump on a call if that is quicker.",
    "We checked the status page and saw nothing reported.",
    "The workaround we are using is manual and does not scale.",
    "Please loop in whoever owns {surface}.",
]
