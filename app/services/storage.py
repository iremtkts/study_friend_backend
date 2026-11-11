import codecs
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from pypdf import PdfReader

from app.core.config import get_settings

CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class StoredUpload:
    stored_filename: str
    file_path: Path
    file_type: str
    file_size_bytes: int
    extracted_text: str | None


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


async def store_upload(upload: UploadFile) -> StoredUpload:
    original_filename = upload.filename or ""
    extension = Path(original_filename).suffix.lower()
    if extension not in {".pdf", ".txt"}:
        raise _unprocessable("Only PDF and TXT files are supported.")

    prefix = await upload.read(5)
    if extension == ".pdf":
        if not prefix.startswith(b"%PDF-"):
            raise _unprocessable("The uploaded file does not contain a valid PDF header.")
        file_type = "pdf"
        decoder = None
        text_parts: list[str] = []
    else:
        if prefix.startswith(b"%PDF-"):
            raise _unprocessable("The file content is PDF but its extension is TXT.")
        file_type = "txt"
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        text_parts = []
        if b"\x00" in prefix:
            raise _unprocessable("TXT files must not contain null bytes.")
        try:
            text_parts.append(decoder.decode(prefix, final=False))
        except UnicodeDecodeError as exc:
            raise _unprocessable("TXT files must be valid UTF-8.") from exc

    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(prefix) > max_bytes:
        raise HTTPException(status_code=413, detail="File exceeds the maximum upload size.")

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4()
    stored_filename = f"{file_id}{extension}"
    final_path = upload_dir / stored_filename
    temp_path = upload_dir / f".{file_id}.tmp"
    file_size = len(prefix)

    try:
        with temp_path.open("xb") as destination:
            if prefix:
                destination.write(prefix)
            while chunk := await upload.read(CHUNK_SIZE):
                file_size += len(chunk)
                if file_size > max_bytes:
                    raise HTTPException(status_code=413, detail="File exceeds the maximum upload size.")
                if decoder is not None:
                    if b"\x00" in chunk:
                        raise _unprocessable("TXT files must not contain null bytes.")
                    try:
                        text_parts.append(decoder.decode(chunk, final=False))
                    except UnicodeDecodeError as exc:
                        raise _unprocessable("TXT files must be valid UTF-8.") from exc
                destination.write(chunk)
            if decoder is not None:
                try:
                    text_parts.append(decoder.decode(b"", final=True))
                except UnicodeDecodeError as exc:
                    raise _unprocessable("TXT files must be valid UTF-8.") from exc
        temp_path.replace(final_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise

    stored_name = stored_filename
    if getattr(settings, "storage_backend", "local") == "cloudinary":
        secret = getattr(settings, "cloudinary_api_secret", None)
        if not getattr(settings, "cloudinary_cloud_name", "") or not getattr(settings, "cloudinary_api_key", "") or secret is None or not secret.get_secret_value():
            final_path.unlink(missing_ok=True)
            raise HTTPException(status_code=503, detail="Cloudinary storage is not configured.")
        try:
            import cloudinary
            from cloudinary.uploader import upload

            cloudinary.config(
                cloud_name=settings.cloudinary_cloud_name,
                api_key=settings.cloudinary_api_key,
                api_secret=settings.cloudinary_api_secret.get_secret_value(),
                secure=True,
            )
            result = upload(
                str(final_path),
                resource_type="raw",
                type="authenticated",
                public_id=f"study_friend/notes/{file_id}{extension}",
                overwrite=False,
            )
            stored_name = result["public_id"]
        except Exception as exc:
            final_path.unlink(missing_ok=True)
            raise HTTPException(status_code=503, detail="The file could not be stored in Cloudinary.") from exc

    return StoredUpload(
        stored_filename=stored_name,
        file_path=final_path,
        file_type=file_type,
        file_size_bytes=file_size,
        extracted_text="".join(text_parts) if decoder is not None else None,
    )


def extract_pdf_text(file_path: Path) -> str:
    try:
        reader = PdfReader(str(file_path), strict=True)
        if reader.is_encrypted:
            raise _unprocessable("Encrypted PDFs are not supported.")
        extracted_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except HTTPException:
        raise
    except Exception as exc:
        raise _unprocessable("The PDF could not be read.") from exc

    if not extracted_text.strip():
        raise _unprocessable("This PDF contains no extractable text; scanned PDFs are not supported.")
    return extracted_text


def delete_file(stored_filename: str) -> None:
    settings = get_settings()
    if stored_filename.startswith("study_friend/notes/"):
        secret = getattr(settings, "cloudinary_api_secret", None)
        if not getattr(settings, "cloudinary_cloud_name", "") or not getattr(settings, "cloudinary_api_key", "") or secret is None or not secret.get_secret_value():
            raise RuntimeError("Cloudinary storage is not configured.")
        import cloudinary
        from cloudinary.uploader import destroy

        cloudinary.config(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key,
            api_secret=secret.get_secret_value(),
            secure=True,
        )
        destroy(stored_filename, resource_type="raw", type="authenticated", invalidate=True)
        return
    if Path(stored_filename).name != stored_filename:
        raise ValueError("Invalid stored filename")
    path = Path(settings.upload_dir) / stored_filename
    path.unlink(missing_ok=True)


def cleanup_temporary_upload(file_path: Path) -> None:
    """Remove the local staging copy after a Cloudinary upload has been committed."""
    if getattr(get_settings(), "storage_backend", "local") == "cloudinary":
        file_path.unlink(missing_ok=True)
