"""Explicit synthetic test provider; makes no claims about real drawings."""
class FakeProvider:
    name = 'fake'
    requires_live = False

    def inspect(self, request, images, *, model, timeout):
        return {'observation': 'Synthetic transport proof only; human review required.',
                'status': 'unresolved', 'provider_run_id': 'fake-' + request['request_id']}
