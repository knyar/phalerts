#!/usr/bin/env python3

import unittest
import unittest.mock

import phalerts

class TestPhalerts(unittest.TestCase):
    def setUp(self):
        self.app = phalerts.app.test_client()

    def post(self, data, query=''):
        return self.app.post("/alerts", query_string=query, data=data,
                             content_type="application/json")

    def test_invalid_args(self):
        rv = self.post('{"version": 4}', "unknown_arg=foobar")
        assert "Unexpected args" in str(rv.data)

    def test_invalid_version(self):
        rv = self.post(data='{"version": 3}')
        assert "Unknown message version" in str(rv.data)

if __name__ == '__main__':
    unittest.main()
