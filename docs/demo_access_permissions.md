# Synthetic access permissions policy

Fictional test policy, not Superhuman policy. Version: 1.0. Policy updated: 2026-10-30.

## Requesting access
Submit a request through the fictional Data Access Portal. Include the dataset identifier, business purpose, requested role, and required duration. Never include passwords, API keys, or personal records in the request or chatbot messages.

## Roles and approval
Reader permits querying approved data. Editor permits modifying data within the approved scope. Administrator permits managing permissions and requires separate platform administrator approval. Request the least privileged role that meets the business need.

Internal data is limited to authenticated employees with the appropriate group membership. Restricted data requires explicit approval from the designated data owner before any grant. Group membership alone does not authorize Restricted access. The current owner and current grants must be checked in the live catalog and access system; this document does not identify them.

## Temporary access
In this fictional workflow, temporary Restricted access expires after 14 calendar days unless a shorter duration is approved. Extensions require a new owner approval before expiry. The access system enforces expiry; the chatbot cannot grant or extend access.

## Review and revocation
Owners review Restricted access every 90 days. Revoke access when employment ends, responsibilities change, or the approved purpose ends. Emergency access still requires the designated owner's approval and an audit record.
