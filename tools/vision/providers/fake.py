"""Explicit synthetic test provider; makes no claims about real drawings."""
import hashlib
from ..contract import _id
class FakeProvider:
    name = 'fake'
    requires_live = False

    def inspect(self, request, images, *, model, timeout):
        return {'observation': 'Synthetic transport proof only; human review required.',
                'status': 'unresolved', 'provider_run_id': _id('fake-', [request, [hashlib.sha256(data).hexdigest() for data in images]])}
