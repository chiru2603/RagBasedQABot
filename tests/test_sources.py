import hashlib
from io import BytesIO
import unittest
from unittest.mock import Mock, patch

from governance_bot.cli import main
from governance_bot.sources import DOC, FOLDER, GoogleDriveSource, SourceError


def response(data=None, content=b'', status=200):
    result = Mock(status_code=status, content=content)
    result.json.return_value = data
    return result


def item(**changes):
    return dict(dict(id='doc1', name='policy.md', mimeType='text/markdown',
                     parents=['folder1'], version='1', modifiedTime='2026-10-30T00:00:00Z',
                     capabilities={'canDownload': True}), **changes)


class DriveSourceTests(unittest.TestCase):
    def source(self, *responses):
        session = Mock()
        session.get.side_effect = responses
        return GoogleDriveSource('folder1', session), session

    def test_listing_paginates_and_filters(self):
        source, session = self.source(
            response({'mimeType': FOLDER}),
            response({'files': [item(), item(id='subfolder', mimeType=FOLDER)], 'nextPageToken': 'next'}),
            response({'files': [item(id='doc2', mimeType=DOC)], 'incompleteSearch': False}))
        docs = source.list_documents()
        self.assertEqual([d.document_id for d in docs], ['doc1', 'doc2'])
        self.assertEqual(session.get.call_args.kwargs['params']['pageToken'], 'next')
        self.assertIn("'folder1' in parents", session.get.call_args.kwargs['params']['q'])

    def test_partial_and_failed_listings_raise(self):
        for last in (response({'files': [], 'incompleteSearch': True}),
                     response(status=403), response({})):
            source, _ = self.source(response({'mimeType': FOLDER}), last)
            with self.assertRaises(SourceError):
                source.list_documents()

    def test_repeated_token_rejected(self):
        page = response({'files': [], 'nextPageToken': 'same'})
        source, _ = self.source(response({'mimeType': FOLDER}), page, page)
        with self.assertRaisesRegex(SourceError, 'pagination'):
            source.list_documents()

    def test_empty_folder_is_complete_listing(self):
        source, _ = self.source(response({'mimeType': FOLDER}), response({'files': []}))
        self.assertEqual(source.list_documents(), [])

    def test_http_error_is_not_misreported_as_invalid_json(self):
        source, _ = self.source(response(status=403))
        with self.assertRaisesRegex(SourceError, 'HTTP 403'):
            source.list_documents()

    def test_fetch_normalizes_and_hashes(self):
        source, session = self.source(response(item()), response(content=b'# Policy\r\nAllowed.\r\n'), response(item()))
        doc = source.fetch_document('doc1')
        self.assertEqual(doc.text, '# Policy\nAllowed.')
        self.assertEqual(doc.content_hash, hashlib.sha256(doc.text.encode()).hexdigest())
        self.assertEqual(session.get.call_args_list[1].kwargs['params']['alt'], 'media')

    def test_google_docs_export(self):
        metadata = item(mimeType=DOC)
        source, session = self.source(response(metadata), response(content=b'Policy text'), response(metadata))
        self.assertEqual(source.fetch_document('doc1').text, 'Policy text')
        self.assertTrue(session.get.call_args_list[1].args[0].endswith('/doc1/export'))
        self.assertEqual(session.get.call_args_list[1].kwargs['params'], {'mimeType': 'text/plain'})

    def test_outside_folder_and_download_denied(self):
        for metadata in (item(parents=['other']), item(trashed=True),
                         item(capabilities={'canDownload': False})):
            source, session = self.source(response(metadata))
            with self.assertRaises(SourceError):
                source.fetch_document('doc1')
            self.assertEqual(session.get.call_count, 1)

    def test_changed_file_rejected(self):
        source, _ = self.source(response(item()), response(content=b'text'), response(item(version='2')))
        with self.assertRaisesRegex(SourceError, 'changed during fetch'):
            source.fetch_document('doc1')

    def test_pdf_without_text_rejected(self):
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        content = BytesIO()
        writer.write(content)
        source, _ = self.source(response(item(mimeType='application/pdf')), response(content=content.getvalue()))
        with self.assertRaisesRegex(SourceError, 'OCR'):
            source.fetch_document('doc1')

    def test_source_cli_does_not_initialize_openai(self):
        with patch('sys.argv', ['governance-bot', 'sources', 'list', '--folder-id', 'folder1']), \
                patch('governance_bot.sources.GoogleDriveSource.from_environment') as factory, \
                patch('governance_bot.cli.OpenAIBackend') as backend, patch('sys.stdout'):
            factory.return_value.source_id = 'gdrive:folder1'
            factory.return_value.list_documents.return_value = []
            self.assertEqual(main(), 0)
            backend.assert_not_called()


if __name__ == '__main__':
    unittest.main()
