# Phase 18: testing strategy

Phase 18 adds deterministic, role-isolated acceptance tests and an end-to-end matrix for the DeepSeek-first platform.

## Test principles

- Each model role is tested through its actual platform boundary, not through a generic chatbot assertion.
- DeepSeek remains the only planner in conversation tests.
- Specialist tests validate structured inputs, bounded outputs, and role-specific behavior.
- External services are replaced with deterministic local fixtures so CI does not require Ollama, browser credentials, GitHub credentials, or internet access.
- Failure, approval, cancellation, missing-model, and retry paths are tested as first-class outcomes.

## Coverage matrix

### DeepSeek controller

- Request understanding and user communication
- Appropriate specialist delegation
- Multi-turn planning and synthesis
- Approval handling
- Recovery after a failed command

### Qwen 2.5:3b research

- Search result normalization
- Browser/dynamic-page inspection contract
- Page extraction
- Citation generation
- Multi-source deduplication and comparison evidence

### Qwen2.5-Coder:7b

- Project creation and file editing
- Test execution and preview generation
- Error repair through retry
- Approval-gated GitHub push behavior

### Gemma 3:4b vision

- Image and screenshot ingestion
- PDF extraction/OCR boundary
- Structured visual question answering
- Mathematical reasoning from an uploaded image

### Llama 3.2:3b creative

- Creative concepts and briefs
- Visual direction
- Presentation structure
- Image-prompt generation
- Explicit external image-service boundary

### End-to-end scenarios

1. Conversation and user communication
2. Image analysis
3. Mathematics from an uploaded image
4. Internet research through a local research fixture
5. Research-to-website generation
6. Visual preview analysis
7. GitHub approval and push
8. User cancellation
9. Missing model handling
10. Model failure and retry
11. Mid-workflow user input request
12. Secret-protected operation

## Running the tests

```bash
python3 -m pytest -q
```

The Phase 18-specific matrix can be run independently:

```bash
python3 -m pytest tests/test_phase18_testing_strategy.py -q
```

The tests intentionally use local fake adapters and fixture tools. Live-provider smoke tests should be added separately and must never weaken the deterministic acceptance suite.
