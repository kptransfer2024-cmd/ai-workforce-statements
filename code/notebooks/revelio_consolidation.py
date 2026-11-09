"""Notebook 01b Part 2.2. No import-time I/O, scan, acceptance, or export.

Checkpoint functions are called explicitly by the researcher. All source paths
are read-only; the full reference is stat'ed, never opened by this module.
"""
from datetime import datetime, timezone
from pathlib import Path
import ast
import hashlib
import inspect
import json
import math
import re

import numpy as np
import pandas as pd

RULE_VERSION = 'revelio-exact-discovery-2026-11-06-v1'
BUNDLE_VERSION = 'revelio-evidence-checkpoint-v1'
STATE_KEYS = (
    'crosswalk_v1', 'compustat_evidence', 'revelio_local', 'saved_candidates',
    'full_id_evidence', 'full_id_source_evidence', 'full_id_scan_summary',
    'local_candidate_evidence', 'additional_local_candidates',
)
RAW_FIELDS = ('rcid', 'company', 'gvkey', 'cik', 'cusip', 'ticker',
              'ultimate_parent_rcid', 'child_rcid')


def text_key(value):
    return "" if pd.isna(value) else str(value).strip()


def positive_digits(value):
    value = text_key(value)
    return (value.lstrip("0") or "") if re.fullmatch(r"[0-9]+", value) else ""


def cusip_audit_key(value):
    value = text_key(value).upper()
    if not value:
        return "", "missing"
    if not re.fullmatch(r"[0-9A-Z*@#]{8}([0-9])?", value):
        return "", "invalid_shape"
    if len(value) == 8:
        return value, "valid8_no_check_digit"
    char_values = {"*": 36, "@": 37, "#": 38}
    total = 0
    for pos, char in enumerate(value[:8]):
        number = int(char) if char.isdigit() else ord(char) - ord("A") + 10 if char.isalpha() else char_values[char]
        product = number * (1 if pos % 2 == 0 else 2)
        total += product // 10 + product % 10
    if int(value[-1]) != (10 - total % 10) % 10:
        return "", "invalid_check_digit"
    return value, "valid9_check_digit"


def _encode(value):
    """Explicit tags preserve nested tuples, nulls, padding, and frame dtypes."""
    if isinstance(value, pd.DataFrame):
        return {'type': 'frame', 'columns': list(value.columns),
                'dtypes': [str(t) for t in value.dtypes], 'index': _encode(list(value.index)),
                'index_name': value.index.name,
                'rows': _encode(list(value.itertuples(index=False, name=None)))}
    if value is pd.NA:
        return {'type': 'pd.NA'}
    if value is pd.NaT:
        return {'type': 'pd.NaT'}
    if isinstance(value, pd.Timestamp):
        return {'type': 'timestamp', 'value': value.isoformat()}
    if isinstance(value, np.generic):
        return _encode(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return {'type': 'float', 'value': repr(value)}
    if isinstance(value, (tuple, list, set, frozenset)):
        items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
        return {'type': type(value).__name__, 'items': [_encode(v) for v in items]}
    if isinstance(value, dict):
        return {'type': 'dict', 'items': [[_encode(k), _encode(v)] for k, v in value.items()]}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f'Unsupported checkpoint value: {type(value).__name__}')


def _decode(value):
    if not isinstance(value, dict):
        return value
    tag = value['type']
    if tag == 'frame':
        frame = pd.DataFrame(_decode(value['rows']), columns=value['columns'])
        for column, dtype in zip(value['columns'], value['dtypes']):
            frame[column] = frame[column].astype(dtype)
        frame.index = _decode(value['index'])
        frame.index.name = value['index_name']
        return frame
    if tag == 'dict':
        return {_decode(k): _decode(v) for k, v in value['items']}
    if tag in ('tuple', 'list', 'set', 'frozenset'):
        constructor = {'tuple': tuple, 'list': list, 'set': set, 'frozenset': frozenset}[tag]
        return constructor(_decode(v) for v in value['items'])
    if tag == 'pd.NA':
        return pd.NA
    if tag == 'pd.NaT':
        return pd.NaT
    if tag == 'timestamp':
        return pd.Timestamp(value['value'])
    if tag == 'float':
        return float(value['value'])
    raise ValueError(f'Unknown checkpoint type: {tag}')


def _bytes(value):
    return json.dumps(_encode(value), ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def state_digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def write_bundle(destination, state, provenance):
    """Exclusive new directory; manifest written last, after exact read-back."""
    destination = Path(destination)
    payload = _bytes(state)
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / 'evidence.json').open('xb') as handle:
        handle.write(payload)
    recovered = _decode(json.loads((destination / 'evidence.json').read_bytes()))
    if _bytes(recovered) != payload:
        raise ValueError('Checkpoint round-trip changed evidence; incomplete directory retained.')
    manifest = {'schema': BUNDLE_VERSION, 'created_utc': datetime.now(timezone.utc).isoformat(),
                'payload_sha256': hashlib.sha256(payload).hexdigest(), 'provenance': provenance,
                'status': 'complete', 'accepted_identities': 0}
    with (destination / 'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
    return destination


def read_bundle(destination, provenance):
    destination = Path(destination)
    manifest = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['schema'] != BUNDLE_VERSION or manifest['status'] != 'complete':
        raise ValueError('Unsupported or incomplete checkpoint.')
    if manifest['provenance'] != provenance:
        raise ValueError('Source fingerprints or discovery code changed; review before resuming.')
    payload = (destination / 'evidence.json').read_bytes()
    if hashlib.sha256(payload).hexdigest() != manifest['payload_sha256']:
        raise ValueError('Checkpoint checksum mismatch.')
    return _decode(json.loads(payload))


def source_fingerprints(project_root):
    """Hash only small inputs. Full-reference stat is a weaker identity check."""
    root = Path(project_root)
    paths = ['data/clean/crosswalk.parquet', 'data/clean/crosswalk_membership_bridge.parquet',
             'data/raw/compustat/Compustat Fundamentals Annual.csv',
             'data/raw/revelio/revelio_company_mapping_20261023.csv']
    result = {'rule_version': RULE_VERSION, 'sources': {}}
    for relative in paths:
        path = root / relative
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(block)
        result['sources'][relative] = {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}
    for relative in ['data/raw/revelio/eudkcvtewjnwtygx.csv.gz',
                     'data/raw/crsp/crsp_sp500_20261022.csv']:
        stat = (root / relative).stat()
        result['sources'][relative] = {'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                                      'content_hashed': False}
    notebook = json.loads((root / 'code/notebooks/01b_complete_revelio_matching.ipynb').read_text(encoding='utf-8'))
    functions = []
    for cell in notebook['cells']:
        if cell['cell_type'] != 'code':
            continue
        for node in ast.parse(''.join(cell['source'])).body:
            if isinstance(node, ast.FunctionDef) and node.name == 'discover_full_identifier_candidates':
                functions.append(ast.dump(node, include_attributes=False))
    if len(functions) != 1:
        raise ValueError('Expected exactly one discovery function in the notebook.')
    result['discovery_function_sha256'] = hashlib.sha256(functions[0].encode()).hexdigest()
    result['identifier_helpers_sha256'] = hashlib.sha256('\n'.join(
        ast.dump(ast.parse(inspect.getsource(function)), include_attributes=False)
        for function in (text_key, positive_digits, cusip_audit_key)
    ).encode()).hexdigest()
    result['limitation'] = 'Fingerprints recorded at checkpoint time, not retrospectively at scan time.'
    return result


def require_state(namespace):
    missing = [key for key in STATE_KEYS if key not in namespace or namespace[key] is None]
    if missing:
        raise RuntimeError('Missing retained evidence: ' + ', '.join(missing) +
                           '. Restore an explicitly selected checkpoint or keep the original live kernel. '
                           'Saved display outputs are insufficient; no scan has been started.')
    return {key: namespace[key] for key in STATE_KEYS}


def _pairs(frame):
    return set(frame[['gvkey', 'rcid']].itertuples(index=False, name=None))


def _triples(frame):
    return set(frame[['gvkey', 'rcid', 'method']].itertuples(index=False, name=None))


def validate_retained_state(state):
    """Strict contract for the already reported run; changed data require review."""
    crosswalk = state['crosswalk_v1']
    targets = set(crosswalk.gvkey)
    assert len(crosswalk) == len(targets) == 652
    assert all(isinstance(g, str) and re.fullmatch(r'[0-9]{6}', g) for g in targets)
    assert crosswalk.firm_id.eq('gvkey:' + crosswalk.gvkey).all()
    assert crosswalk.rcid.isna().all() and crosswalk.decision_basis.isna().all()
    saved = {(r.gvkey, c['rcid'], m) for r in crosswalk.itertuples(index=False)
             for c in json.loads(r.revelio_candidates_json) for m in c['methods']}
    assert saved == set(state['saved_candidates']) and len(saved) == 1638
    assert len({(g, r) for g, r, m in saved}) == 565
    evidence = state['full_id_evidence']
    source = state['full_id_source_evidence']
    annual = state['compustat_evidence']
    assert source.compustat_row.is_unique and annual.compustat_row.is_unique
    base_columns = [c for c in source if c not in ('cik_comparison', 'cusip_comparison')]
    pd.testing.assert_frame_equal(source[base_columns], annual[base_columns])
    assert source.cik_comparison.eq(source.cik.map(positive_digits)).all()
    assert source.cusip_comparison.eq(source.cusip.map(lambda v: cusip_audit_key(v)[0])).all()
    assert len(evidence) == 2001 and len(_pairs(evidence)) == 780
    assert evidence.gvkey.nunique() == 651
    assert evidence.revelio_source_row.nunique() == 780
    assert evidence.groupby('gvkey').rcid.nunique().gt(1).sum() == 115
    assert not evidence.duplicated(['gvkey', 'rcid', 'method', 'revelio_source_row']).any()
    assert state['full_id_scan_summary']['rows_scanned'] == 33_961_589
    assert state['full_id_scan_summary']['retained_evidence_records'] == 2001
    assert state['full_id_scan_summary']['identifier_hit_rows_without_rcid'] == 0
    assert evidence.groupby('method').gvkey.nunique().to_dict() == {'cik': 610, 'direct_gvkey': 642, 'full_cusip': 639}
    assert evidence.groupby('method').size().to_dict() == {'cik': 720, 'direct_gvkey': 642, 'full_cusip': 639}
    assert targets - set(evidence.gvkey) == {'179700'}
    lookup = {'direct_gvkey': {}, 'cik': {}, 'full_cusip': {}}
    for r in source.itertuples(index=False):
        for method, key in [('direct_gvkey', r.gvkey_key), ('cik', r.cik_comparison), ('full_cusip', r.cusip_comparison)]:
            if key:
                lookup[method].setdefault((r.gvkey_key, key), []).append(int(r.compustat_row))
    for r in evidence.itertuples(index=False):
        assert r.gvkey in targets and isinstance(r.rcid, str) and r.rcid == text_key(r.revelio_rcid_raw) != ''
        assert all(isinstance(getattr(r, 'revelio_' + f + '_raw'), str) for f in RAW_FIELDS)
        assert isinstance(r.revelio_source_row, (int, np.integer)) and 1 <= r.revelio_source_row <= 33_961_589
        key = {'direct_gvkey': text_key(r.revelio_gvkey_raw),
               'cik': positive_digits(r.revelio_cik_raw),
               'full_cusip': cusip_audit_key(r.revelio_cusip_raw)[0]}[r.method]
        assert key and r.comparison_key == key
        if r.method == 'direct_gvkey':
            assert key == r.gvkey
        assert tuple(r.compustat_rows) == tuple(lookup[r.method].get((r.gvkey, key), []))
        assert r.method == 'direct_gvkey' or len(r.compustat_rows) > 0
    local = state['local_candidate_evidence']
    additional = state['additional_local_candidates']
    assert local.evidence_id.is_unique and len(_pairs(local)) == 73
    assert _triples(local) == _triples(additional)
    assert local.gvkey.nunique() == 43 and set(local.gvkey) <= targets
    assert not ({g for g, r, m in saved} & set(local.gvkey))
    assert set(local.method) == {'ticker'}  # This is the recorded historical supplement.
    previous = {(g, r) for g, r, m in saved} | _pairs(local)
    assert len(previous) == 638 and len({g for g, r in previous}) == 608
    assert len(_pairs(evidence) - previous) == 216
    assert len(previous - _pairs(evidence)) == 74
    reference = state['revelio_local']
    for r in local.itertuples(index=False):
        ref = r.revelio_evidence
        original = reference.iloc[int(ref['revelio_source_row']) - 1]
        assert text_key(original.rcid) == r.rcid
        for field in RAW_FIELDS:
            a, b = ref[field], original[field]
            assert (pd.isna(a) and pd.isna(b)) or a == b
        assert r.source_evidence['gvkey'] == r.gvkey
        assert r.source_evidence['ticker_key'] == ref['ticker_key'] == r.comparison_key
    return pd.DataFrame([
        ('target_firms', 652), ('original_pairs', 565), ('original_method_triples', 1638),
        ('earlier_supplement_pairs', 73), ('full_reference_pairs', 780),
        ('full_reference_evidence_records', 2001), ('full_reference_candidate_firms', 651),
        ('full_reference_multiple_rcid_firms', 115), ('accepted_rcids', 0),
    ], columns=['check', 'observed_value']).assign(status='PASS')


def capture_checkpoint(namespace, project_root, destination):
    state = require_state(namespace)
    validate_retained_state(state)
    disk_crosswalk = pd.read_parquet(Path(project_root) / 'data/clean/crosswalk.parquet')
    pd.testing.assert_frame_equal(state['crosswalk_v1'], disk_crosswalk)
    state = dict(state)
    state['checkpoint_provenance_notes'] = namespace.get(
        'revelio_full_provenance', namespace.get('checkpoint_provenance_notes', {}))
    return write_bundle(destination, state, source_fingerprints(project_root))


def restore_checkpoint(project_root, destination):
    state = read_bundle(destination, source_fingerprints(project_root))
    validate_retained_state(state)
    pd.testing.assert_frame_equal(state['crosswalk_v1'], pd.read_parquet(
        Path(project_root) / 'data/clean/crosswalk.parquet'))
    state['full_id_status'] = 'SCAN_COMPLETE_REVIEW_REQUIRED'
    state['revelio_full_provenance'] = state.get('checkpoint_provenance_notes', {})
    return state


def build_evidence(state):
    """One record per origin/target/RCID/method/source row (legacy source pairs retained)."""
    records = []
    for index, r in enumerate(state['full_id_evidence'].to_dict('records'), 1):
        records.append({'evidence_id': f'full:{index}', 'origin': 'full_reference',
                        'gvkey': r['gvkey'], 'rcid': r['rcid'], 'method': r['method'],
                        'matching_rule': RULE_VERSION, 'comparison_key': r['comparison_key'],
                        'revelio_source_row': r['revelio_source_row'], 'compustat_rows': tuple(r['compustat_rows']),
                        'validated_full_cusip_support': r['method'] == 'full_cusip',
                        'revelio_raw': {f: r['revelio_' + f + '_raw'] for f in RAW_FIELDS},
                        'source_evidence': {'full_evidence_record_ordinal': index}, 'legacy_flags': {}})
    annual = state['compustat_evidence']
    reference = state['revelio_local'].copy(deep=True)
    reference['revelio_source_row'] = range(1, len(reference) + 1)
    grouped = {g: frame for g, frame in annual.groupby('gvkey_key')}
    crosswalk_positions = {g: i for i, g in enumerate(state['crosswalk_v1'].gvkey, 1)}
    for gvkey, rcid, method in sorted(state['saved_candidates']):
        source = grouped.get(gvkey, annual.iloc[:0])
        refs = reference.loc[reference.rcid_key.eq(rcid)]
        if method == 'direct_gvkey':
            refs = refs.loc[refs.gvkey_key.eq(gvkey)]
        else:
            column = 'cik_for_comparison' if method == 'cik' else 'cusip_for_comparison'
            refs = refs.loc[refs[column].notna() & refs[column].isin(source[column].dropna())]
        assert not refs.empty, f'Original candidate evidence unavailable: {(gvkey, rcid, method)}'
        for ref in refs.to_dict('records'):
            key = gvkey if method == 'direct_gvkey' else ref[column]
            matching = source if method == 'direct_gvkey' else source.loc[source[column].eq(key)]
            records.append({'evidence_id': f'v1:{gvkey}:{rcid}:{method}:{ref["revelio_source_row"]}',
                            'origin': 'notebook01', 'gvkey': gvkey, 'rcid': rcid, 'method': method,
                            'matching_rule': 'notebook01_shape_only_full_cusip' if method == 'full_cusip' else 'notebook01_original',
                            'comparison_key': key, 'revelio_source_row': ref['revelio_source_row'],
                            'compustat_rows': tuple(int(x) for x in matching.compustat_row),
                            'validated_full_cusip_support': method == 'full_cusip' and bool(cusip_audit_key(key)[0]),
                            'revelio_raw': {f: ref[f] for f in reference.columns if not f.endswith(('_key', '_comparison'))},
                            'source_evidence': {'crosswalk_row': crosswalk_positions[gvkey],
                                                'candidate_json_field': 'revelio_candidates_json'}, 'legacy_flags': {}})
    for r in state['local_candidate_evidence'].to_dict('records'):
        src = r['source_evidence']
        records.append({'evidence_id': f'legacy:{r["evidence_id"]}', 'origin': 'earlier_local',
                        'gvkey': r['gvkey'], 'rcid': r['rcid'], 'method': r['method'],
                        'matching_rule': 'historical_local_supplement_unchanged', 'comparison_key': r['comparison_key'],
                        'revelio_source_row': r['revelio_evidence']['revelio_source_row'],
                        'compustat_rows': (int(src['source_row']),) if src['source'] == 'compustat' else (),
                        'validated_full_cusip_support': False, 'revelio_raw': r['revelio_evidence'].copy(),
                        'source_evidence': src.copy(),
                        'legacy_flags': {k: v for k, v in r.items() if k not in (
                            'evidence_id', 'gvkey', 'rcid', 'method', 'comparison_key', 'source_evidence', 'revelio_evidence')}})
    result = pd.DataFrame(records)
    assert result.evidence_id.is_unique
    assert _triples(result.loc[result.origin.eq('notebook01')]) == set(state['saved_candidates'])
    assert _triples(result.loc[result.origin.eq('full_reference')]) == _triples(state['full_id_evidence'])
    assert len(result.loc[result.origin.eq('earlier_local')]) == len(state['local_candidate_evidence'])
    return result


def _values(values):
    return tuple(sorted({str(v) for v in values if text_key(v)}))


def _comparison(left, right, normalizer):
    a, b = {normalizer(v) for v in left} - {''}, {normalizer(v) for v in right} - {''}
    if not a or not b:
        return 'missing_or_invalid'
    if a & b:
        return 'agreement' if b <= a else 'mixed_agreement_and_disagreement'
    return 'disagreement_all_observed'


def summarize_candidates(crosswalk, annual, evidence):
    rows = []
    annual_groups = {g: frame for g, frame in annual.groupby('gvkey_key')}
    for (gvkey, rcid), group in evidence.groupby(['gvkey', 'rcid'], sort=True):
        source = annual_groups.get(gvkey, annual.iloc[:0])
        refs = group.revelio_raw.tolist()
        raw = {f: _values(r.get(f, pd.NA) for r in refs) for f in RAW_FIELDS}
        methods = tuple(sorted(set(group.method)))
        origins = tuple(sorted(set(group.origin)))
        cik_result = _comparison(source.cik, raw['cik'], positive_digits)
        gvkey_result = _comparison([gvkey], raw['gvkey'], text_key)
        validated = bool(group.validated_full_cusip_support.any())
        legacy_sources = group.loc[group.origin.eq('earlier_local'), 'source_evidence']
        rows.append({'gvkey': gvkey, 'rcid': rcid, 'research_company_names': _values(source.conm),
                     'revelio_company_names': raw['company'], 'evidence_methods': methods, 'evidence_origins': origins,
                     'direct_gvkey_support': 'direct_gvkey' in methods, 'normalized_cik_support': 'cik' in methods,
                     'validated_full_cusip_support': validated,
                     'legacy_shape_only_cusip_support': bool(((group.method == 'full_cusip') & ~group.validated_full_cusip_support).any()),
                     'supplementary_ticker_name_support': bool(set(methods) & {'ticker', 'normalized_company_name'}),
                     'evidence_ids': tuple(group.evidence_id), 'evidence_record_count': len(group),
                     'compustat_rows': tuple(sorted(set(source.compustat_row))),
                     'supporting_compustat_rows': tuple(sorted({int(i) for values in group.compustat_rows for i in values})),
                     'revelio_source_references': tuple(sorted(set(zip(group.origin.map(
                         lambda origin: 'full_reference' if origin == 'full_reference' else 'ticker_filtered_reference'), group.revelio_source_row)))),
                     'compustat_cik_raw': _values(source.cik), 'compustat_cusip_raw': _values(source.cusip),
                     'compustat_ticker_raw': _values(source.tic), 'annual_reporting_dates': _values(source.datadate),
                     'legacy_dated_source_evidence_ids': tuple(group.loc[group.origin.eq('earlier_local'), 'evidence_id']),
                     'legacy_observed_dates': tuple(sorted({str(d) for s in legacy_sources
                         for d in (s.get('observed_dates') if isinstance(s.get('observed_dates'), (tuple, list)) else ())
                         if text_key(d)})),
                     **{'revelio_' + f + '_raw': raw[f] for f in RAW_FIELDS},
                     'cik_comparison': cik_result, 'gvkey_comparison': gvkey_result,
                     'has_identifier_conflict': any(s in {'disagreement_all_observed', 'mixed_agreement_and_disagreement'} for s in (cik_result, gvkey_result)),
                     'parent_scope_observed': bool({text_key(v) for v in raw['ultimate_parent_rcid']} - {rcid}),
                     'child_rcid_observed': bool(raw['child_rcid']),
                     'historical_validity_status': 'unverified_snapshot_association',
                     'quality_assessment_status': 'pending_part_2_3'})
    candidates = pd.DataFrame(rows)
    counts = candidates.groupby('gvkey').rcid.nunique()
    rcid_counts = candidates.groupby('rcid').gvkey.nunique()
    candidates['candidate_count'] = candidates.gvkey.map(counts)
    candidates['multiple_rcid_flag'] = candidates.candidate_count.gt(1)
    candidates['rcid_target_count'] = candidates.rcid.map(rcid_counts)
    candidates['rcid_shared_across_targets'] = candidates.rcid_target_count.gt(1)
    firms = crosswalk[['gvkey']].copy(deep=True)
    names = {g: _values(frame.conm) for g, frame in annual_groups.items()}
    firm_candidates = candidates.groupby('gvkey').rcid.agg(lambda x: tuple(sorted(set(x))))
    firms['research_company_names'] = [names.get(g, ()) for g in firms.gvkey]
    firms['research_company_name'] = pd.Series([' | '.join(v) if v else pd.NA for v in firms.research_company_names], index=firms.index, dtype='string')
    firms['candidate_rcids'] = [firm_candidates.get(g, ()) for g in firms.gvkey]
    firms['candidate_count'] = firms.candidate_rcids.map(len)
    firms['multiple_rcid_flag'] = firms.candidate_count.gt(1)
    firms['accepted_rcid'] = pd.Series(pd.NA, index=firms.index, dtype='string')
    firms['human_review_status'] = 'not_reviewed'
    return candidates, firms


def consolidate(state):
    checks = validate_retained_state(state)
    before = state_digest(state)
    evidence = build_evidence(state)
    candidates, firms = summarize_candidates(state['crosswalk_v1'], state['compustat_evidence'], evidence)
    baseline = {(g, r) for g, r, m in state['saved_candidates']}
    local = _pairs(state['local_candidate_evidence'])
    full = _pairs(state['full_id_evidence'])
    previous = baseline | local
    union = previous | full
    assert _pairs(candidates) == union and len(candidates) == 854
    assert len(firms) == firms.gvkey.nunique() == 652
    assert list(firms.gvkey) == list(state['crosswalk_v1'].gvkey)
    assert firms.candidate_count.sum() == len(candidates)
    assert set(firms.loc[firms.candidate_count.eq(0), 'gvkey']) == {'179700'}
    assert firms.accepted_rcid.isna().all()
    assert set(candidates.gvkey) <= set(firms.gvkey)
    assert sum(candidates.evidence_record_count) == len(evidence)
    assert {i for ids in candidates.evidence_ids for i in ids} == set(evidence.evidence_id)
    assert not candidates.duplicated(['gvkey', 'rcid']).any()
    membership = pd.DataFrame([{'gvkey': g, 'rcid': r, 'in_notebook01': (g, r) in baseline,
                               'in_earlier_supplement': (g, r) in local, 'in_full_reference': (g, r) in full}
                              for g, r in sorted(union)])
    reconciliation = pd.DataFrame([
        ('Notebook 01', len(baseline)), ('Earlier supplement', len(local)),
        ('Earlier union', len(previous)), ('Full reference', len(full)),
        ('Earlier/full intersection', len(previous & full)), ('Full only', len(full - previous)),
        ('Earlier only — retained', len(previous - full)), ('Consolidated union', len(union)),
    ], columns=['candidate_pair_set', 'candidate_pairs'])
    method_summary = evidence.groupby(['origin', 'method']).agg(
        target_firms=('gvkey', 'nunique'), evidence_records=('evidence_id', 'size')).reset_index()
    method_summary['candidate_pairs'] = [len(_pairs(evidence.loc[evidence.origin.eq(r.origin) & evidence.method.eq(r.method)])) for r in method_summary.itertuples(index=False)]
    assert before == state_digest(state), 'Consolidation mutated retained input evidence.'
    checks = pd.concat([checks, pd.DataFrame([
        ('consolidated_pairs', len(candidates), 'PASS'),
        ('consolidated_firm_rows', len(firms), 'PASS'),
        ('input_evidence_digest_unchanged', before, 'PASS'),
        ('all_pair_evidence_links_resolve', len(evidence), 'PASS'),
    ], columns=checks.columns)], ignore_index=True)
    return {'consolidated_evidence': evidence, 'consolidated_candidates': candidates,
            'consolidated_firm_index': firms, 'candidate_membership': membership,
            'candidate_reconciliation': reconciliation, 'consolidation_method_summary': method_summary,
            'consolidation_checks': checks, 'consolidated_unmatched': firms.loc[firms.candidate_count.eq(0)].copy()}
