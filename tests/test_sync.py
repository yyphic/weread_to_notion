import os
import unittest
from unittest.mock import MagicMock, patch

import requests
import weread


class AuthenticationTests(unittest.TestCase):
    def test_cookies_are_secure_and_domain_scoped(self):
        jar = weread.parse_cookie_string('wr_vid=123; wr_skey=sample')
        self.assertTrue(all(c.secure and c.domain == '.weread.qq.com' for c in jar))
        session = requests.Session()
        session.cookies = jar
        good = session.prepare_request(requests.Request('GET', 'https://i.weread.qq.com/user/notebooks'))
        bad = session.prepare_request(requests.Request('GET', 'https://example.com/'))
        plain = session.prepare_request(requests.Request('GET', 'http://i.weread.qq.com/'))
        self.assertIn('wr_skey=sample', good.headers['Cookie'])
        self.assertNotIn('Cookie', bad.headers)
        self.assertNotIn('Cookie', plain.headers)

    def test_empty_cookie_rejected(self):
        with self.assertRaises(weread.SyncError):
            weread.parse_cookie_string('')

    def test_wrong_host_never_called(self):
        with patch.object(requests.Session, 'request') as request:
            with self.assertRaises(weread.SyncError):
                weread.WereadSession().get('https://example.com/')
            request.assert_not_called()

    def test_auth_error_sanitized_and_no_redirect(self):
        response = MagicMock(status_code=401, text='secret-value')
        with patch.object(requests.Session, 'request', return_value=response) as request:
            with self.assertRaises(weread.SyncError) as error:
                weread.WereadSession().get(weread.WEREAD_NOTEBOOKS_URL)
            self.assertNotIn('secret-value', str(error.exception))
            self.assertEqual(request.call_args.kwargs['timeout'], (10, 30))
            self.assertFalse(request.call_args.kwargs['allow_redirects'])

    def test_business_error_stops_sync(self):
        response = MagicMock()
        response.json.return_value = {'errcode': -1, 'errmsg': 'secret-value'}
        with self.assertRaises(weread.SyncError) as error:
            weread.weread_json(response)
        self.assertNotIn('secret-value', str(error.exception))

    def test_missing_configuration_before_network(self):
        with patch.dict(os.environ, {}, clear=True), patch('sys.argv', ['weread.py']), patch.object(weread, 'Client') as client:
            with self.assertRaises(weread.SyncError):
                weread.main()
            client.assert_not_called()


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.env = {'WEREAD_COOKIE': 'wr_skey=sample', 'NOTION_TOKEN': 'test-token', 'NOTION_DATABASE_ID': 'test-db'}
        self.client = MagicMock()
        self.client.databases.retrieve.return_value = {'data_sources': [{'id': 'source'}]}
        self.book = {'sort': 1, 'book': {'bookId': 'book', 'title': 'Title', 'cover': 'https://example.com/cover', 'author': 'Author'}}

    def run_main(self, args, append_failure=False):
        with patch.dict(os.environ, self.env, clear=True), patch('sys.argv', ['weread.py'] + args), patch.object(weread, 'Client', return_value=self.client), patch.object(weread, 'WereadSession'), patch.object(weread, 'get_sort', return_value=0), patch.object(weread, 'get_notebooklist', return_value=[self.book]), patch.object(weread, 'check', return_value=['old']), patch.object(weread, 'get_chapter_info', return_value=None), patch.object(weread, 'get_bookmark_list', return_value=[]), patch.object(weread, 'get_review_list', return_value=([], [])), patch.object(weread, 'insert_to_notion', return_value='new') as create, patch.object(weread, 'add_children', side_effect=RuntimeError('failure') if append_failure else None, return_value=[]) as append:
            if append_failure:
                with self.assertRaises(RuntimeError):
                    weread.main()
            else:
                weread.main()
            return create, append

    def test_check_does_not_write(self):
        create, append = self.run_main(['--check'])
        create.assert_not_called()
        append.assert_not_called()
        self.client.pages.update.assert_not_called()

    def test_failed_replacement_preserves_old_page(self):
        self.run_main([], append_failure=True)
        self.client.pages.update.assert_called_once_with(page_id='new', archived=True)

    def test_success_archives_only_old_page(self):
        self.run_main([])
        self.client.pages.update.assert_called_once_with(page_id='old', archived=True)

    def test_exact_batch_size_and_empty_input(self):
        weread.client = self.client
        self.client.blocks.children.append.return_value = {'results': [{}] * 100}
        with patch.object(weread.time, 'sleep'):
            self.assertEqual(weread.add_children('id', []), [])
            self.client.blocks.children.append.assert_not_called()
            self.assertEqual(len(weread.add_children('id', [{}] * 100)), 100)
            self.client.blocks.children.append.assert_called_once()

    def test_previous_pages_paginate_without_mutation(self):
        weread.client = self.client
        weread.data_source_id = 'source'
        self.client.data_sources.query.side_effect = [
            {'results': [{'id': 'first'}], 'has_more': True, 'next_cursor': 'cursor'},
            {'results': [{'id': 'second'}], 'has_more': False},
        ]
        with patch.object(weread.time, 'sleep'):
            self.assertEqual(weread.check('book'), ['first', 'second'])
        self.assertEqual(self.client.data_sources.query.call_args.kwargs['start_cursor'], 'cursor')
        self.client.pages.update.assert_not_called()


if __name__ == '__main__':
    unittest.main()
