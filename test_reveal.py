import base64
import unittest

from reveal import reveal

INNER = (
    '<div class="postTitle"><h2><a href="/abss/example-title/">'
    "Example Title - Author</a></h2></div>"
    '<div class="postContent"><p>Posted: 1 Jan 2024<br>File Size: 1.18 GBs</p></div>'
)


def hidden(inner):
    blob = base64.b64encode(inner.encode()).decode()
    return '<div class="post re-ab" style="display:none;">%s</div>' % blob


class RevealTests(unittest.TestCase):
    def test_decodes_hidden_post_and_keeps_wrapper(self):
        page = "<html><body>%s</body></html>" % hidden(INNER)
        out, count = reveal(page)
        self.assertEqual(count, 1)
        self.assertIn('class="post re-ab"', out)
        self.assertIn("Example Title - Author", out)
        self.assertIn('href="/abss/example-title/"', out)
        self.assertNotIn("display:none", out)
        self.assertNotIn(base64.b64encode(INNER.encode()).decode()[:24], out)

    def test_rewrites_absolute_site_urls(self):
        out, count = reveal('<a href="https://audiobookbay.lu/abss/example/">x</a>')
        self.assertEqual(count, 0)
        self.assertIn('href="/abss/example/"', out)
        self.assertNotIn("audiobookbay.lu", out)

    def test_leaves_other_hosts(self):
        html = '<img src="https://example.com/cover.jpg">'
        out, _count = reveal(html)
        self.assertIn("example.com", out)


if __name__ == "__main__":
    unittest.main()
