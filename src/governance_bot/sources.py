"""Read-only Google Drive source discovery and content extraction."""
from dataclasses import asdict, dataclass
import hashlib
from io import BytesIO
import os
import re
import unicodedata

API = 'https://www.googleapis.com/drive/v3/files'
SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
FOLDER = 'application/vnd.google-apps.folder'
DOC = 'application/vnd.google-apps.document'
FIELDS = 'id,name,mimeType,webViewLink,modifiedTime,version,md5Checksum,parents,trashed,capabilities(canDownload)'
SUPPORTED = {DOC, 'application/pdf', 'text/plain', 'text/markdown', 'text/x-markdown'}


class SourceError(ValueError):
    """Discovery or fetching failed; callers must not infer source deletions."""


@dataclass(frozen=True)
class DocumentMetadata:
    source_id: str
    document_id: str
    title: str
    mime_type: str
    source_url: str
    last_updated: str | None
    source_version: str | None
    source_checksum: str | None


@dataclass(frozen=True)
class SourceDocument:
    metadata: DocumentMetadata
    text: str
    content_hash: str


def authenticated_session():
    """ADC supports service-account files and externally provisioned OAuth credentials."""
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
    except ImportError as exc:
        raise SourceError('Install Drive dependencies: python -m pip install -e ".[drive]"') from exc
    try:
        credentials, _ = google.auth.default(scopes=[SCOPE])
    except google.auth.exceptions.GoogleAuthError as exc:
        raise SourceError('Configure Google Application Default Credentials; see README Drive setup') from exc
    return AuthorizedSession(credentials)


class GoogleDriveSource:
    def __init__(self, folder_id, session=None):
        if not folder_id or not re.fullmatch(r'[A-Za-z0-9_-]+', folder_id):
            raise SourceError('Set GOOGLE_DRIVE_FOLDER_ID to a raw Drive folder ID')
        self.folder_id = folder_id
        self.source_id = f'gdrive:{folder_id}'
        self.session = session if session is not None else authenticated_session()

    @classmethod
    def from_environment(cls, folder_id=None):
        return cls(folder_id or os.getenv('GOOGLE_DRIVE_FOLDER_ID'))

    def _get(self, suffix='', **params):
        try:
            response = self.session.get(API + suffix, params=params, timeout=45)
        except Exception as exc:
            # Do not expose HTTP bodies, headers, or credentials in CLI errors.
            raise SourceError('Drive request failed; check credentials and network access') from exc
        if not 200 <= response.status_code < 300:
            raise SourceError(f'Drive request failed (HTTP {response.status_code}); no complete source snapshot available')
        return response

    def _json(self, suffix='', **params):
        response = self._get(suffix, **params)
        try:
            result = response.json()
        except (TypeError, ValueError) as exc:
            raise SourceError('Invalid Drive JSON response') from exc
        if not isinstance(result, dict):
            raise SourceError('Invalid Drive response structure')
        return result

    def _metadata(self, item):
        if not all(isinstance(item.get(k), str) and item[k] for k in ('id', 'name', 'mimeType')):
            raise SourceError('Drive file metadata is missing required fields')
        return DocumentMetadata(self.source_id, item['id'], item['name'], item['mimeType'],
                                item.get('webViewLink') or f"https://drive.google.com/file/d/{item['id']}/view",
                                item.get('modifiedTime'), item.get('version'), item.get('md5Checksum'))

    def list_documents(self):
        """Return a complete supported-file listing or raise; never return partial results."""
        folder = self._json('/' + self.folder_id, fields='id,mimeType,trashed', supportsAllDrives=True)
        if folder.get('mimeType') != FOLDER or folder.get('trashed'):
            raise SourceError('Configured source is not an accessible, active Drive folder')
        documents, seen, token = {}, set(), None
        while True:
            params = dict(q=f"'{self.folder_id}' in parents and trashed = false", pageSize=100,
                          fields=f'nextPageToken,incompleteSearch,files({FIELDS})',
                          supportsAllDrives=True, includeItemsFromAllDrives=True)
            if token:
                params['pageToken'] = token
            page = self._json(**params)
            if page.get('incompleteSearch') or not isinstance(page.get('files'), list):
                raise SourceError('Incomplete Drive listing; source snapshot rejected')
            for item in page['files']:
                if not isinstance(item, dict):
                    raise SourceError('Invalid file in Drive listing')
                if item.get('mimeType') in SUPPORTED:
                    metadata = self._metadata(item)
                    documents[metadata.document_id] = metadata
            token = page.get('nextPageToken')
            if not token:
                return sorted(documents.values(), key=lambda d: (d.title, d.document_id))
            if not isinstance(token, str) or token in seen:
                raise SourceError('Invalid or repeated Drive pagination token')
            seen.add(token)

    def fetch_document(self, document_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', document_id):
            raise SourceError('Invalid Drive document ID')
        suffix = '/' + document_id
        item = self._json(suffix, fields=FIELDS, supportsAllDrives=True)
        if item.get('trashed') or self.folder_id not in item.get('parents', []):
            raise SourceError('Document is outside the configured source folder')
        metadata = self._metadata(item)
        if metadata.mime_type not in SUPPORTED:
            raise SourceError('Unsupported Drive document type')
        if item.get('capabilities', {}).get('canDownload') is False:
            raise SourceError('Document download is not permitted')
        response = (self._get(suffix + '/export', mimeType='text/plain') if metadata.mime_type == DOC
                    else self._get(suffix, alt='media', supportsAllDrives=True))
        if metadata.mime_type == 'application/pdf':
            from pypdf import PdfReader
            try:
                pages = [p.extract_text() or '' for p in PdfReader(BytesIO(response.content)).pages]
            except Exception as exc:
                raise SourceError('Unable to extract PDF text') from exc
            if not any(p.strip() for p in pages):
                raise SourceError('PDF has no extractable text; OCR is not included')
            text = '\n'.join(f'# Page {i}\n{p}' for i, p in enumerate(pages, 1))
        else:
            try:
                text = response.content.decode('utf-8-sig')
            except UnicodeDecodeError as exc:
                raise SourceError('Document must contain UTF-8 text') from exc
        text = unicodedata.normalize('NFC', text.replace('\r\n', '\n').replace('\r', '\n')).strip()
        if not text:
            raise SourceError('Document has no extractable text')
        after = self._json(suffix, fields=FIELDS, supportsAllDrives=True)
        if (after.get('version'), after.get('modifiedTime'), after.get('parents'), after.get('trashed')) != (
                item.get('version'), item.get('modifiedTime'), item.get('parents'), item.get('trashed')):
            raise SourceError('Document changed during fetch; retry before indexing')
        return SourceDocument(metadata, text, hashlib.sha256(text.encode('utf-8')).hexdigest())


def source_preview(source, document_id=None):
    if document_id:
        return asdict(source.fetch_document(document_id))
    return {'source_id': source.source_id, 'complete': True,
            'documents': [asdict(d) for d in source.list_documents()]}
