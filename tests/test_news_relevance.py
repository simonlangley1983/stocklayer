import unittest
from datetime import date
from automation.news_sentiment import prepare_articles, day_bounds

class NewsRelevanceTests(unittest.TestCase):
    def prepare(self, rows):
        return prepare_articles(rows, {"aliases":["Example PLC"]}, *day_bounds(date(2026,9,14)))

    def article(self, title, url, domain="publisher.test"):
        return {"title":title,"url":url,"domain":domain,"publishedAt":"2026-09-14T12:00:00Z"}

    def test_search_result_without_company_is_rejected(self):
        accepted, rejected = self.prepare([self.article("Unrelated earnings rise", "https://publisher.test/one")])
        self.assertEqual(accepted, [])
        self.assertEqual(rejected["missing_company_alias"], 1)

    def test_duplicate_headline_with_different_tracking_url_is_counted_once(self):
        rows = [self.article("Example PLC wins contract", "https://news.google.com/rss/articles/"+key) for key in ("a","b")]
        accepted, rejected = self.prepare(rows)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected["duplicate_publisher_headline"], 1)

    def test_distinct_publishers_remain_available_for_story_clustering(self):
        accepted, _ = self.prepare([self.article("Example PLC wins contract", "https://one.test/a", "one.test"), self.article("Example PLC wins contract", "https://two.test/a", "two.test")])
        self.assertEqual(len(accepted), 2)
