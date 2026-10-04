# Phase 9: attachments and Gemma workflows

Phase 9 adds a local attachment pipeline and preserves DeepSeek's authority over visual analysis.

## File pipeline

```text
User uploads a file
  ↓
AttachmentPipeline stores it in a task-scoped workspace directory
  ↓
Filename, size, MIME type, magic bytes, and executable policy are validated
  ↓
Deterministic local safety scan runs
  ↓
Text/PDF extraction or image preview metadata is generated
  ↓
A safe description and extracted excerpt enter DeepSeek's context
  ↓
DeepSeek decides whether to delegate to the vision role
  ↓
Gemma receives the named attachment-analysis objective
  ↓
Gemma findings return in a structured JSON envelope
  ↓
DeepSeek answers, explains, or delegates the next step
```

## Secure local storage

- Maximum attachment size: 25 MB
- Task-scoped directories under `workspace/attachments/<task_id>/`
- Stored files receive restrictive local permissions
- Filenames are reduced to safe basename characters
- Symlinks and non-regular source files are rejected
- Executable extensions are rejected
- Image/PDF magic bytes are checked against declared types
- A deterministic scan rejects obvious binary/script hazards
- The scan is a local safety check, not a replacement for enterprise antivirus

The SQLite attachment record includes the SHA-256 hash, MIME type, scan status, preview status, extracted text, safe description, and Gemma support flag.

## Preview and extraction

- Plain text, JSON, and CSV content is decoded into a bounded excerpt
- PDFs use local `pdftotext` when available
- PNG, JPEG, and GIF dimensions are read from headers for preview metadata
- Images are marked as Gemma-supported visual attachments
- PDFs are marked Gemma-supported because they may require visual inspection after text extraction

## DeepSeek and Gemma boundary

Uploading a file does not bypass DeepSeek. The attachment description is added to the persisted conversation as safe user-visible context. DeepSeek decides whether to issue:

```json
{
  "type": "delegate_to_model",
  "arguments": {
    "role": "vision",
    "objective": "Read the equation and diagram",
    "inputs": {"attachment_id": "..."},
    "expected_output": "structured visual findings"
  }
}
```

The executor rejects `controller` as a delegation target. When the named role is `vision`, the result is normalized into:

```json
{
  "specialist": "gemma",
  "analysis_type": "visual_attachment",
  "findings": {"...": "..."}
}
```

The structured result returns to DeepSeek for interpretation and final response.

## API and GUI

The existing multipart endpoint now performs the complete pipeline:

```text
POST /api/tasks/:id/attachments
```

The GUI's Attach control uses a browser file picker, and the Files tab shows the stored attachment and its metadata. The task transcript receives the safe description so the next DeepSeek turn can use it.
