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
from .runner import run_vision, VisionExecutionError


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


def plan_review(core, entity_id, *, model, provider='none', stages=('V1', 'V2', 'V3'),
                instruction='Inspect for discrepancies and uncertainty; cite only supplied evidence.',
                dpi=300):
    """No network, image I/O or writes; every scheduled send is explicit."""
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
    jobs, skipped = [], []
    def add(stage, surfaces, relation=None):
        content = dict(stage=stage, candidates=deepcopy(surfaces), relation=relation)
        jobs.append(dict(content, job_id=_id('vision-job-', content)))
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
    for candidate in candidates:
        if 'V1' in stages:
            if candidate['input_scope'] == 'region':
                add('V1', [candidate])
            else:
                skipped.append(dict(stage='V1', candidate_id=candidate['candidate_id'],
                                    reason='page_has_no_context_bbox'))
        if 'V3' in stages:
            add('V3', [candidate])
    if 'V2' in stages:
        def by_evidence(evidence_id):
            return next((c for c in candidates if any(
                r.get('kind') == 'evidence' and r.get('id') == evidence_id
                for r in c['source_refs'])), None)
        for ref in context['drawing_references']:
            source = by_evidence(ref['source']['evidence']['id'])
            target_id = context['reference_targets'].get(ref['id'])
            dest = by_evidence(target_id) if target_id else None
            if (ref['resolution_state'] not in ('exact', 'resolved_deterministically')
                    or source is None or dest is None or source['candidate_id'] == dest['candidate_id']):
                skipped.append(dict(stage='V2', reference_id=ref['id'],
                                    reason='relation_has_no_distinct_resolved_evidence_pair'))
                continue
            relation = {k: ref[k] for k in ('id', 'relation_type', 'resolution_state', 'provenance')}
            relation['source_refs'] = [deepcopy(ref)]
            add('V2', [source, dest], relation)
        # Same selected semantic entity has explicit occurrences across drawings.
        # This is a deterministic cooccurrence, not an inferred section/detail link.
        direct = [c for c in candidates if any(r['kind'] == 'drawing_occurrence'
                                             for r in c['source_refs'])]
        for left, right in combinations(direct, 2):
            relation = dict(id=_id('cooccurrence-', [entity_id, left['candidate_id'], right['candidate_id']]),
                            relation_type='semantic_entity_cooccurrence', resolution_state='exact',
                            provenance='deterministic_shared_semantic_entity',
                            source_refs=deepcopy(left['source_refs'] + right['source_refs']))
            add('V2', [left, right], relation)
    content = dict(contract_version=1, semantic_entity_id=entity_id, provider=provider, model=model,
                   stages=list(stages), instruction=instruction, dpi=dpi, context=context,
                   candidates=candidates, jobs=jobs, skipped=skipped,
                   provider_calls_planned=len(jobs) if provider != 'none' else 0,
                   network_calls_planned=len(jobs) if provider not in ('none', 'fake') else 0,
                   requires_live_opt_in=provider not in ('none', 'fake'))
    return dict(content, plan_id=_id('vision-review-plan-', content))


def execute_review(core, plan, sources, *, root, database, output=Path('artifacts/vision-review'),
                   provider=None, live=False, timeout=60.0):
    """Regenerate the whole plan before render/send, returning a source-faithful packet."""
    fresh = plan_review(core, plan['semantic_entity_id'], model=plan['model'],
                        provider=plan['provider'], stages=plan['stages'],
                        instruction=plan['instruction'], dpi=plan['dpi'])
    _require(fresh == plan, 'review plan changed; replan required')
    _require(isinstance(sources, dict), 'source mapping required')
    if provider is None:
        _require(plan['provider'] == 'none', 'planned provider is required')
    else:
        _require(provider.name == plan['provider'], 'provider mismatch')
        if getattr(provider, 'requires_live', True) and not live:
            raise VisionExecutionError('live_opt_in_required')
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
                                               live=live, timeout=timeout))
        except Exception as exc:
            # Persist only a safe category. Partial work stays a failure, never supported.
            failures.append(dict(job_id=job['job_id'], stage=job['stage'],
                                 code=exc.code if isinstance(exc, VisionExecutionError) else 'evidence_failure'))
    content = dict(contract_version=1, evidence_class='review_packet', plan=deepcopy(plan),
                   requests=requests, executions=executions, observations=observations,
                   source_assignments=[dict(document_id=d['id'], source_pdf=str(sources[d['id']]),
                                            source_sha256=d['source_sha256'])
                                       for d in plan['context']['source_documents']],
                   failures=failures, status='failed' if failures else ('ok' if provider else 'rendered'),
                   human_review_required=True)
    return dict(content, packet_id=_id('vision-review-packet-', content))
