# Revelio matching redesign and execution contract — 2026-11-08

Scope: prepare dependencies and Part 2.2 now; researcher executes and inspects.
Part 2.3 and final exports are separate, later checkpoints. No scan or production
export is authorized for agent execution. Work stays in the requested notebook
workspace; no commits, worktree migration, external services, or source edits.

## Audit and design

Notebook 01 establishes 652 string GVKEYs via historical CRSP–CCM P/C links.
The saved crosswalk has 565 pairs, 1,638 method triples, and zero accepted RCIDs.
The previous local supplement has 73 pairs for 43 additional firms, bringing
candidate coverage to 608; its old quality audit applies only to those 73 pairs.
Saved full-reference outputs report 651 firms, 780 pairs, 115 multi-RCID firms,
2,001 evidence records, 780 distinct matching reference rows, and 33,961,589
scanned rows. Method firm counts are 642 GVKEY, 610 CIK, 639 full CUSIP.
These are saved observations, not results of executing the redesigned code.

The full scan depends on text/CIK/CUSIP helpers and historical comparison sets
defined later. Move identical identifier helpers earlier; remove historical
comparison sets from discovery prerequisites and reconcile them in Part 2.2.
Keep scan function and lookup rules unchanged. Guard both expensive scan calls.

Recommended approach: retain evidence from the live kernel, validate it, optionally
checkpoint it explicitly, and consolidate in memory. Replaying the old ticker
search would repeat work and reading the full reference would be expensive.
A lost kernel with no checkpoint cannot be recovered from ten displayed rows;
stop and report the missing objects instead. Preserve the complete old notebook
in a dated local archive for historical methodology and outputs.

## Exact interfaces

Inputs: `crosswalk_v1`, `compustat_evidence`, `revelio_local`, `saved_candidates`,
`full_id_evidence`, `full_id_source_evidence`, `full_id_scan_summary`,
`local_candidate_evidence`, `additional_local_candidates`. Full-reference source
path is `data/raw/revelio/eudkcvtewjnwtygx.csv.gz`. Local input paths remain those
in Part 1. Earlier evidence is imported, never generated again in Part 2.2.

Outputs in memory:

- `consolidated_evidence`: namespaced evidence ID, GVKEY, RCID, method, origin,
  rule, comparison key, original source-row references and nested raw evidence.
  Full-scan units stay source-row/target/method, with linked Compustat rows.
- `consolidated_candidates`: one GVKEY–RCID pair, names and raw identifiers,
  method/support flags, source and evidence references, historical dates,
  observed hierarchy, multiplicity and identifier-disagreement diagnostics.
  Old shape-only CUSIP support is distinguished from validated full equality.
- `consolidated_firm_index`: exactly 652 original GVKEYs, names when available,
  all candidate RCIDs/counts, no leading choice, null acceptance, unreviewed state.
- `candidate_reconciliation`, `candidate_membership`, `consolidation_method_summary`,
  `consolidation_checks`, `consolidated_unmatched`: review and preservation tables.

Expected arithmetic, subject to execution: old union 638 pairs, intersection with
full reference 564, full-only 216, old-only 74, consolidated 854. Do not force the
output to 780. Remaining GVKEY inferred from saved outputs: 179700; no Compustat
name available. Historical CRSP ticker DPS is a locator, not a name/identity proof.

## Review and deliverables planned after verification

Part 2.3 will assess exact GVKEY, positive digits-only normalized CIK, validated
full CUSIP, and 8/9 format equivalence separately. CUSIP variation alone is not
an entity contradiction. Corroboration across identifier types does not imply
independent source verification. Names, tickers, and hierarchy cannot accept or
merge identities. Review classification will retain orthogonal flags for conflicts,
multi-RCID firms, RCIDs shared across targets, missingness, and hierarchy scope.
Proposed categories: conflicting identifier evidence; ambiguous multiple RCIDs;
strongly corroborated candidate; plausible requiring review; insufficient evidence;
no candidate (firm level only). Exact precedence and distributions await Part 2.2.
No old 73-pair classification will be propagated.

Final A schema: gvkey, research_company_name, candidate_count, candidate_rcids,
leading_candidate_rcid, leading_revelio_company_name, supporting_match_methods,
strongest_evidence, has_identifier_conflict, multiple_rcid_flag,
match_review_category, recommended_next_action, review_notes, accepted_rcid,
human_review_status. Missing names remain null; multiple historical names remain
available. Leading choice is optional, provisional, and null for unresolved ties.
B adds classifications and explanations to the consolidated pair schema. C reports
source scope, separate firm/pair/evidence/row units, methods and quality categories,
remaining unmatched target, snapshot/historical limits, and next review steps.

Versioned derived directories belong under `data/clean/revelio_matching/`.
The checkpoint is a typed JSON bundle plus checksum manifest, for nested raw
evidence without pickle; future review tables use CSV and Parquet, with supporting
evidence and field definitions. Excel engines are not installed; no new dependency
is needed. Publication must use a new directory, full read-back validation, and
a completion manifest. Final deliverable exports are not implemented this iteration.

## Implementation and validation plan

1. Add focused synthetic tests for identifier semantics, pair/evidence units,
   preservation of old-only alternatives, missing names, and checkpoint integrity.
2. Add a local helper for lossless evidence checkpoints and consolidation, with
   no import-time reads/writes. Checkpoint writes require an explicit function call.
3. Archive notebook verbatim; preserve Part 1 code/outputs and discovery function;
   move helpers, guard scans, replace obsolete narrative and Parts 2.2/2.3 flow.
4. Compile every cell, compare protected code and outputs to archive, run synthetic
   tests only. Leave new cells unexecuted. Researcher runs checkpoint cells next.

Risks: kernel loss; changed source files; lost identifier padding; weaker old CUSIP
rules; rows erroneously deduplicated at firm level; temporal claims from snapshots;
hashes recorded only now (not at the historical scan). A current source size/mtime
check is not retrospective proof of scan-time file identity. State this limitation.

## Execution handoff

The edited notebook has 39 cells. Cell numbers below are one-based physical
positions, not Jupyter execution counts:

1. Keep the completed Jupyter kernel alive. Reload the changed notebook from disk
   without restarting its kernel or saving an old editor buffer over the edit.
2. Run cell 15, `2.1.0` / ID `p21-workflow-setup`. Leave checkpoint load path None.
3. Run cell 30, `2.2.1` / ID `p22-validate-inputs`. Stop if anything is missing.
4. Cell 32, `2.2.2` / ID `p22-save-checkpoint`, is optional. Set the write flag True
   only to preserve retained evidence now. Keep the printed version directory.
5. Run cells 34, 36, 38: `2.2.3`, `2.2.4`, `2.2.5`, then stop.

Send the input validation table, pair reconciliation, method summary, printed
coverage/multiplicity counts, three-way source membership counts, unmatched row,
final check table and PASS message. Send a full traceback on failure. Expected
854 is a reconciliation assertion based on prior saved outputs, not a new result.

After a checkpoint exists, the shortest fresh-kernel path is cell 2 (Part 0), cell
15 with the explicit checkpoint path, then cells 30, 34, 36, 38. Without the original
kernel or a complete checkpoint, stop: the existing notebook output contains only
previews of full evidence and is insufficient to recreate it. Recovery/scanning
would require a separately discussed execution path; none runs automatically.

Implementation verification: all code cells compile; structural comparison confirms
Part 1 cells/outputs, both scan functions, shared identifier helpers, and lookup
construction remain identical to the archived notebook. Synthetic tests check
checkpoint preservation/tampering, candidate/evidence counts, old invalid-CUSIP
retention, nonpromotion, missing state, and CUSIP-only disagreement. No research
cell or real-data consolidation has been executed by the agent.
