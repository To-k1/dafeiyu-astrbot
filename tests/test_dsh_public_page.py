import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.resolver import ThreadedResolver

from deploy.dsh_bridge.public_page import PageText, PublicResolver, page_url, save_public_page
from deploy.dsh_bridge.public_search import search_evidence, search_server


class PublicPageTests(unittest.IsolatedAsyncioTestCase):
    def test_html_retains_headings_and_document_text(self):
        reader = PageText()
        reader.feed('<title>Docs</title><body><nav>Menu</nav><main><h1>Encoding</h1>'
                    '<article><p>See the <b>examples</b>.</p><pre>  return data</pre>'
                    '<script>ignore()</script><footer>bottom links</footer></article></main></body>')
        title, text = reader.result()
        self.assertEqual(title, 'Docs')
        self.assertIn('# Encoding', text)
        self.assertIn('See the examples.', text)
        self.assertIn('  return data', text)
        self.assertNotIn('Menu', text)
        self.assertNotIn('ignore()', text)
        self.assertNotIn('bottom links', text)

    async def test_local_targets_are_rejected_at_validation_and_dns_lookup(self):
        for url in ('https://127.0.0.1/', 'http://[::ffff:127.0.0.1]/', 'http://169.254.169.254/latest',
                    'https://alice:pw@example.org', 'file:///etc/passwd', 'https://example.org:81/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                page_url(url)
        resolver = PublicResolver()
        try:
            records = [{'host': '8.8.8.8'}, {'host': '192.168.1.1'}]
            with patch.object(ThreadedResolver, 'resolve', AsyncMock(return_value=records)):
                with self.assertRaises(OSError):
                    await resolver.resolve('example.org')
        finally:
            await resolver.close()

    async def test_page_tool_archives_once_and_records_page_evidence(self):
        url = 'https://example.org/original'
        final = 'https://example.org/article'
        body = '这是正文。' * 2500 + '\nLAST LINE'
        page = {'kind': 'page_text', 'requested_url': url, 'url': final,
                'title': 'Article', 'text': body, 'fetched_at': '2026-11-18T00:00:00+00:00'}
        snippet = [{'url': url, 'title': 'Excerpt', 'snippet': 'not the article'}]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            evidence = root / 'evidence.jsonl'
            server = search_server(evidence, lambda content: save_public_page(root, content),
                                   search=AsyncMock(return_value=snippet))
            with patch('deploy.dsh_bridge.public_search.fetch_public_page', AsyncMock(return_value=page)) as fetch:
                await server._tool_manager.call_tool('search_public_web', {'query': 'example'})
                invalid = await server._tool_manager.call_tool('read_public_page', {'url': url, 'offset': 1})
                self.assertEqual(invalid['status'], 'unavailable')
                result = await server._tool_manager.call_tool('read_public_page', {'url': url})
                chunks = [result['text']]
                while result['next_offset'] is not None:
                    result = await server._tool_manager.call_tool('read_public_page', {
                        'url': url, 'offset': result['next_offset']})
                    chunks.append(result['text'])
                fetch.assert_awaited_once_with(url)
            self.assertEqual(''.join(chunks), body)
            artifact = (root / result['artifact'].removeprefix('shared/')).read_text(encoding='utf-8')
            self.assertIn('来源：' + final, artifact)
            self.assertTrue(artifact.endswith(body + '\n'))
            [source] = search_evidence(evidence)
            self.assertEqual(source['artifact'], result['artifact'])
            self.assertEqual((source['kind'], source['url']), ('page_text', final))
            self.assertNotIn('text', source)
