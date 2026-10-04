"""Network-free review plan, explicit render/fake execution, opt-in live execution."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from tools.query_core.query import QueryCore
from .review import plan_review, execute_review
from .runner import VisionExecutionError, require_live_budget
from .contract import VisionContractError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'execute'))
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--entity', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--provider', choices=('none', 'fake', 'openai'), default='none')
    parser.add_argument('--stages', nargs='+', choices=('V0', 'V1', 'V2', 'V3'), default=['V1', 'V2', 'V3'])
    parser.add_argument('--policy', choices=('focused', 'exhaustive'), default='focused')
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--source', action='append', default=[], metavar='DOCUMENT_ID=PDF')
    parser.add_argument('--dpi', type=int, default=300)
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--max-live-calls', type=int, help='Required paid-call budget for live execution')
    parser.add_argument('--output', type=Path, default=Path('artifacts/vision-review'))
    args = parser.parse_args(argv)
    try:
        sources = {}
        for source in args.source:
            key, value = source.split('=', 1)
            if not key or not value or key in sources:
                raise VisionContractError('invalid source mapping')
            sources[key] = Path(value)
        with QueryCore(args.root / args.database) as core:
            plan = plan_review(core, args.entity, model=args.model, provider=args.provider,
                               stages=args.stages, dpi=args.dpi, policy=args.policy)
            if args.action == 'plan':
                print(json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False))
                return 0
            if not plan['jobs']:
                print(json.dumps({'status': 'insufficient_evidence', 'plan_id': plan['plan_id'],
                                  'request_count': 0}))
                return 1
            provider = None
            if args.provider == 'fake':
                from .providers.fake import FakeProvider
                provider = FakeProvider()
            elif args.provider == 'openai':
                if not args.live:
                    raise VisionExecutionError('live_opt_in_required')
                require_live_budget(plan['network_calls_planned'], args.max_live_calls)
                if not os.environ.get('OPENAI_API_KEY'):
                    print(json.dumps({'status': 'SKIPPED', 'reason': 'auth_missing'}))
                    return 0
                from .providers.openai import OpenAIProvider
                provider = OpenAIProvider()
            packet = execute_review(core, plan, sources, root=args.root,
                                    database=args.database, output=args.output,
                                    provider=provider, live=args.live, timeout=args.timeout,
                                    max_live_calls=args.max_live_calls)
        # The renderer's artifact policy applies equally to exported JSON.
        from tools.pdf_pipeline.pipeline import _render_output_directory
        directory = _render_output_directory(args.root.resolve(), args.output)
        target = directory / (packet['packet_id'] + '.json')
        # Atomic replacement replaces a pre-existing symlink itself, never its target.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                             prefix='.review-', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(packet, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print(json.dumps({'status': packet['status'], 'packet_id': packet['packet_id'],
                          'output': str(target), 'failure_count': len(packet['failures'])}))
        return 0 if packet['status'] in ('ok', 'rendered') else 1
    except Exception as exc:
        # No exception message, raw transport, credential, or source text is logged.
        print(json.dumps({'status': 'failed', 'code': VisionExecutionError(exc.code).code if isinstance(exc, VisionExecutionError)
                          else 'invalid_review_input'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
