"""Run with Python 3.12 -S to protect the optional Vision runtime boundary."""
import sys
import tools.vision
import tools.vision.providers.openai
import tools.vision.providers.fake


def main():
    forbidden = ('httpx', 'pymupdf', 'fitz', 'jsonschema')
    assert not any(name.startswith(forbidden) for name in sys.modules)
    assert callable(tools.vision.plan_review)
    assert callable(tools.vision.run_vision)
    print('ok: stdlib Vision planning/contracts/runner; optional provider import isolated')


if __name__ == '__main__':
    main()
