import unittest
import monitor_annual_reports as monitor

class ReportDiscoveryTests(unittest.TestCase):
    def test_annual_pdf_in_quarterly_folder(self):
        self.assertTrue(monitor.is_report_link('Annual report and accounts', 'https://example.com/2024/q4/2024-lbg-annual-report.pdf', 2026))

    def test_quarterly_and_interim_documents_still_rejected(self):
        for label, name in [('Annual reports: Q4 results','q4-results.pdf'), ('Interim report','interim-2025.pdf')]:
            self.assertFalse(monitor.is_report_link(label, 'https://example.com/annual-report/'+name, 2026))

    def test_hash_does_not_change_document_type(self):
        self.assertTrue(monitor.is_report_link('Annual report 2025', 'https://example.com/annual-report-2025.pdf?hash=abcq4def', 2026))

    def test_generic_link_uses_document_title(self):
        reports=monitor.parse_report_links('https://example.com/reports', '<a href="/static-files/id" title="Annual Report 2025.pdf">Report</a>', 2026)
        self.assertEqual(len(reports),1)
        self.assertEqual(reports[0]['year'],2025)
        self.assertEqual(reports[0]['url'],'https://example.com/static-files/id')

    def test_report_year_precedes_cms_upload_timestamp(self):
        report=monitor.parse_report_links('https://example.com', '<a href="/annual-report-2023.2025-04-29.pdf">Annual report 2023</a>', 2026)[0]
        self.assertEqual(report['year'],2023)

    def test_combined_bat_report_with_underscore_year(self):
        rows=monitor.parse_report_links('https://example.com', '<a href="/BAT_Combined_Annual_and_Sustainability_Report_2024_Reduced.pdf">PDF</a>',2026)
        self.assertEqual(rows[0]['year'],2024)

    def test_pdf_viewer_resolves_to_download(self):
        rows=monitor.parse_report_links('https://example.com', '<a href="/pdf-viewer.aspx?src=%2Freports%2Fannual-report-2026.pdf">Annual Report 2026</a>',2026)
        self.assertEqual(rows[0]['url'],'https://example.com/reports/annual-report-2026.pdf')

if __name__=='__main__': unittest.main()
