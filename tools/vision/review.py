"""Transient deterministic review planning, rendering, execution and source trace.

No database writes. Query Core and contract imports remain stdlib-only. Rendering
and provider imports happen only when explicitly executing a plan.
"""
from copy import deepcopy
from itertools import combinations
from pathlib import Path
import hashlib
import json

from tools.query_core.vision_routing import route_vision_evidence
from .contract import _id, _require, _text, build_vision_inspection_request
from .context_contract import build_vision_context_inspection_request
from .stages import build_stage_request
from .runner import run_vision, VisionExecutionError, require_live_budget

# Fixed policy shared by fake/render-only/live plans. Never enumerate omitted pairs.
MAX_V2_COOCCURRENCE_JOBS = 32


def _evidence_surface(row, navigation):
    bbox = [row[k] for k in ('x_min', 'y_min', 'x_max', 'y_max')]
    return {'evidence_id': row['id'],
            'document': {'id': row['document_id'], 'identity': row['document_identity'],
                         'source_filename': row['source_filename'],
                         'source_sha256': row['source_sha256']},
            'pdf_page': row['pdf_page'], 'bbox': None if all(v is None for v in bbox) else bbox,
            'coordinate_space': row['coordinate_space'], 'navigation': navigation}


def collect_review_context(core, entity_id):
    """Augment unchanged Phase 4 context with explicitly stored one-hop references."""
    context = core.get_architectural_evidence_context(entity_id)
    _require(context is not None, 'semantic entity not found')
    context = deepcopy(context)
    refs = {}
    # Also collect references on occurrence views; never traverse recursively.
    for occurrence in context['drawing_occurrences']:
        view = occurrence.get('view_id')
        if view:
            refs.update((r['id'], r) for r in core.get_drawing_references_for_view(view))
    if core.has_drawing_reference_capability():
        evidence_ids = {r['evidence_id'] for r in context['evidence']}
        for occurrence in context['drawing_occurrences']:
            if occurrence.get('evidence_id'):
                evidence_ids.add(occurrence['evidence_id'])
        for evidence_id in sorted(evidence_ids):
            for row in core.connection.execute(
                'SELECT id FROM drawing_references WHERE source_evidence_id=? ORDER BY id',
                (evidence_id,),
            ):
                ref = core.get_drawing_reference(row['id'])
                refs[ref['id']] = ref
    original_ids = {r['evidence_id'] for r in context['evidence']}
    documents = {r['id']: r for r in context['source_documents']}
    reference_targets = {}
    for ref in refs.values():
        for side in ('source', 'target'):
            value = ref.get(side)
            row = value.get('evidence') if value else None
            if row and row['id'] not in original_ids:
                surface = _evidence_surface(row, value['navigation'])
                context['evidence'].append(surface)
                original_ids.add(row['id'])
                documents[row['document_id']] = surface['document']
        target = ref.get('target') or {}
        if target.get('evidence'):
            reference_targets[ref['id']] = target['evidence']['id']
        elif target.get('navigation'):
            navigation = target['navigation']
            document_id = (navigation.get('document') or {}).get('id')
            page = navigation.get('pdf_page')
            if document_id and type(page) is int and page > 0:
                document = core.connection.execute(
                    'SELECT id,identity,source_filename,source_sha256 FROM documents WHERE id=?',
                    (document_id,),
                ).fetchone()
                if document:
                    document = dict(document)
                    # A page surface from recorded sheet navigation is a deterministic
                    # projection, not a new stored source evidence fact.
                    surface_id = _id('review-reference-surface-', [ref['id'], navigation])
                    context['evidence'].append(dict(
                        evidence_id=surface_id, document=document, pdf_page=page, bbox=None,
                        coordinate_space=None, navigation=deepcopy(navigation),
                        evidence_class='deterministic_derived', drawing_reference_id=ref['id']))
                    reference_targets[ref['id']] = surface_id
                    documents[document_id] = document
    context['reference_targets'] = reference_targets
    context['source_documents'] = sorted(documents.values(), key=lambda r: (r['identity'], r['id']))
    context['evidence'].sort(key=lambda r: r['evidence_id'])
    context['retrieval_coverage'] = deepcopy(context['coverage'])
    context['coverage'].update(evidence_count=len(context['evidence']),
                               source_document_count=len(context['source_documents']))
    context['drawing_references'] = [refs[k] for k in sorted(refs)]
    return context


def _page_projection(candidate):
    result = deepcopy(candidate)
    result.update(input_scope='page', bbox=None, coordinate_space=None, bbox_quality='page_only')
    surface_key = json.dumps([result['document']['id'], result['pdf_page'], 'page', None],
                             ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    result['candidate_id'] = 'vision-candidate-' + hashlib.sha256(surface_key.encode()).hexdigest()
    return result


def _v3_surfaces(core, candidates, protected_ids):
    """Union only exact stored cells in one table row; retain every member.

    Page identity or proximity alone never establishes membership. Explicit
    reference endpoints remain separate exact inputs. Mixed/non-cell candidates
    fail closed to their original surface.
    """
    groups, unchanged = {}, []
    cells = {}
    for candidate in candidates:
        occurrences = [r for r in candidate['source_refs'] if r['kind'] == 'drawing_occurrence']
        keys = set()
        valid = bool(occurrences) and candidate['input_scope'] == 'region'
        for ref in occurrences:
            if ref.get('source_kind') != 'pdf_table_cell':
                valid = False
                break
            cell_id = ref['source_id']
            if cell_id not in cells:
                cells[cell_id] = core.get_pdf_table_cell(cell_id)
            cell = cells[cell_id]
            if (cell is None or cell['bbox'] != candidate['bbox']
                    or cell['document'] != candidate['document']
                    or cell['pdf_page'] != candidate['pdf_page']
                    or cell['coordinate_space'] != candidate['coordinate_space']
                    or cell['row_span'] != 1):
                valid = False
                break
            keys.add((candidate['document']['id'], candidate['pdf_page'],
                      cell['table']['table_id'], cell['row_index']))
        if not valid or len(keys) != 1 or candidate['candidate_id'] in protected_ids:
            unchanged.append(candidate)
        else:
            groups.setdefault(next(iter(keys)), []).append(candidate)
    replacements = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda c: c['candidate_id'])
        if len(members) == 1:
            unchanged.extend(members)
            continue
        boxes = [c['bbox'] for c in members]
        bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes)]
        refs = {_id('', ref): ref for c in members for ref in c['source_refs']}
        aggregate = dict(
            input_scope='region', document=deepcopy(members[0]['document']),
            pdf_page=key[1], bbox=bbox, bbox_quality=None,
            coordinate_space='pdf_points_top_left', navigation=None,
            evidence_class='deterministic_derived', provenance='exact_table_row_union_v1',
            aggregation_key=dict(document_id=key[0], pdf_page=key[1],
                                 table_id=key[2], row_index=key[3]),
            members=deepcopy(members), source_refs=[refs[k] for k in sorted(refs)],
            routing_reasons=sorted({'exact_table_row_union_v1'} | {
                reason for c in members for reason in c['routing_reasons']}))
        # Includes exact member IDs, geometry, navigation and provenance, never
        # iteration order, an LLM choice or a nearest-geometry calculation.
        aggregate['candidate_id'] = _id('vision-candidate-', aggregate)
        unchanged.append(aggregate)
        replacements.extend(dict(stage='V3', candidate_id=c['candidate_id'],
                                 replacement_candidate_id=aggregate['candidate_id'],
                                 reason='exact_table_row_union_v1') for c in members)
    # Reference inputs lead final inspection, followed by canonical surface IDs.
    return sorted(unchanged, key=lambda c: (c['candidate_id'] not in protected_ids,
                                           c['candidate_id'])), replacements


def plan_review(core, entity_id, *, model, provider='none', stages=('V1', 'V2', 'V3'),
                instruction='Inspect for discrepancies and uncertainty; cite only supplied evidence.',
                dpi=300, policy='focused'):
    """No network, image I/O or writes; every scheduled send is explicit."""
    _require(policy in ('focused', 'exhaustive'), 'invalid review policy')
    _text(model, 'model')
    _text(provider, 'provider')
    _text(instruction, 'instruction')
    _require(type(dpi) is int and 36 <= dpi <= 1200, 'invalid DPI')
    _require(isinstance(stages, (list, tuple)) and bool(stages)
             and all(s in ('V0', 'V1', 'V2', 'V3') for s in stages)
             and len(set(stages)) == len(stages), 'invalid stages')
    context = collect_review_context(core, entity_id)
    routed = route_vision_evidence(context)
    candidates = routed['candidates']
    for candidate in candidates:
        for ref_id, surface_id in context['reference_targets'].items():
            if any(r['kind'] == 'evidence' and r['id'] == surface_id for r in candidate['source_refs']):
                candidate['source_refs'].append(dict(kind='drawing_reference', id=ref_id, side='target'))
    resolved_refs = [r for r in context['drawing_references']
                     if r['resolution_state'] in ('exact', 'resolved_deterministically')]
    evidence_candidates = {r['id']: c for c in candidates for r in c['source_refs']
                           if r['kind'] == 'evidence'}
    protected_ids = set()
    for ref in resolved_refs:
        for evidence_id in (ref['source']['evidence']['id'],
                            context['reference_targets'].get(ref['id'])):
            candidate = evidence_candidates.get(evidence_id)
            if candidate:
                protected_ids.add(candidate['candidate_id'])
    jobs, skipped = [], []
    def add(stage, surfaces, relation=None, reason='exact_candidate'):
        content = dict(stage=stage, candidates=deepcopy(surfaces), relation=relation)
        jobs.append(dict(content, job_id=_id('vision-job-', content), selection_reason=reason))
    if 'V0' in stages:
        pages = {}
        for candidate in candidates:
            page = _page_projection(candidate)
            if page['candidate_id'] in pages:
                existing = pages[page['candidate_id']]
                refs = {_id('', r): r for r in existing['source_refs'] + page['source_refs']}
                existing['source_refs'] = [refs[k] for k in sorted(refs)]
                existing['routing_reasons'] = sorted(set(existing['routing_reasons'] + page['routing_reasons']))
            else:
                pages[page['candidate_id']] = page
        for page in pages.values():
            add('V0', [page])
    selective_v3 = 'V3' in stages and policy == 'focused' and bool(resolved_refs)
    for candidate in candidates:
        if 'V1' in stages:
            if candidate['input_scope'] == 'region':
                add('V1', [candidate])
            else:
                skipped.append(dict(stage='V1', candidate_id=candidate['candidate_id'],
                                    reason='page_has_no_context_bbox'))
        if 'V3' in stages and not selective_v3:
            add('V3', [candidate])
    replacements = []
    if selective_v3:
        v3_candidates, replacements = _v3_surfaces(core, candidates, protected_ids)
        skipped.extend(replacements)
        for candidate in v3_candidates:
            reason = ('explicit_reference_endpoint' if candidate['candidate_id'] in protected_ids
                      else candidate.get('provenance', 'exact_candidate'))
            add('V3', [candidate], reason=reason)
    cooccurrence = dict(limit=MAX_V2_COOCCURRENCE_JOBS, eligible_count=0, scheduled_count=0,
                        omitted_count=0, suppressed_count=0, reason=None)
    if 'V2' in stages:
        explicit_pairs = set()
        for ref in context['drawing_references']:
            source = evidence_candidates.get(ref['source']['evidence']['id'])
            target_id = context['reference_targets'].get(ref['id'])
            dest = evidence_candidates.get(target_id)
            if (ref['resolution_state'] not in ('exact', 'resolved_deterministically')
                    or source is None or dest is None or source['candidate_id'] == dest['candidate_id']):
                skipped.append(dict(stage='V2', reference_id=ref['id'],
                                    reason='relation_has_no_distinct_resolved_evidence_pair'))
                continue
            pair = tuple(sorted((source['candidate_id'], dest['candidate_id'])))
            if pair in explicit_pairs:
                skipped.append(dict(stage='V2', reference_id=ref['id'], reason='explicit_pair_already_scheduled'))
                continue
            explicit_pairs.add(pair)
            relation = dict(relation_type=ref['relation_type'],
                            source_refs=[dict(kind='drawing_reference', id=ref['id'])])
            add('V2', [source, dest], relation, reason='resolved_explicit_relation')
        # Shared entity occurrences are compared only after explicit pairs. The
        # count is algebraic; combinations is lazy and stops at the named limit.
        direct = [c for c in candidates if any(r['kind'] == 'drawing_occurrence'
                                             for r in c['source_refs'])]
        direct_ids = {c['candidate_id'] for c in direct}
        duplicate_count = sum(a in direct_ids and b in direct_ids for a, b in explicit_pairs)
        eligible = len(direct) * (len(direct) - 1) // 2 - duplicate_count
        scheduled = 0
        suppressed = eligible if policy == 'focused' and resolved_refs else 0
        if not (policy == 'focused' and resolved_refs):
            for left, right in combinations(direct, 2):
                if scheduled >= MAX_V2_COOCCURRENCE_JOBS:
                    break
                pair = tuple(sorted((left['candidate_id'], right['candidate_id'])))
                if pair in explicit_pairs:
                    continue
                relation = dict(relation_type='semantic_entity_cooccurrence',
                                source_refs=[dict(kind='semantic_entity', id=entity_id)])
                add('V2', [left, right], relation, reason='bounded_cooccurrence_fallback')
                scheduled += 1
        omitted = eligible - scheduled - suppressed
        reason = ('explicit_relation_first' if suppressed else
                  'deterministic_cooccurrence_limit' if omitted else None)
        cooccurrence.update(eligible_count=eligible, scheduled_count=scheduled, omitted_count=omitted,
                            suppressed_count=suppressed, reason=reason)
        if omitted or suppressed:
            skipped.append(dict(stage='V2', **cooccurrence))
    if not jobs:
        skipped.append(dict(reason='no_candidates' if not candidates else 'all_selected_stages_skipped'))
    content = dict(contract_version=1, semantic_entity_id=entity_id, provider=provider, model=model,
                   stages=list(stages), instruction=instruction, dpi=dpi, policy=policy, context=context,
                   candidates=candidates, jobs=jobs, skipped=skipped,
                   status='ready' if jobs else 'insufficient_evidence', v2_cooccurrence=cooccurrence,
                   v3_selection=dict(eligible_count=len(candidates) if 'V3' in stages else 0,
                                     scheduled_count=sum(j['stage'] == 'V3' for j in jobs),
                                     aggregated_member_count=len(replacements),
                                     reason='exact_table_row_union_v1' if replacements else None),
                   provider_calls_planned=len(jobs) if provider != 'none' else 0,
                   network_calls_planned=len(jobs) if provider not in ('none', 'fake') else 0,
                   requires_live_opt_in=provider not in ('none', 'fake'))
    return dict(content, plan_id=_id('vision-review-plan-', content))


def execute_review(core, plan, sources, *, root, database, output=Path('artifacts/vision-review'),
                   provider=None, live=False, timeout=60.0, max_live_calls=None):
    """Regenerate the whole plan before render/send, returning a source-faithful packet."""
    fresh = plan_review(core, plan['semantic_entity_id'], model=plan['model'],
                        provider=plan['provider'], stages=plan['stages'],
                        instruction=plan['instruction'], dpi=plan['dpi'], policy=plan.get('policy', 'exhaustive'))
    _require(fresh == plan, 'review plan changed; replan required')
    _require(isinstance(sources, dict), 'source mapping required')
    _require(type(live) is bool, 'invalid live opt-in')
    _require(type(timeout) in (int, float) and 0 < timeout <= 300, 'invalid timeout')
    if not plan['jobs']:
        return _review_packet(plan, [], [], [], [], [], 'insufficient_evidence')
    if provider is None:
        _require(plan['provider'] == 'none', 'planned provider is required')
    else:
        _require(provider.name == plan['provider'], 'provider mismatch')
        if getattr(provider, 'requires_live', True):
            if not live:
                raise VisionExecutionError('live_opt_in_required')
            require_live_budget(len(plan['jobs']), max_live_calls)
    # All source assignments/SHA are checked before any provider is called.
    for document in plan['context']['source_documents']:
        _require(document['id'] in sources, 'source PDF mapping missing')
        try:
            data = (Path(root) / sources[document['id']]).read_bytes()
        except (OSError, TypeError, ValueError):
            raise VisionExecutionError('source_unreadable') from None
        _require(hashlib.sha256(data).hexdigest() == document['source_sha256'], 'source SHA mismatch')
    from tools.pdf_pipeline.vision_render import _render_selected_candidate
    requests, executions, observations, failures, cache = [], [], [], [], {}
    for job in plan['jobs']:
        inputs, paths = [], []
        try:
            for candidate in job['candidates']:
                key = (candidate['candidate_id'], job['stage'] == 'V1')
                if key not in cache:
                    rendered = _render_selected_candidate(
                        Path(root), Path(database), plan['semantic_entity_id'], candidate['candidate_id'],
                        Path(sources[candidate['document']['id']]), dpi=plan['dpi'], output=Path(output),
                        context=job['stage'] == 'V1', _candidate=candidate)
                    path = Path(root) / rendered['output_path']
                    builder = (build_vision_context_inspection_request if job['stage'] == 'V1'
                               else build_vision_inspection_request)
                    cache[key] = (builder(rendered, path, plan['instruction']), path)
                item, path = cache[key]
                inputs.append(item)
                paths.append(path)
            request = (build_stage_request(job['stage'], inputs, plan['instruction'], relation=job['relation'])
                       if job['stage'] in ('V0', 'V2') else inputs[0])
            requests.append(request)
            executions.append(dict(job_id=job['job_id'], request_id=request['request_id'],
                                   image_paths=[str(p) for p in paths],
                                   source_refs=[c['source_refs'] for c in job['candidates']]))
            if provider is not None:
                observations.append(run_vision(request, paths, provider, model=plan['model'],
                                               live=live, timeout=timeout, max_live_calls=max_live_calls))
        except Exception as exc:
            # Persist only a safe category. Partial work stays a failure, never supported.
            failures.append(dict(job_id=job['job_id'], stage=job['stage'],
                                 code=VisionExecutionError(exc.code).code if isinstance(exc, VisionExecutionError) else 'evidence_failure'))
    assignments = [dict(document_id=d['id'], source_pdf=str(sources[d['id']]),
                        source_sha256=d['source_sha256']) for d in plan['context']['source_documents']]
    status = 'failed' if failures else ('ok' if provider else 'rendered')
    return _review_packet(plan, requests, executions, observations, failures, assignments, status)


def _review_packet(plan, requests, executions, observations, failures, assignments, status):
    content = dict(contract_version=1, evidence_class='review_packet', plan=deepcopy(plan),
                   requests=requests, executions=executions, observations=observations,
                   source_assignments=assignments, failures=failures, status=status,
                   human_review_required=True)
    return dict(content, packet_id=_id('vision-review-packet-', content))
