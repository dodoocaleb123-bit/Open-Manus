"""Secure local attachment pipeline for Phase 9."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from execution import SQLiteStateStore

MAX_ATTACHMENT_BYTES = 250 * 1024 * 1024
ALLOWED_MIME_PREFIXES = ("image/", "text/", "application/pdf", "application/json", "application/csv")
BLOCKED_EXTENSIONS = {".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".com", ".msi", ".scr", ".ps1"}


class AttachmentError(ValueError):
    """Raised when an attachment fails local validation or safe storage."""


@dataclass(frozen=True)
class AttachmentRecord:
    attachment_id: str
    task_id: str
    filename: str
    path: str
    media_type: str
    size: int
    sha256: str
    scan_status: str
    preview_status: str
    extracted_text: str
    description: str
    gemma_supported: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AttachmentPipeline:
    """Validate, scan, store, and describe files without executing them."""

    def __init__(self, store: SQLiteStateStore, *, max_bytes: int = MAX_ATTACHMENT_BYTES) -> None:
        self.store = store
        self.max_bytes = max_bytes

    def ingest(self, task_id: str, filename: str, media_type: str | None, data: bytes) -> AttachmentRecord:
        safe_name = self._safe_filename(filename)
        detected_type = self._detect_type(safe_name, media_type, data)
        self._validate(safe_name, detected_type, data)
        scan_status = self._scan(safe_name, detected_type, data)
        attachment_id = f"att_{uuid.uuid4().hex}"
        target_dir = self._secure_task_dir(task_id)
        target = target_dir / f"{attachment_id}_{safe_name}"
        target.write_bytes(data)
        os.chmod(target, 0o600)
        sha256 = hashlib.sha256(data).hexdigest()
        extracted_text, preview_status = self._preview(target, detected_type, data)
        description = self._description(safe_name, detected_type, len(data), extracted_text, data)
        gemma_supported = detected_type.startswith("image/") or detected_type == "application/pdf"
        metadata = {
            "filename": safe_name,
            "size": len(data),
            "sha256": sha256,
            "scan_status": scan_status,
            "preview_status": preview_status,
            "extracted_text": extracted_text[:12000],
            "description": description,
            "gemma_supported": gemma_supported,
            "uploaded": True,
        }
        self.store.record_attachment(task_id, attachment_id, str(target), detected_type, metadata)
        return AttachmentRecord(
            attachment_id=attachment_id,
            task_id=task_id,
            filename=safe_name,
            path=str(target),
            media_type=detected_type,
            size=len(data),
            sha256=sha256,
            scan_status=scan_status,
            preview_status=preview_status,
            extracted_text=extracted_text[:12000],
            description=description,
            gemma_supported=gemma_supported,
            metadata=metadata,
        )

    def ingest_path(self, task_id: str, source_path: str) -> AttachmentRecord:
        source = Path(source_path).expanduser().resolve()
        if not source.is_file() or source.is_symlink():
            raise AttachmentError("attachment path must be a regular file")
        if source.stat().st_size > self.max_bytes:
            raise AttachmentError(f"attachment is larger than {self.max_bytes} bytes")
        return self.ingest(task_id, source.name, mimetypes.guess_type(source.name)[0], source.read_bytes())

    def _secure_task_dir(self, task_id: str) -> Path:
        context = self.store.load_context(task_id)
        root = Path(context.workspace_root if context else os.getenv("OPENMANUS_WORKSPACE_ROOT", "./workspace")).expanduser().resolve()
        target = root / "attachments" / task_id
        target.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        os.chmod(target.parent, 0o700)
        os.chmod(target, 0o700)
        return target

    def _validate(self, filename: str, media_type: str, data: bytes) -> None:
        if not data:
            raise AttachmentError("attachment is empty")
        if len(data) > self.max_bytes:
            raise AttachmentError(f"attachment is larger than {self.max_bytes} bytes")
        if Path(filename).suffix.lower() in BLOCKED_EXTENSIONS:
            raise AttachmentError("executable attachments are not accepted")
        if not any(media_type == allowed or media_type.startswith(allowed) for allowed in ALLOWED_MIME_PREFIXES):
            raise AttachmentError(f"unsupported attachment type: {media_type}")
        magic = data[:16]
        if media_type == "application/pdf" and not magic.startswith(b"%PDF"):
            raise AttachmentError("file does not match its PDF type")
        if media_type == "image/png" and not magic.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AttachmentError("file does not match its PNG type")
        if media_type in {"image/jpeg", "image/jpg"} and not magic.startswith(b"\xff\xd8\xff"):
            raise AttachmentError("file does not match its JPEG type")
        if media_type == "image/gif" and not magic.startswith((b"GIF87a", b"GIF89a")):
            raise AttachmentError("file does not match its GIF type")

    def _scan(self, filename: str, media_type: str, data: bytes) -> str:
        """Run deterministic local safety checks; this is not an antivirus engine."""
        if b"\x00" in data[:4096] and media_type.startswith("text/"):
            raise AttachmentError("text attachment contains binary null bytes")
        if re.search(rb"(^|\n)#!\s*/", data[:4096]) and Path(filename).suffix.lower() in {".sh", ".py", ".rb", ".pl"}:
            raise AttachmentError("executable script attachments are not accepted")
        return "passed_local_safety_scan"

    def _preview(self, path: Path, media_type: str, data: bytes) -> tuple[str, str]:
        if media_type.startswith("text/") or media_type in {"application/json", "application/csv"}:
            return data.decode("utf-8", errors="replace")[:12000], "text_extracted"
        if media_type == "application/pdf":
            try:
                completed = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, timeout=15, check=False)
                if completed.returncode == 0:
                    return completed.stdout[:12000], "text_extracted"
            except (OSError, subprocess.SubprocessError):
                pass
            return "", "preview_unavailable"
        if media_type.startswith("image/"):
            dimensions = self._image_dimensions(media_type, data)
            return "", f"image_preview_available{dimensions}"
        return "", "preview_unavailable"

    @staticmethod
    def _image_dimensions(media_type: str, data: bytes) -> str:
        try:
            if media_type == "image/png" and len(data) >= 24:
                width = int.from_bytes(data[16:20], "big")
                height = int.from_bytes(data[20:24], "big")
                return f"_{width}x{height}"
            if media_type == "image/gif" and len(data) >= 10:
                width = int.from_bytes(data[6:8], "little")
                height = int.from_bytes(data[8:10], "little")
                return f"_{width}x{height}"
            if media_type in {"image/jpeg", "image/jpg"}:
                index = 2
                while index + 9 < len(data):
                    if data[index] != 0xFF:
                        index += 1
                        continue
                    marker = data[index + 1]
                    length = int.from_bytes(data[index + 2:index + 4], "big")
                    if marker in range(0xC0, 0xC4) and index + 8 < len(data):
                        height = int.from_bytes(data[index + 5:index + 7], "big")
                        width = int.from_bytes(data[index + 7:index + 9], "big")
                        return f"_{width}x{height}"
                    index += max(length + 2, 2)
        except (IndexError, ValueError):
            pass
        return ""

    @staticmethod
    def _description(filename: str, media_type: str, size: int, extracted_text: str, data: bytes) -> str:
        size_kb = max(1, round(size / 1024))
        if media_type.startswith("image/"):
            return f"Image attachment '{filename}', {media_type}, {size_kb} KB. Visual inspection is available to Gemma."
        if media_type == "application/pdf":
            excerpt = " ".join(extracted_text.split())[:500]
            return f"PDF attachment '{filename}', {size_kb} KB. Extracted text excerpt: {excerpt or 'no text extracted; visual inspection may be needed.'}"
        excerpt = " ".join(extracted_text.split())[:500]
        return f"Text attachment '{filename}', {media_type}, {size_kb} KB. Content excerpt: {excerpt}"

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = Path(filename or "attachment.bin").name
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
        return safe[:180] or "attachment.bin"

    @staticmethod
    def _detect_type(filename: str, media_type: str | None, data: bytes) -> str:
        guessed = (media_type or mimetypes.guess_type(filename)[0] or "application/octet-stream").split(";")[0].strip().lower()
        if guessed == "application/octet-stream":
            if data.startswith(b"%PDF"):
                return "application/pdf"
            if data.startswith(b"\x89PNG"):
                return "image/png"
            if data.startswith(b"\xff\xd8\xff"):
                return "image/jpeg"
            try:
                data.decode("utf-8")
                return "text/plain"
            except UnicodeDecodeError:
                pass
        return guessed
