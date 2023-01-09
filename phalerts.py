#!/usr/bin/env python3
"""Creates and updates Phabricator / Maniphest tasks for Prometheus alerts.

phalerts implements Prometheus alertmanager webhook API and creates/updates
Phabricator tasks for alerts. Inspired by https://github.com/fabxc/jiralerts

https://github.com/knyar/phalerts
Licensed under MIT license.

Copyright 2017 Contributors to the phalerts project.
"""

import argparse
import logging
import os
import sys

from flask import Flask, request, make_response
from jinja2 import Template, TemplateError
from phabricator import Phabricator
import prometheus_client as prometheus

app = Flask(__name__)
args = None
phab = None

# Jinja2 template used for task description.
TPL_DESCRIPTION = r"""
== Common information

{% for k, v in commonAnnotations|dictsort -%}
* **{{ k }}**: {{ v }}
{% endfor %}
{% for k, v in commonLabels|dictsort -%}
* **{{ k }}**: {{ v }}
{% endfor %}

== Firing alerts
{% for a in alerts if a.status == 'firing' -%}

---

  {% for k, v in a.annotations|dictsort -%}
* **{{ k }}**: {{ v }}
  {% endfor %}
  {% for k, v in a.labels|dictsort -%}
* **{{ k }}**: {{ v }}
  {% endfor -%}
* [Source]({{ a.generatorURL }})

{% endfor %}
"""

metric_request_latency = prometheus.Histogram(  # pylint: disable=no-value-for-parameter
    "phalerts_request_latency_seconds", "Latency of incoming requests")
metric_error_count = prometheus.Counter(  # pylint: disable=no-value-for-parameter
    "phalerts_request_errors_total", "Number of request processing errors")
metric_phabricator_latency = prometheus.Histogram(  # pylint: disable=no-value-for-parameter
    "phalerts_phabricator_latency_seconds",
    "Latency of outgoing Phabricator requests", ["api_call"])

class Error(RuntimeError):
    pass

def phab_request(api_func, **kwargs):
    """Sends a Phabricator API request, measuring latency and logging result."""
    api_call = "%s.%s" % (api_func.method, api_func.endpoint)
    with metric_phabricator_latency.labels(api_call).time():  # pylint: disable=no-member
        result = api_func(**kwargs)
        logging.debug("Got %s response: %s", api_call, result)
        return result

def find_project_phid(name):
    """Looks for a project with a given name in Phabricator.

    Returns phid of a single matched project, or raises an Error if project has
    not been found.

    Args:
        name: (string) name of a project.
    """
    result = phab_request(phab.project.search, constraints=dict(name=name))
    if result["cursor"]["after"]:
        raise Error("Unexpected 'after' cursor while searching for project %s" %
                    name)
    for project in result["data"]:
        # For the list of fields, see:
        # https://secure.phabricator.com/conduit/method/project.search/
        if project["fields"]["name"] == name:
            return project["phid"]
    raise Error("Could not find project %s" % name)

def create_task(title, description, phids):
    """Creates a Maniphest task.

    Tasks are created by using `maniphest.edit` API endpoint without an
    objectIdentifier. For more details, see:
    https://secure.phabricator.com/conduit/method/maniphest.edit/

    Args:
        title: (string) task title.
        description: (string) task description.
        phids: (list of strings) project IDs that a new task should be
            assigned to.
    """
    transactions = [
        dict(type="title", value=title),
        dict(type="description", value=description),
    ]
    if phids:
        transactions.append(dict(type="projects.add", value=phids))
    result = phab_request(phab.maniphest.edit, transactions=transactions)
    if not result.get("object"):
        raise Error("Failed to create task. Got response: %s" % result)
    logging.info("Created task %s/T%s", args.phabricator_url.rstrip("/"),
                 result["object"]["id"])

def update_task(task, description):
    """Updates description of an existing Maniphest task.

    Args:
        task: a nested dictionary corresponding to an existing Maniphest task
            (as returned by `find_task`).
        description: (string) new task description.
    """
    transactions = [dict(type="description", value=description)]
    result = phab_request(
        phab.maniphest.edit,
        transactions=transactions,
        objectIdentifier=task["phid"])
    if len(result["transactions"]) < len(transactions):
        raise Error("Failed to apply all transactions for task %s. Got %s" % (
            task["phid"], result))

def find_task(title, phids):
    """Looks for an open task with a given title in given project IDs.

    Returns a single matched task. For the list of fields, see:
    https://secure.phabricator.com/conduit/method/maniphest.search/

    Args:
        title: (string) task title.
        phids: (list of strings) project IDs that should be assigned to
            a task to be returned. Can be empty.
    """
    result = phab_request(
        phab.maniphest.search,
        constraints=dict(
            query='title:"%s"' % title,
            statuses=["open"],
            projects=phids,
        ),
        attachments=dict(projects=True),
        # this expands to "title, id", so we'll get the newest open task if
        # multiple open tasks exist.
        order="title",
    )
    if result["cursor"]["after"]:
        raise Error("Unexpected 'after' cursor while searching for task %s" %
                    title)
    for task in result["data"]:
        # 'query' constraint actually does a full text search, so we iterate
        # over returned tasks looking for a one with the title we need.
        if task["fields"]["name"] != title:
            continue
        task_projects = set(task["attachments"]["projects"]["projectPHIDs"])
        # Check that the task has all required projects assigned to it.
        if not set(phids).issubset(task_projects):
            continue

        return task

def process_task(title, description, projects, phids):
    """Makes sure there is a task with a given title and description.

    Either creates or updates an existing task with a given description. Note,
    that only tasks that belong to a given set of projects or PHIDs will be
    considered.

    Args:
        title: (string) task title.
        description: (string) task description.
        projects: (list of strings) names of projects that a task should be
            assigned to.
        phids: (list of strings) projects IDs that a task should be
            assigned to.
    """
    for project in projects:
        phid = find_project_phid(project)
        logging.info("Project %s has PHID %s", project, phid)
        phids.append(phid)

    logging.info("Looking for tasks with title='%s' in %s", title, phids)

    task = find_task(title, phids)

    if not task:
        logging.info("Creating a task with title='%s' in %s", title, phids)
        create_task(title, description, phids)
    elif task["fields"]["description"]["raw"] == description:
        logging.info("Task %s/T%s already exists with correct description",
                     args.phabricator_url.rstrip("/"), task["id"])
    else:
        logging.info("Updating task %s/T%s",
                     args.phabricator_url.rstrip("/"), task["id"])
        update_task(task, description)

@app.route("/alerts", methods=["POST"])
@metric_request_latency.time()
def alerts():
    """Processes a POST request from Alertmanager."""
    unknown_args = set(request.args.keys()) - {"project", "phid", "title"}
    if unknown_args:
        logging.error("Unexpected args %s", unknown_args)
        return "Unexpected args %s" % unknown_args, 400

    data = request.get_json()
    if data["version"] != "4":
        logging.error("Unknown message version %s", data["version"])
        return "Unknown message version %s" % data["version"], 400

    logging.debug("Got data: %s", data)

    # Sort list of alerts by label values to make sure that task description
    # stays the same when Alertmanager sends the same list of alerts in a
    # different order.
    if "alerts" in data:
        data["alerts"] = sorted(data["alerts"],
                                key=lambda a: sorted(a["labels"].values()))

    tpl = args.tpl_title
    if "title" in request.args:
        tpl = request.args["title"]

    try:
        title = Template(tpl).render(data)
    except TemplateError as e:
        logging.error("Unable to format title %s", e)
        return "Unable to format title template %s" % e, 400
    description = Template(TPL_DESCRIPTION).render(data)

    try:
        process_task(title, description, request.args.getlist("project"),
                     request.args.getlist("phid"))
    except Error as e:
        logging.error("Got error: %s", e)
        metric_error_count.inc()
        return "Error", 500

    return "OK", 200

@app.route("/metrics")
def metrics():
    resp = make_response(prometheus.generate_latest(prometheus.REGISTRY))
    resp.headers["Content-Type"] = prometheus.CONTENT_TYPE_LATEST
    return resp, 200

def main():
    global args  # pylint: disable=global-statement
    global phab  # pylint: disable=global-statement
    parser = argparse.ArgumentParser(
        description="Creates and updates Maniphest tasks for alerts",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "-p", "--port", type=int, default=8292, help="Port to bind to")
    parser.add_argument(
        "-b", "--bind", default="localhost", help="Host to bind to")
    parser.add_argument(
        "-t", "--tpl_title", default="{{ groupLabels.alertname }}",
        help="Jinja2 template used for task title. Will be overridden "
             "by 'title' in query string")
    parser.add_argument(
        "-d", "--debug", action="store_const", dest="loglevel",
        const=logging.DEBUG, default=logging.INFO, help="Enable debug logging")
    parser.add_argument(
        "phabricator_url",
        help="Base URL of Phabricator, e.g. https://phabricator.company.tld")
    parser.add_argument(
        "phabricator_user", help=(
            "Phabricator username. Authentication token should be provided via "
            "PHABRICATOR_TOKEN environment variable"))
    args = parser.parse_args()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(message)s", level=args.loglevel)

    token = os.environ.get("PHABRICATOR_TOKEN")
    if not token:
        print("PHABRICATOR_TOKEN not set")
        sys.exit(1)

    phab = Phabricator(host="%s/api/" % args.phabricator_url.rstrip("/"),
                       username=args.phabricator_user, token=token)
    app.run(host=args.bind, port=args.port)

if __name__ == "__main__":
    main()
