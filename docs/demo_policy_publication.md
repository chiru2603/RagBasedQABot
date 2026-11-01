# Synthetic policy publication and ingestion guide

Fictional test guide, not Superhuman policy. Version: 1.0. Policy updated: 2026-10-30.

## Approved source folder
The configured Google Drive folder contains approved, broadly shared governance guidance for the test corpus. Keep drafts, personal records, credentials, and Restricted source data outside this folder. A policy explaining Restricted data may be broadly shared without containing the Restricted records themselves.

## Document identity and dates
The Drive file ID identifies a document across renames. The source URL links readers to the original file. Drive modification time and the policy's stated update date describe different events; the index build time is neither of those dates.

## Expected daily synchronization
The planned daily pipeline discovers new documents, updates changed content, skips unchanged content, and removes indexed documents confirmed to have left the configured source scope. These are target behaviors for testing, not a claim that scheduling or the Drive connector is already implemented.

## Source failures and conflicting guidance
A failed or incomplete folder listing must not be treated as proof that documents were removed. Preserve the previous index when synchronization fails. When retrieved policies conflict, explain the conflict or abstain; do not invent a resolution from the most recent indexing timestamp.

## Test change procedure
For an update test, edit the temporary access duration in the separate access permissions policy and increment its version. For a removal test, use a disposable copy rather than deleting the original policy. After each sync, verify both stored content and cited answers.
