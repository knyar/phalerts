#!/usr/bin/env python3

# https://github.com/knyar/phalerts
# Licensed under MIT license.
# Copyright 2017 Contributors to the phalerts project.

import copy
import unittest
import unittest.mock

import parameterized
import phalerts

MSG_ONE_ALERT = """{
    "groupLabels": {"alertname": "SomethingIsBroken"},
    "commonLabels": {"job": "foo", "alertname": "SomethingIsBroken"},
    "commonAnnotations": {"text": "Hey, something is broken"},
    "externalURL": "http://alertmanager.local",
    "receiver": "phalerts",
    "version": "4",
    "status": "firing",
    "alerts": [{
        "labels": {"job": "foo", "alertname": "SomethingIsBroken"},
        "status": "firing",
        "annotations": {"text": "Hey, something is broken"},
        "generatorURL": "http://prometheus.local/graph?...",
        "startsAt": "2017-01-01T00:00:00.000Z",
        "endsAt": "0001-01-01T00:00:00Z"
    }]
}"""

PROJECT_SEARCH_RESULT = {
    "data": [
        {"id": 11, "phid": "PHID-11", "type": "PROJ", "attachments": {},
         "fields": {
             "name": "foobar-extra",
             "slug": "foobar-extra",
             "description": "foobar-extra project",
             "parent": None,
             "policy": {"view": "users", "edit": "PHID-xx", "join": "users"},
             "depth": 0,
             "icon": {
                 "key": "project", "icon": "fa-briefcase", "name": "Project"},
             "dateModified": 1506189207,
             "milestone": None,
             "color": {"key": "red", "name": "Red"},
             "dateCreated": 1506189207,
         }},
        {"id": 12, "phid": "PHID-12", "type": "PROJ", "attachments": {},
         "fields": {
             "name": "foobar",
             "slug": "foobar",
             "description": "foobar correct project",
             "parent": None,
             "policy": {"view": "admin", "edit": "admin", "join": "no-one"},
             "depth": 0,
             "icon": {"key": "group", "icon": "fa-users", "name": "Group"},
             "dateModified": 1506189207,
             "milestone": None,
             "color": {"key": "blue", "name": "Blue"},
             "dateCreated": 1506189207,
         }},
    ],
    "maps": {" slugMap": {}},
    "query": {"queryKey": None},
    "cursor": {"before": None, "after": None, "order": None, "limit": 100}}

TASK_SEARCH_RESULT = {
    "data": [
        {"id": 21, "phid": "PHID-21", "type": "TASK",
         "attachments": {"projects": {"projectPHIDs": ["PHID-11", "PHID-12"]}},
         "fields": {
             "name": "title SomethingIsBroken some other text",
             "policy": {"view": "users", "interact": "users", "edit": "users"},
             "ownerPHID": "PHID-USER-xxx",
             "dateModified": 1507208273,
             "description": {"raw": "task description is here"},
             "subtype": "default",
             "authorPHID": "PHID-USER-xxx",
             "dateCreated": 1507207778,
             "points": None,
             "priority": {"name": "High", "value": 80, "subpriority": 0,
                          "color": "red"},
             "status": {"name": "Open", "value": "open", "color": None},
             "spacePHID": "PHID-SPCE-buk45qxjzvj55bz4ihno",
         }},
        {"id": 22, "phid": "PHID-22", "type": "TASK",
         "attachments": {"projects": {"projectPHIDs": ["PHID-12"]}},
         "fields": {
             "name": "title SomethingIsBroken",
             "policy": {"view": "users", "interact": "users", "edit": "users"},
             "ownerPHID": "PHID-USER-xxx",
             "dateModified": 1507208275,
             "description": {"raw": "desc SomethingIsBroken"},
             "subtype": "default",
             "authorPHID": "PHID-USER-xxx",
             "dateCreated": 1507201234,
             "points": None,
             "priority": {"name": "High", "value": 80, "subpriority": 0,
                          "color": "red"},
             "status": {"name": "Open", "value": "open", "color": None},
             "spacePHID": "PHID-SPCE-buk45qxjzvj55bz4ihno",
         }},
    ],
    "maps": {},
    "query": {"queryKey": None},
    "cursor": {"before": None, "after": None, "order": "title", "limit": 100}}

TASK_EDIT_RESULT = {
    'object': {'phid': 'PHID-TASK-xxx', 'id': 1234},
    'transactions': [
        {'phid': 'PHID-XACT-TASK-xxx1'}, {'phid': 'PHID-XACT-TASK-xxx2'},
        {'phid': 'PHID-XACT-TASK-xxx3'}, {'phid': 'PHID-XACT-TASK-xxx4'}]}

class TestPhalerts(unittest.TestCase):
    def setUp(self):
        self.app = phalerts.app.test_client()
        args_patcher = unittest.mock.patch("phalerts.args")
        self.args = args_patcher.start()
        self.addCleanup(args_patcher.stop)
        self.args.tpl_title = "title {{ groupLabels.alertname }}"
        phab_patcher = unittest.mock.patch("phalerts.phab")
        self.phab = phab_patcher.start()
        self.addCleanup(phab_patcher.stop)
        self.phab.project.search.return_value = PROJECT_SEARCH_RESULT
        self.phab.maniphest.search.return_value = TASK_SEARCH_RESULT
        self.phab.maniphest.edit.return_value = TASK_EDIT_RESULT

    def post(self, data, query=""):
        return self.app.post("/alerts", query_string=query, data=data,
                             content_type="application/json")

    def test_invalid_args(self):
        rv = self.post('{"version": 4}', "unknown_arg=foobar")
        self.assertIn("Unexpected args", str(rv.data))

    def test_invalid_version(self):
        rv = self.post(data='{"version": 3}')
        self.assertIn("Unknown message version", str(rv.data))

    @unittest.mock.patch("phalerts.TPL_DESCRIPTION",
                         "desc {{ groupLabels.alertname }}")
    @unittest.mock.patch("phalerts.process_task")
    def test_process_task_called(self, process_task):
        # no projects
        self.post(MSG_ONE_ALERT)
        process_task.assert_called_with(
            "title SomethingIsBroken", "desc SomethingIsBroken", [], [])
        # a single project
        self.post(MSG_ONE_ALERT, "project=foobar")
        process_task.assert_called_with(
            "title SomethingIsBroken", "desc SomethingIsBroken", ["foobar"], [])
        # multiple projects
        self.post(MSG_ONE_ALERT, "project=foo&project=bar")
        process_task.assert_called_with(
            "title SomethingIsBroken", "desc SomethingIsBroken", ["foo", "bar"], [])
        # a single phid
        self.post(MSG_ONE_ALERT, "phid=foobar")
        process_task.assert_called_with(
            "title SomethingIsBroken", "desc SomethingIsBroken", [], ["foobar"])
        # multiple phids
        self.post(MSG_ONE_ALERT, "phid=foo&phid=bar")
        process_task.assert_called_with(
            "title SomethingIsBroken", "desc SomethingIsBroken", [], ["foo", "bar"])
        # mix phid/project
        self.post(MSG_ONE_ALERT, "project=foo&phid=bar")
        process_task.assert_called_with(
            "title SomethingIsBroken", "desc SomethingIsBroken", ["foo"], ["bar"])
        # string title with no template
        self.post(MSG_ONE_ALERT, "title=notemplate")
        process_task.assert_called_with(
            "notemplate", "desc SomethingIsBroken", [], [])
        # templated title
        self.post(MSG_ONE_ALERT, "title=status {{ alerts[0].status }}")
        process_task.assert_called_with(
            "status firing", "desc SomethingIsBroken", [], [])

    def test_nonexistent_project(self):
        result = copy.deepcopy(PROJECT_SEARCH_RESULT)
        result["data"] = []
        self.phab.project.search.return_value = result
        with self.assertRaises(phalerts.Error) as cm:
            phalerts.process_task("title", "desc", ["foobar"], [])
        self.assertIn("Could not find project foobar", str(cm.exception))

    @parameterized.parameterized.expand([
        # Task already exists, and has correct project assigned.
        ("title SomethingIsBroken", "desc SomethingIsBroken", ["foobar"], []),
        # Task already exists, and has two projects assigned (including the
        # expected one).
        ("title SomethingIsBroken some other text", "task description is here",
         ["foobar"], []),
    ])
    def test_task_exists_and_not_changed(self, title, description, projects,
                                         phids):
        phalerts.process_task(title, description, projects, phids)
        self.phab.project.search.assert_called_once()
        self.phab.maniphest.search.assert_called_once()
        # No tasks should have been created or edited.
        self.phab.maniphest.edit.assert_not_called()

    @parameterized.parameterized.expand([
        ("title SomethingIsBroken", "new description", ["foobar"], []),
        ("title SomethingIsBroken some other text", "new description",
         ["foobar", "foobar-extra"], []),
    ])
    def test_task_exists_and_changed(self, title, description, projects, phids):
        """Test that a task will be changed if description is different."""
        phalerts.process_task(title, description, projects, phids)
        self.phab.project.search.assert_called()
        self.phab.maniphest.search.assert_called_once()
        # Ensure that the task has been edited (rather than created, which uses
        # the same maniphest.edit API endpoint).
        self.phab.maniphest.edit.assert_called_once()
        self.assertIn(
            "objectIdentifier", self.phab.maniphest.edit.call_args[1].keys())

    @parameterized.parameterized.expand([
        # No task with such title.
        ("title SomethingElseIsBroken", "desc SomethingIsBroken", ["foobar"], []),
        # Project is different.
        ("title SomethingIsBroken", "desc SomethingIsBroken", ["foobar-extra"], []),
    ])
    def test_task_created(self, title, description, projects, phids):
        """Test that a new task is created."""
        phalerts.process_task(title, description, projects, phids)
        self.phab.project.search.assert_called()
        self.phab.maniphest.search.assert_called_once()
        # Ensure that the task is created.
        self.phab.maniphest.edit.assert_called_once()
        self.assertNotIn(
            "objectIdentifier", self.phab.maniphest.edit.call_args[1].keys())

    def test_task_creation_failed(self):
        self.phab.maniphest.edit.return_value = {}
        with self.assertRaises(phalerts.Error):
            phalerts.process_task("new title", "new description", [], [])


if __name__ == "__main__":
    unittest.main()
